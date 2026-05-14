"""
Pipeline orchestrator — runs Phase 1 → 2 → 3 with login check and incremental detection.

Usage:
    python scripts/run_pipeline.py --phase 1 --dry-run     # dry-run Phase 1
    python scripts/run_pipeline.py --phase 1               # run Phase 1
    python scripts/run_pipeline.py --phase 2               # run Phase 2
    python scripts/run_pipeline.py --phase 3               # run Phase 3
    python scripts/run_pipeline.py --all                    # run all three
    python scripts/run_pipeline.py --all --dry-run        # dry-run all
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import os
import subprocess
import time as _time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from scripts.db import (
    init_db,
    get_all_scrapes,
    get_all_prompts,
    get_tweets_for_scrape,
    get_categories,
    _conn,
)

# Path to .env
ENV_PATH = PROJECT_ROOT / ".env"


def _read_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _write_env(patch: dict):
    existing = _read_env()
    existing.update(patch)
    lines = [f"{k}={v}" for k, v in sorted(existing.items())]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_credentials() -> bool:
    """
    Check and bootstrap missing credentials before running pipeline.
    Returns True if all credentials are ready, False if user bailed.
    """
    print("\n[Credential Check]")

    # ── 1. API Key ────────────────────────────────────────────────────────────
    env = _read_env()
    api_key = env.get("OPENAI_API_KEY", "").strip()
    placeholder_keys = ("", "your-api-key-here", "sk-...xx")
    if not api_key or api_key in placeholder_keys:
        print("  OPENAI_API_KEY is missing or is a placeholder.")
        try:
            new_key = input("  Paste your MiniMax API key: ").strip()
        except EOFError:
            print("  Aborted.")
            return False
        if new_key and new_key not in placeholder_keys:
            _write_env({"OPENAI_API_KEY": new_key})
            print("  [OK] API key saved to .env")
            # Reload so subsequent code picks it up
            from dotenv import load_dotenv
            load_dotenv(override=True)
            api_key = new_key
        else:
            print("  Invalid key — cannot continue without API key.")
            return False

    # ── 2. X Cookies ──────────────────────────────────────────────────────────
    cookies_path = PROJECT_ROOT / "outputs" / "cookies.json"
    if not cookies_path.exists():
        print("  cookies.json not found — X authentication required.")
        return _ensure_x_cookies()
    import time
    age_days = (time.time() - cookies_path.stat().st_mtime) / 86400
    if age_days > 7:
        print(f"  cookies.json is {age_days:.1f} days old — re-authentication required.")
        return _ensure_x_cookies()

    print("  [OK] All credentials OK")
    return True


def _ensure_x_cookies() -> bool:
    """Interactive: get X username/password and run browser_auth.py."""
    try:
        username = input("  X username (email or phone): ").strip()
        password = input("  X password: ").strip()
    except EOFError:
        print("  Aborted.")
        return False
    if not username or not password:
        print("  Username and password required.")
        return False

    print(f"  Launching browser to authenticate @{username}...")
    script = PROJECT_ROOT / "scripts" / "browser_auth.py"
    result = subprocess.run(
        [sys.executable, str(script), "--username", username, "--password", password],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    if result.returncode == 0:
        print("  [OK] X authentication successful")
        return True
    else:
        print(f"  [FAIL] X authentication failed (exit {result.returncode})")
        if result.stdout:
            print(result.stdout[:500])
        return False


# ─── Login status ─────────────────────────────────────────────────────────────

def check_login_status() -> tuple[bool, str]:
    """
    Check if X cookies are available and fresh enough.
    Returns (is_logged_in, reason).
    """
    cookies_path = PROJECT_ROOT / "outputs" / "cookies.json"
    if not cookies_path.exists():
        return False, f"cookies.json not found"

    import time
    age_days = (time.time() - cookies_path.stat().st_mtime) / 86400
    if age_days > 7:
        return False, f"cookies.json is {age_days:.1f} days old (threshold 7 days)"

    return True, f"cookies.json fresh ({age_days:.1f} days old)"


# ─── DB counters ─────────────────────────────────────────────────────────────

def count_tweets_in_db() -> int:
    row = _conn().execute("SELECT COUNT(*) FROM tweets").fetchone()
    return row[0] if row else 0


def count_prompts_in_db() -> int:
    row = _conn().execute("SELECT COUNT(*) FROM prompts").fetchone()
    return row[0] if row else 0


def count_images_in_db() -> int:
    row = _conn().execute(
        "SELECT COUNT(*) FROM prompts WHERE image_gcs_url IS NOT NULL"
    ).fetchone()
    return row[0] if row else 0


def get_latest_scrape_id() -> int | None:
    row = _conn().execute(
        "SELECT id FROM scrapes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def get_new_prompt_ids() -> list[int]:
    """Return prompt_ids that exist without an image_gcs_url."""
    rows = _conn().execute(
        "SELECT id FROM prompts WHERE image_gcs_url IS NULL"
    ).fetchall()
    return [r[0] for r in rows]


# ─── Phase execution helpers ──────────────────────────────────────────────────

def run_x_multi_search() -> tuple[int, int]:
    """Run Phase 1: x_multi_search.py. Returns (exit_code, tweet_count)."""
    script = PROJECT_ROOT / "scripts" / "x_multi_search.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    return result.returncode, count_tweets_in_db()


def run_extract_prompts(scrape_id: int = None) -> tuple[int, int]:
    """Run Phase 2: extract_prompts.py. Returns (exit_code, prompt_count)."""
    script = PROJECT_ROOT / "scripts" / "extract_prompts.py"
    cmd = [sys.executable, str(script)]
    if scrape_id is not None:
        cmd.extend(["--scrape-id", str(scrape_id)])
    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    return result.returncode, count_prompts_in_db()


def run_expand_queries(scrape_id: int = None) -> tuple[int, list]:
    """Run Phase 1.5: expand_queries.py. Returns (exit_code, new_queries)."""
    script = PROJECT_ROOT / "scripts" / "expand_queries.py"
    cmd = [sys.executable, str(script), "--write"]
    if scrape_id is not None:
        cmd.extend(["--scrape-id", str(scrape_id)])
    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    new_queries = []
    if result.returncode == 0:
        # Parse stdout for new queries (expand_queries.py prints them)
        for line in result.stdout.splitlines():
            if line.startswith("  + "):
                new_queries.append(line[4:].strip())
    return result.returncode, new_queries


def run_generate_images(scrape_id: int = None) -> tuple[int, int]:
    """Run Phase 3: generate_images.py. Returns (exit_code, image_count)."""
    script = PROJECT_ROOT / "scripts" / "generate_images.py"
    cmd = [sys.executable, str(script)]
    if scrape_id is not None:
        cmd.extend(["--scrape-id", str(scrape_id)])
    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    return result.returncode, count_images_in_db()


# ─── Print helpers ──────────────────────────────────────────────────────────

def print_status():
    init_db()
    tweets = count_tweets_in_db()
    prompts = count_prompts_in_db()
    images = count_images_in_db()
    logged_in, reason = check_login_status()

    print("=" * 60)
    print("Pipeline Status")
    print("=" * 60)
    print(f"  X Login:     {'OK' if logged_in else 'FAIL'} ({reason})")
    print(f"  Tweets in DB:  {tweets}")
    print(f"  Prompts:       {prompts}")
    print(f"  Images:        {images}")
    print("=" * 60)


def dry_run_report(phase: str):
    init_db()
    logged_in, _ = check_login_status()

    if phase in ("1", "all"):
        new_ids = get_new_prompt_ids()
        print(f"\n[Phase 1] Dry-run:")
        print(f"   New tweets to scrape: (uses INSERT OR IGNORE — safe to re-run)")
        if not logged_in:
            print(f"   WARNING: not logged in")

    if phase in ("2", "all"):
        new_ids = get_new_prompt_ids()
        print(f"\n[Phase 2] Dry-run:")
        print(f"   New prompts to extract: {len(new_ids)}")

    if phase in ("3", "all"):
        new_ids = get_new_prompt_ids()
        print(f"\n[Phase 3] Dry-run:")
        print(f"   New images to generate: {len(new_ids)}")


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Pipeline orchestrator — Phase 1→2→3")
    parser.add_argument("--phase",   type=str, default=None,
                        help="Phase: 1, 2, 3, or 'all'")
    parser.add_argument("--scrape-id", type=int, default=None,
                        help="Scope Phase 2/3 to specific scrape")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show what would be done")
    parser.add_argument("--all",      action="store_true",
                        help="Run all three phases")
    args = parser.parse_args()

    init_db()

    if not args.phase and not args.all:
        print_status()
        return

    phase = args.phase or "all"

    print("=" * 60)
    print("Pipeline Run")
    print("=" * 60)
    print(f"  Phase:   {phase}")
    print(f"  Dry-run: {args.dry_run}")
    print("=" * 60)

    logged_in, reason = check_login_status()
    print(f"\nX Login: {'OK' if logged_in else 'FAIL'} ({reason})")

    if args.dry_run:
        dry_run_report(phase)
        return

    # ── Credential bootstrap ─────────────────────────────────────────────────
    if not ensure_credentials():
        print("\nCredential bootstrap failed — aborting pipeline.")
        return

    # ── Phase 1 ───────────────────────────────────────────────────────────
    if phase in ("1", "all"):
        print("\n[Phase 1] Starting X Multi-Query Scrape...")
        if not logged_in:
            print("  WARNING: not logged in — using existing cookies (may be stale).")
        rc, tweet_count = run_x_multi_search()
        latest_scrape = get_latest_scrape_id()
        print(f"\n[Phase 1] Done — tweets in DB: {tweet_count}, scrape_id={latest_scrape}")
        if rc != 0:
            print(f"  FAIL: Phase 1 exit code {rc}")
            return

        # ── Phase 1.5: Query Expansion ───────────────────────────────────
        if phase in ("1", "all"):
            print("\n[Phase 1.5] Expanding queries via LLM...")
            rc_exp, new_queries = run_expand_queries(scrape_id=latest_scrape)
            if rc_exp == 0 and new_queries:
                print(f"  [OK] {len(new_queries)} new queries added to configs/x-scraper.yaml")
                for q in new_queries:
                    print(f"    + {q}")
            else:
                print(f"  [SKIP] query expansion returned {rc_exp}")

    # ── Phase 2 ─────────────────────────────────────────────────────────
    if phase in ("2", "all"):
        print("\n[Phase 2] Starting LLM Prompt Extraction...")
        new_ids = get_new_prompt_ids()
        print(f"  Will process tweets (existing entries auto-skipped)")
        rc, prompt_count = run_extract_prompts(scrape_id=args.scrape_id)
        print(f"\n[Phase 2] Done — prompts in DB: {prompt_count}")
        if rc != 0:
            print(f"  FAIL: Phase 2 exit code {rc}")
            return

    # ── Phase 3 ─────────────────────────────────────────────────────────
    if phase in ("3", "all"):
        print("\n[Phase 3] Starting Gemini Image Generation...")
        new_ids = get_new_prompt_ids()
        print(f"  Will generate {len(new_ids)} images")
        rc, image_count = run_generate_images(scrape_id=args.scrape_id)
        print(f"\n[Phase 3] Done — images in DB: {image_count}")
        if rc != 0:
            print(f"  FAIL: Phase 3 exit code {rc}")
            return

    print_status()


if __name__ == "__main__":
    main()