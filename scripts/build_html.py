"""
Build HTML from DB — reads all prompts from DB and generates veo3-prompt-library.html.

Usage:
    python scripts/build_html.py                    # build from DB (local only)
    python scripts/build_html.py --scrape-id 2      # specific scrape
    python scripts/build_html.py --output /tmp/test.html
    python scripts/build_html.py --publish          # build + sync images + push to GitHub Pages
    python scripts/build_html.py --publish --dry-run  # verify what would be published
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import os
import subprocess
import shutil
from datetime import datetime, timezone

from scripts.db import init_db, _conn

GITHUB_REPO = "https://github.com/evanpanlabs-design/sparkiSeoPromptLibraryWebPage.git"
WEB_TEMP_DIR = Path.home() / ".cache" / "sparki-web-pages"


# ─── Schema validation ─────────────────────────────────────────────────────────

REQUIRED_FIELDS = ["id", "tweet_id", "url", "category", "title", "prompt_text", "notes",
                   "author", "engagement"]
EXPECTED_AUTHOR_KEYS = {"name", "screen_name", "followers"}
EXPECTED_ENGAGEMENT_KEYS = {"likes", "retweets", "replies"}


def validate_prompt(p: dict, local_img_dir: Path) -> list[str]:
    """Validate a single prompt dict. Returns list of error messages (empty = OK)."""
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in p:
            errors.append(f"missing field: '{field}'")

    # Validate nested author
    author = p.get("author", {})
    if not isinstance(author, dict):
        errors.append(f"author is not a dict: {type(author)}")
    else:
        for key in EXPECTED_AUTHOR_KEYS:
            if key not in author:
                errors.append(f"missing author.{key}")

    # Validate nested engagement
    eng = p.get("engagement", {})
    if not isinstance(eng, dict):
        errors.append(f"engagement is not a dict: {type(eng)}")
    else:
        for key in EXPECTED_ENGAGEMENT_KEYS:
            if key not in eng:
                errors.append(f"missing engagement.{key}")

    # Validate image exists locally
    img_path = local_img_dir / f"{p['id']}.png"
    if not img_path.exists():
        errors.append(f"missing image: {img_path}")

    return errors


def validate_prompts(prompts: list[dict], local_img_dir: Path) -> tuple[int, list]:
    """
    Validate all prompts. Returns (error_count, error_messages).
    Aborts build if any prompt has errors.
    """
    all_errors = []
    for p in prompts:
        errs = validate_prompt(p, local_img_dir)
        if errs:
            all_errors.append(f"  prompt id={p.get('id','?')}: {'; '.join(errs)}")

    return len(all_errors), all_errors


# ─── Helpers ───────────────────────────────────────────────────────────────────

def load_template(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def escape_js(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
    )


# ─── HTML build ────────────────────────────────────────────────────────────────

def prompts_to_js_array(prompts: list[dict]) -> str:
    lines = []
    for p in prompts:
        line = (
            f'{{'
            f'"id":{p["id"]},'
            f'"tweet_id":"{escape_js(p.get("tweet_id",""))}",'
            f'"url":"{escape_js(p.get("url",""))}",'
            f'"category":"{escape_js(p.get("category","other"))}",'
            f'"title":"{escape_js(p.get("title",""))}",'
            f'"prompt_text":"{escape_js(p.get("prompt_text",""))}",'
            f'"notes":"{escape_js(p.get("notes",""))}",'
            f'"author":{{'
            f'"name":"{escape_js(p.get("author",{}).get("name",""))}",'
            f'"screen_name":"{escape_js(p.get("author",{}).get("screen_name",""))}",'
            f'"followers":{p.get("author",{}).get("followers",0)}'
            f'}},'
            f'"engagement":{{'
            f'"likes":{p.get("engagement",{}).get("likes",0)},'
            f'"retweets":{p.get("engagement",{}).get("retweets",0)},'
            f'"replies":{p.get("engagement",{}).get("replies",0)}'
            f'}}'
            f'}}'
        )
        lines.append(line)
    return "[\n                " + ",\n                ".join(lines) + "\n              ]"


def build_html(output_path: Path, scrape_id: int = None,
               validate: bool = True,
               local_img_dir: Path = None) -> list[dict]:
    init_db()

    if scrape_id:
        rows = _conn().execute(
            "SELECT id, tweet_id, url, title, category, prompt_text, notes, "
            "author_name, author_screen, followers_count, "
            "likes_count, retweet_count, reply_count, view_count, "
            "image_gcs_url "
            "FROM prompts WHERE scrape_id=? ORDER BY id",
            (scrape_id,),
        ).fetchall()
    else:
        rows = _conn().execute(
            "SELECT id, tweet_id, url, title, category, prompt_text, notes, "
            "author_name, author_screen, followers_count, "
            "likes_count, retweet_count, reply_count, view_count, "
            "image_gcs_url "
            "FROM prompts ORDER BY id"
        ).fetchall()

    # Build flat prompt dicts
    raw_prompts = []
    for r in rows:
        raw_prompts.append({
            "id": r[0],
            "tweet_id": r[1],
            "url": r[2],
            "title": r[3],
            "category": r[4],
            "prompt_text": r[5],
            "notes": r[6] or "",
            "author_name": r[7] or "",
            "author_screen": r[8] or "",
            "followers_count": r[9] or 0,
            "likes_count": r[10] or 0,
            "retweet_count": r[11] or 0,
            "reply_count": r[12] or 0,
            "view_count": r[13] or 0,
            "image_gcs_url": r[14] or "",
        })

    # Convert to nested author/engagement structure (matches JS expectations)
    prompts = []
    for p in raw_prompts:
        prompts.append({
            "id": p["id"],
            "tweet_id": p["tweet_id"],
            "url": p["url"],
            "category": p["category"],
            "title": p["title"],
            "prompt_text": p["prompt_text"],
            "notes": p["notes"],
            "author": {
                "name": p["author_name"],
                "screen_name": p["author_screen"],
                "followers": p["followers_count"],
            },
            "engagement": {
                "likes": p["likes_count"],
                "retweets": p["retweet_count"],
                "replies": p["reply_count"],
            },
            "image_gcs_url": p["image_gcs_url"],
        })

    print(f"Loaded {len(prompts)} prompts from DB (scrape_id={scrape_id or 'all'})")

    # Validate
    if validate and local_img_dir:
        err_count, err_msgs = validate_prompts(prompts, local_img_dir)
        if err_count > 0:
            print(f"\nERROR: {err_count} prompt(s) failed validation:")
            for msg in err_msgs:
                print(msg)
            print("\nAborting build. Fix the above issues before publishing.")
            sys.exit(1)
        print(f"  [{err_count} errors] validation passed")

    template_path = PROJECT_ROOT / "outputs" / "veo3-prompt-library.html"
    if not template_path.exists():
        print(f"ERROR: template not found at {template_path}")
        sys.exit(1)

    html = load_template(template_path)

    old_marker = "const prompts = ["
    old_start_idx = html.find(old_marker)
    if old_start_idx == -1:
        print("ERROR: could not find 'const prompts = [' in template")
        sys.exit(1)

    depth = 0
    for i, c in enumerate(html[old_start_idx:], old_start_idx):
        if c == '[': depth += 1
        elif c == ']': depth -= 1
        if depth == 0:
            old_end_idx = i
            break

    new_array = prompts_to_js_array(prompts)
    new_html = (html[:old_start_idx] + f"const prompts = {new_array}" + html[old_end_idx + 1:])

    new_html = new_html.replace(
        "Last updated: 2026-01-27",
        f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"HTML written to {output_path}")
    cats = {}
    for p in prompts:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    for cat, cnt in sorted(cats.items()):
        print(f"    {cat}: {cnt}")

    return prompts


# ─── GitHub Pages publish ──────────────────────────────────────────────────────

def get_gh_token() -> str | None:
    token = os.environ.get("GH_TOKEN", "").strip()
    if token and token != "your-gh-token-here":
        return token
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip()
    return None


def publish(output_path: Path, local_img_dir: Path, dry_run: bool = False):
    token = get_gh_token()
    if not token:
        print("ERROR: GH_TOKEN not found in environment or .env")
        print("  Set GH_TOKEN=ghp_... in .env to enable --publish")
        sys.exit(1)

    if dry_run:
        print("[DRY RUN] Would publish:")
        print(f"  HTML: {output_path}")
        print(f"  Images: {local_img_dir}/*.png")
        return

    # Clone the GitHub Pages repo using git credentials helper approach
    WEB_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    if (WEB_TEMP_DIR / ".git").exists():
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=WEB_TEMP_DIR, capture_output=True
        )
        subprocess.run(
            ["git", "checkout", "gh-pages"],
            cwd=WEB_TEMP_DIR, capture_output=True
        )
        subprocess.run(
            ["git", "pull", "origin", "gh-pages"],
            cwd=WEB_TEMP_DIR, capture_output=True
        )
    else:
        # Clone using token in URL (git handles credential storage)
        subprocess.run(
            ["git", "clone", "--branch", "gh-pages",
             f"https://{token}@github.com/evanpanlabs-design/sparkiSeoPromptLibraryWebPage.git",
             str(WEB_TEMP_DIR)],
            capture_output=True, text=True
        )
        if not (WEB_TEMP_DIR / ".git").exists():
            print("ERROR: git clone failed")
            sys.exit(1)

    # Copy HTML
    dest_html = WEB_TEMP_DIR / "index.html"
    shutil.copy2(output_path, dest_html)
    print(f"  Copied HTML -> {dest_html}")

    # Copy images
    dest_imgs = WEB_TEMP_DIR / "generated_images"
    dest_imgs.mkdir(exist_ok=True)
    img_files = list(local_img_dir.glob("*.png"))
    copied = 0
    for img in img_files:
        shutil.copy2(img, dest_imgs / img.name)
        copied += 1
    print(f"  Copied {copied} images -> {dest_imgs}")

    # Commit + push
    subprocess.run(["git", "add", "-A"], cwd=WEB_TEMP_DIR, capture_output=True)
    msg = f"publish: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    result = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=WEB_TEMP_DIR, capture_output=True, text=True
    )
    if result.returncode != 0:
        if "nothing to commit" in result.stdout:
            print("  No changes to commit (already up to date)")
            return
        print(f"  Commit failed: {result.stdout}")
        return

    push_result = subprocess.run(
        ["git", "push", "origin", "gh-pages"],
        cwd=WEB_TEMP_DIR, capture_output=True, text=True
    )
    if push_result.returncode == 0:
        print(f"  Pushed to GitHub Pages OK")
    else:
        print(f"  Push failed: {push_result.stderr}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build HTML from DB prompts")
    parser.add_argument("--scrape-id", type=int, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--publish", action="store_true",
                        help="Build + sync images + push to GitHub Pages")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --publish: show what would be done without pushing")
    parser.add_argument("--no-validate", action="store_true",
                        help="Skip schema validation (for debug)")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = PROJECT_ROOT / "outputs" / "veo3-prompt-library.html"

    local_img_dir = PROJECT_ROOT / "outputs" / "generated_images"

    prompts = build_html(
        output_path=output_path,
        scrape_id=args.scrape_id,
        validate=not args.no_validate,
        local_img_dir=local_img_dir,
    )

    if args.publish:
        publish(output_path, local_img_dir, dry_run=args.dry_run)