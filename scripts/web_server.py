"""
web_server.py — Flask API for pipeline trigger and prompt display.

Usage:
    python scripts/web_server.py [--port 5000]

Endpoints:
    GET  /status      — pipeline status (login, counts)
    GET  /prompts     — all prompts from DB as JSON
    POST /start       — trigger full pipeline (runs in background)
    GET  /job/<jid>   — job status/progress
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from scripts.db import (
    init_db,
    get_all_prompts,
)
from scripts.run_pipeline import (
    check_login_status,
    run_x_multi_search,
    run_extract_prompts,
    run_generate_images,
    get_new_prompt_ids,
    count_tweets_in_db,
    count_prompts_in_db,
    count_images_in_db,
    get_latest_scrape_id,
)
from scripts.auto_git import git_add_and_commit

app = Flask(__name__)

# In-memory job registry
_jobs: dict[str, dict] = {}


# ─── Job helpers ─────────────────────────────────────────────────────────────

def start_job(phase: str = "all"):
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "phase": phase,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "stats": {},
        "error": None,
    }

    def _run():
        try:
            logged_in, reason = check_login_status()

            if phase in ("1", "all"):
                _jobs[job_id]["stats"]["phase1_start"] = count_tweets_in_db()
                rc, tc = run_x_multi_search()
                _jobs[job_id]["stats"]["phase1_tweets"] = tc
                scrape_id = get_latest_scrape_id()
                git_add_and_commit(
                    f"feat(pipeline): Phase 1 scrape — {tc} tweets"
                )

            if phase in ("2", "all") or phase == "1":
                # Phase 2 runs after Phase 1 (even if only --phase 1 was requested, run 2 too)
                if phase != "2":
                    # use latest scrape_id if triggered by --all or standalone
                    scrape_id = get_latest_scrape_id()
                rc, pc = run_extract_prompts(scrape_id=scrape_id)
                _jobs[job_id]["stats"]["phase2_prompts"] = pc
                git_add_and_commit(
                    f"feat(pipeline): Phase 2 extract — {pc} prompts"
                )

            if phase in ("3", "all"):
                new_ids = get_new_prompt_ids()
                _jobs[job_id]["stats"]["phase3_images"] = len(new_ids)
                rc, ic = run_generate_images()
                _jobs[job_id]["stats"]["phase3_images"] = ic
                git_add_and_commit(
                    f"feat(pipeline): Phase 3 images — {ic} images"
                )

            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return job_id


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.route("/status", methods=["GET"])
def status():
    init_db()
    logged_in, reason = check_login_status()
    return jsonify({
        "logged_in": logged_in,
        "login_reason": reason,
        "tweets": count_tweets_in_db(),
        "prompts": count_prompts_in_db(),
        "images": count_images_in_db(),
    })


@app.route("/prompts", methods=["GET"])
def prompts():
    init_db()
    all_prompts = get_all_prompts(limit=500)
    # flatten for JSON (SQLite Row → dict handled by get_all_prompts)
    return jsonify({
        "total": len(all_prompts),
        "prompts": [
            {
                "id": p["id"],
                "title": p["title"],
                "category": p["category"],
                "prompt_text": p["prompt_text"],
                "notes": p.get("notes", ""),
                "author_screen": p.get("author_screen", ""),
                "followers_count": p.get("followers_count", 0),
                "likes_count": p.get("likes_count", 0),
                "retweet_count": p.get("retweet_count", 0),
                "reply_count": p.get("reply_count", 0),
                "view_count": p.get("view_count", 0),
                "url": p["url"],
                "image_gcs_url": p.get("image_gcs_url") or None,
                "image_generated_at": p.get("image_generated_at") or None,
            }
            for p in all_prompts
        ],
    })


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(silent=True) or {}
    phase = data.get("phase", "all")
    job_id = start_job(phase)
    return jsonify({"job_id": job_id, "phase": phase, "status": "started"})


@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify({"job_id": job_id, **job})


@app.route("/jobs", methods=["GET"])
def list_jobs():
    return jsonify({jid: {k: v for k, v in j.items() if k != "error"}
                     for jid, j in _jobs.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Flask server")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    init_db()
    print(f"Starting server on http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=True)