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
from flask_cors import CORS

# ─── App Setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)
CORS(app)

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


# ─── Cookie Helpers ─────────────────────────────────────────────────────────

def load_cookies(cookies_path: Path) -> list[dict]:
    """Load cookies from flat dict format (same as x_multi_search.py)."""
    with open(cookies_path, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    return [{"name": k, "value": v, "domain": ".x.com", "path": "/"} for k, v in cookies.items()]


def validate_cookies(cookies_path: Path) -> tuple[bool, str]:
    """Verify cookies.json can successfully log into X.com."""
    if not cookies_path.exists():
        return False, "cookies.json not found"

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            ctx.add_cookies(load_cookies(cookies_path))
            page = ctx.new_page()
            page.goto("https://x.com/home", timeout=15_000)
            page.wait_for_selector('[data-testid="primaryColumn"]', timeout=10_000)
            browser.close()
            return True, "cookies valid"
    except Exception as e:
        return False, str(e)[:120]


def interactive_login(cookies_path: Path) -> bool:
    """Open visible browser for user to log in, save cookies on tab close."""
    from playwright.sync_api import sync_playwright

    print("[Sparki] Cookies invalid — opening login window...")
    state.add_log("WARN", "Cookies 无效，正在打开登录窗口...")

    try:
        browser = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto("https://x.com/i/flow/login", timeout=30_000)

            login_done = threading.Event()

            def on_page_close(close_page):
                login_done.set()

            page.on("close", on_page_close)

            # Poll for successful login every 5s
            while not login_done.is_set():
                try:
                    page.wait_for_selector('[data-testid="primaryColumn"]', timeout=5)
                    login_done.set()
                    state.add_log("INFO", "登录成功！正在保存 cookies...")
                except Exception:
                    pass  # still waiting

            if page.url == "about:blank" or page.url.startswith("about:"):
                state.add_log("WARN", "用户关闭了登录窗口，登录取消")
                try:
                    browser.close()
                except Exception:
                    pass
                return False

            # Save cookies in flat dict format (same as browser_auth.py)
            cookies = ctx.cookies()
            flat = {c["name"]: c["value"] for c in cookies}
            cookies_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cookies_path, "w", encoding="utf-8") as f:
                json.dump(flat, f, ensure_ascii=False, indent=2)

            state.add_log("INFO", f"Cookies 已保存 ({len(flat)} 条)")
            try:
                browser.close()
            except Exception:
                pass
            return True

    except Exception as e:
        state.add_log("ERROR", f"登录失败: {e}")
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        return False


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
        # Validate existing cookies
        valid, reason = validate_cookies(cookies)
        if not valid:
            state.add_log("WARN", f"Cookies 无效: {reason}")
            state.add_log("INFO", "正在打开登录窗口...")
            ok = interactive_login(cookies)
            if not ok:
                state.add_log("ERROR", "登录失败，Scrape 取消")
                state.running = False
                return
            # Verify new cookies
            valid2, _ = validate_cookies(cookies)
            if not valid2:
                state.add_log("ERROR", "新 Cookies 仍然无效")
                state.running = False
                return
            state.add_log("INFO", "Cookies 验证通过，开始 Scrape")

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
                ctx = browser.new_context(
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
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
            new_logs = state.get_logs_since(idx)
            for log in new_logs:
                yield f"data: {json.dumps({'type':'log','ts':log['ts'],'level':log['level'],'msg':log['msg'],'stats':state.get_stats()})}\n\n"
            if new_logs:
                idx += len(new_logs)

            # Exit immediately when pipeline has finished
            if state.progress_pct == 100 and not state.running:
                yield f"data: {json.dumps({'type':'done','stats':state.get_stats()})}\n\n"
                break

            # Keepalive
            yield ": \n\n"
            time.sleep(3)

    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={"X-Accel-Buffering": "no"})


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
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"Sparki Server starting on http://{args.host}:{args.port}")
    print(f"API: GET /api/status  POST /api/config  GET /api/logs  POST /api/stop")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()