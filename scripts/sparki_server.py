"""
Sparki HTTP API Server — Flask backend for the Sparki GUI

Runs the Pipeline in a background thread and exposes:
  GET  /api/status        — current task state
  POST /api/config        — start task with config JSON
  GET  /api/logs          — SSE stream of real-time log lines
  POST /api/stop          — send stop signal
  GET  /api/prompts       — all extracted prompts from DB
  GET  /api/config        — GET current saved config

Usage:
    python scripts/sparki_server.py
    python scripts/sparki_server.py --port 8765
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request, Response, stream_with_context

# ─── App Setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

# ─── Task State (shared across threads) ─────────────────────────────────────

class ServerTaskState:
    def __init__(self):
        self.running = False
        self.paused = False
        self.stopped = False
        self.start_time: Optional[float] = None
        self.mode = ""
        self.scroll_count = 0
        self.total_tweets = 0
        self.extracted_prompts = 0
        self.failed = 0
        self.skipped = 0
        self.current_query = ""
        self.current_query_idx = 0
        self.total_queries = 0
        self.progress_pct = 0
        self.logs: list[dict] = []
        self.prompts: list[dict] = []  # prompts extracted this session
        self._lock = threading.Lock()
        self._log_event = threading.Event()
        self._config: dict = {}

    def add_log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.logs.append({"ts": ts, "level": level, "msg": msg})
            if len(self.logs) > 500:
                self.logs = self.logs[-300:]
        self._log_event.set()

    def get_logs_since(self, since_idx: int) -> list[dict]:
        with self._lock:
            if since_idx >= len(self.logs):
                return []
            return self.logs[since_idx:]

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "paused": self.paused,
                "stopped": self.stopped,
                "mode": self.mode,
                "start_time": self.start_time,
                "elapsed_sec": int(time.time() - self.start_time) if self.start_time else 0,
                "scroll_count": self.scroll_count,
                "total_tweets": self.total_tweets,
                "extracted_prompts": self.extracted_prompts,
                "failed": self.failed,
                "skipped": self.skipped,
                "current_query": self.current_query,
                "current_query_idx": self.current_query_idx,
                "total_queries": self.total_queries,
                "progress_pct": self.progress_pct,
            }

    def reset(self):
        with self._lock:
            self.running = False
            self.paused = False
            self.stopped = False
            self.start_time = None
            self.mode = ""
            self.scroll_count = 0
            self.total_tweets = 0
            self.extracted_prompts = 0
            self.failed = 0
            self.skipped = 0
            self.current_query = ""
            self.current_query_idx = 0
            self.total_queries = 0
            self.progress_pct = 0
            self.logs.clear()
            self.prompts.clear()
        self._log_event.clear()


state = ServerTaskState()


# ─── Pipeline Workers ────────────────────────────────────────────────────────

def run_scrape(cfg: dict):
    state.running = True
    state.start_time = time.time()
    state.mode = "scrape"
    state.add_log("SYS", "=== Scrape 开始 ===")

    queries = cfg.get("queries", [])
    sc = cfg.get("scraper", {})
    eng = cfg.get("engagement_filter", {})
    state.total_queries = len(queries)
    state.current_query_idx = 0

    try:
        from scripts.db import init_db, insert_tweets, get_all_tweet_ids, create_scrape, _conn
        init_db()
        known_ids = get_all_tweet_ids()

        from scripts.x_multi_search import scroll_and_scrape
        from playwright.sync_api import sync_playwright

        cookies = PROJECT_ROOT / "outputs" / "cookies.json"
        if not cookies.exists():
            state.add_log("ERROR", f"cookies.json not found at {cookies}")
            state.running = False
            return

        rows = _conn().execute("SELECT MAX(id) FROM scrapes").fetchone()
        scrape_id = rows[0] if rows and rows[0] else None
        if scrape_id is None:
            scrape_id = create_scrape(queries=queries, config_yaml="", total_raw=0, total_dedup=0)

        for qi, query in enumerate(queries):
            if state.stopped:
                break

            state.current_query_idx = qi + 1
            state.current_query = query
            state.progress_pct = int((qi / len(queries)) * 100)
            state.add_log("INFO", f"[{qi+1}/{len(queries)}] Query: {query}")

            proxy_str = "http://127.0.0.1:7897"
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=sc.get("headless", True),
                    proxy={"server": proxy_str} if proxy_str else None,
                )
                ctx = browser.contexts[0]
                page = ctx.new_page()
                page.goto(f"https://x.com/search?q={query.replace(' ', '+')}", timeout=30_000)
                page.wait_for_timeout(2_000)

                tweets = scroll_and_scrape(
                    page,
                    max_scrolls=sc.get("max_scrolls", 150),
                    pause=sc.get("pause_between_scrolls", 0.2),
                    stale_threshold=sc.get("stale_threshold", 8),
                    known_ids=known_ids,
                )
                browser.close()

            for t in tweets:
                known_ids.add(t["tweet_id"])

            state.total_tweets += len(tweets)
            state.scroll_count += 1

            insert_tweets(scrape_id, tweets, source_query=query)

            passed = sum(1 for t in tweets
                if t.get("favorite_count", 0) >= eng.get("min_likes", 50)
                or t.get("retweet_count", 0) >= eng.get("min_retweets", 20))
            state.skipped += (len(tweets) - passed)

            state.add_log("INFO", f"  -> Collected {len(tweets)} tweets (total: {state.total_tweets}), "
                                  f"passed filter {passed}/{len(tweets)}")

            while state.paused and not state.stopped:
                time.sleep(0.3)

    except Exception as e:
        import traceback
        state.add_log("ERROR", f"Scraper exception: {e}")
        state.add_log("ERROR", traceback.format_exc()[-300:])

    state.running = False
    state.progress_pct = 100
    state.add_log("SYS", "=== Scrape 完成 ===")


def run_extract(cfg: dict):
    time.sleep(0.5)
    state.running = True
    state.start_time = state.start_time or time.time()
    state.mode = "extract"
    state.add_log("SYS", "=== Extract 开始 ===")

    try:
        from scripts.db import init_db, get_tweets_without_prompts, insert_prompts, _conn, get_categories
        from scripts.extract_prompts import build_system_prompt, process_tweet
        init_db()

        tweets = get_tweets_without_prompts(limit=200)
        state.total_tweets = len(tweets)
        state.add_log("INFO", f"Found {len(tweets)} tweets to process")

        if not tweets:
            state.add_log("INFO", "No tweets to extract")
            state.running = False
            state.progress_pct = 100
            return

        categories = get_categories()
        system_prompt = build_system_prompt(categories)

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            state.add_log("ERROR", "OPENAI_API_KEY not set in .env")
            state.running = False
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed
        concurrency = 15

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {
                ex.submit(process_tweet, t,
                          cfg.get("llm", {}).get("api_base", "https://api.minimaxi.com/v1"),
                          api_key,
                          cfg.get("llm", {}).get("model", "MiniMax-M2.7"),
                          system_prompt): t
                for t in tweets
            }

            for fut in as_completed(futures):
                if state.stopped:
                    ex.shutdown(wait=False)
                    break

                t = futures[fut]
                try:
                    result_tuple = fut.result()
                except Exception:
                    result_tuple = (None, False, None)

                prompt_dict, was_extracted, _ = result_tuple
                state.scroll_count += 1

                if was_extracted and prompt_dict:
                    state.extracted_prompts += 1
                    title = prompt_dict.get("title", "?")[:50]
                    state.add_log("OK", f"+ Extracted: {title}")

                    # Add to session prompts
                    with state._lock:
                        state.prompts.append(prompt_dict)

                    # Broadcast via SSE
                    broadcast_event({
                        "type": "prompt",
                        "prompt": prompt_dict,
                        "stats": state.get_stats(),
                    })
                else:
                    state.failed += 1

                while state.paused and not state.stopped:
                    time.sleep(0.3)

        if state.extracted_prompts > 0:
            scrape_id = _conn().execute("SELECT MAX(id) FROM scrapes").fetchone()[0] or 1
            # Save prompts to DB
            with state._lock:
                prompts_copy = list(state.prompts)
            insert_prompts(scrape_id, prompts_copy)
            state.add_log("INFO", f"Saved {len(prompts_copy)} prompts to DB")

    except Exception as e:
        import traceback
        state.add_log("ERROR", f"Extract exception: {e}")
        state.add_log("ERROR", traceback.format_exc()[-300:])

    state.running = False
    state.progress_pct = 100
    state.add_log("SYS", "=== Extract 完成 ===")


def run_pipeline(cfg: dict):
    mode_scrape = cfg.get("run_scrape", True)
    mode_extract = cfg.get("run_extract", True)
    mode_html = cfg.get("run_html", True)

    if mode_scrape:
        t1 = threading.Thread(target=run_scrape, args=(cfg,), daemon=True)
        t1.start()
        t1.join()  # wait scrape to finish

    if mode_extract and not state.stopped:
        t2 = threading.Thread(target=run_extract, args=(cfg,), daemon=True)
        t2.start()
        t2.join()

    if mode_html and not state.stopped:
        try:
            from scripts.build_html import build_html
            output_path = PROJECT_ROOT / "outputs" / "veo3-prompt-library.html"
            build_html(output_path, validate=False)
            state.add_log("INFO", "HTML generated successfully!")
            broadcast_event({"type": "done", "stats": state.get_stats()})
        except Exception as e:
            state.add_log("ERROR", f"HTML build failed: {e}")

    state.running = False
    state.add_log("SYS", "=== Pipeline 完成 ===")
    broadcast_event({"type": "done", "stats": state.get_stats()})


def broadcast_event(data: dict):
    """Override in SSE endpoint to push events."""
    pass  # SSE handler replaces this


# ─── SSE Log Stream ─────────────────────────────────────────────────────────

_log_listeners: list = []


def sse_log_stream():
    """Generator for SSE log stream."""
    idx = 0
    while True:
        if state.stopped and not state.running:
            yield f"data: {json.dumps({'type':'done','stats':state.get_stats()})}\n\n"
            break

        new_logs = state.get_logs_since(idx)
        for log in new_logs:
            yield f"data: {json.dumps({'type':'log','ts':log['ts'],'level':log['level'],'msg':log['msg'],'stats':state.get_stats()})}\n\n"
        if new_logs:
            idx += len(new_logs)

        # Also check for prompt events
        with state._lock:
            prompt_count = len(state.prompts)

        time.sleep(0.3)


# ─── API Routes ─────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "stats": state.get_stats(),
        "config": state._config,
        "log_count": len(state.logs),
    })


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(state._config)


@app.route("/api/config", methods=["POST"])
def api_post_config():
    if state.running and not state.paused:
        return jsonify({"ok": False, "error": "Task already running"}), 409

    cfg = request.get_json()
    if not cfg:
        return jsonify({"ok": False, "error": "No JSON body"}), 400

    state._config = cfg
    state.reset()
    state._config = cfg

    mode_scrape = cfg.get("run_scrape", True)
    mode_extract = cfg.get("run_extract", True)

    mode_strs = []
    if mode_scrape: mode_strs.append("Scrape")
    if mode_extract: mode_strs.append("Extract")
    state.mode = "+".join(mode_strs) or "Pipeline"

    state.add_log("SYS", f"Config received — {state.mode}")

    t = threading.Thread(target=run_pipeline, args=(cfg,), daemon=True)
    t.start()

    return jsonify({"ok": True, "mode": state.mode})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state.stopped = True
    state.running = False
    state.add_log("SYS", "Stop signal received")
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    state.paused = not state.paused
    status = "paused" if state.paused else "resumed"
    state.add_log("SYS", f"Task {status}")
    return jsonify({"ok": True, "paused": state.paused})


@app.route("/api/logs", methods=["GET"])
def api_logs():
    def generate():
        idx = 0
        while True:
            if state.stopped and not state.running:
                yield f"data: {json.dumps({'type':'done','stats':state.get_stats()})}\n\n"
                break

            new_logs = state.get_logs_since(idx)
            for log in new_logs:
                yield f"data: {json.dumps({'type':'log','ts':log['ts'],'level':log['level'],'msg':log['msg'],'stats':state.get_stats()})}\n\n"
            if new_logs:
                idx += len(new_logs)

            time.sleep(0.3)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/prompts", methods=["GET"])
def api_prompts():
    with state._lock:
        prompts = list(state.prompts)
    return jsonify({"prompts": prompts, "total": len(prompts)})


@app.route("/api/prompts/all", methods=["GET"])
def api_prompts_all():
    """Read all prompts from DB."""
    try:
        from scripts.db import init_db, get_all_prompts
        init_db()
        prompts = get_all_prompts(limit=500)
        return jsonify({"prompts": prompts, "total": len(prompts)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "running": state.running})


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sparki HTTP API Server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"Sparki Server starting on http://{args.host}:{args.port}")
    print(f"API: GET /api/status  POST /api/config  GET /api/logs  POST /api/stop")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()