"""
auto_git.py — Simple git commit helper for pipeline phases.

Usage:
    python scripts/auto_git.py --phase 1 --stats "38 new tweets"
    python scripts/auto_git.py --phase 2 --stats "14 new prompts"
    python scripts/auto_git.py --phase 3 --stats "14 cover images"
    python scripts/auto_git.py --status   # show git status
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

import argparse
import subprocess


PHASE_MESSAGES = {
    "1": "Phase 1 — X Multi-Query Scrape",
    "2": "Phase 2 — LLM Prompt Extraction",
    "3": "Phase 3 — Gemini Image Generation",
}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
    )


def git_status() -> str:
    result = _run(["git", "status", "--short"])
    return result.stdout.strip()


def git_add_and_commit(message: str) -> tuple[bool, str]:
    result = _run(["git", "add", "-A"])
    if result.returncode != 0:
        return False, f"git add failed: {result.stderr}"

    result = _run(["git", "diff", "--cached", "--quiet"])
    if result.returncode == 1:
        result = _run(["git", "commit", "-m", message])
        if result.returncode != 0:
            return False, f"git commit failed: {result.stderr}"
        return True, f"Committed: {message}"
    else:
        return True, "Nothing to commit (no staged changes)"


def git_log_summary(limit: int = 5) -> str:
    result = _run(["git", "log", f"--oneline", f"-n{limit}"])
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="Simple git commit helper")
    parser.add_argument("--phase", type=str, default=None,
                        help="Phase number: 1, 2, or 3")
    parser.add_argument("--stats", type=str, default="",
                        help="Stats string to append to commit message")
    parser.add_argument("--status", action="store_true",
                        help="Show git status and recent commits")
    args = parser.parse_args()

    if args.status or (not args.phase):
        status = git_status()
        print("Git Status:")
        if status:
            print(status)
        else:
            print("  (clean)")
        print()
        print("Recent commits:")
        print(git_log_summary())
        return

    if args.phase not in PHASE_MESSAGES:
        print(f"Invalid phase: {args.phase}. Must be 1, 2, or 3.")
        sys.exit(1)

    phase_msg = PHASE_MESSAGES[args.phase]
    stats_msg = f" — {args.stats}" if args.stats else ""
    full_msg = f"feat(pipeline): {phase_msg}{stats_msg}"

    print(f"Committing: {full_msg}")
    ok, detail = git_add_and_commit(full_msg)
    print(detail)


if __name__ == "__main__":
    main()