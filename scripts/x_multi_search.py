"""
Multi-query X scraper — runs multiple search queries and deduplicates results.

Loads queries and filters from configs/x-scraper.yaml, runs each through the
Playwright scroll-and-extract pipeline (reusing logic from x_scraper_pw.py),
applies engagement filters and negative keyword filtering, outputs deduped tweets.

Usage:
    python scripts/x_multi_search.py
    python scripts/x_multi_search.py --config configs/x-scraper.yaml --output outputs/raw_tweets.json
"""

import argparse
import random
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path

import json

import yaml

from playwright.sync_api import sync_playwright

from db import init_db, create_scrape, insert_tweets, get_all_tweet_ids
from logging_utils import get_logger, step_log

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COOKIES = PROJECT_ROOT / "outputs" / "cookies.json"
DEFAULT_RAW_OUTPUT = PROJECT_ROOT / "outputs" / "raw_tweets.json"
DEFAULT_PROXY = "http://127.0.0.1:7897"


# ─── Helpers (reused from x_scraper_pw.py) ────────────────────────────────────

def rnd_delay(min_ms: int = 50, max_ms: int = 1000):
    _time.sleep(random.uniform(min_ms, max_ms) / 1000.0)


def load_cookies(cookies_path: Path) -> list[dict]:
    with open(cookies_path, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    return [{"name": k, "value": v, "domain": ".x.com", "path": "/"} for k, v in cookies.items()]


def parse_metric(text: str) -> int:
    if not text:
        return 0
    text = text.strip().replace(",", "")
    if not text:
        return 0
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    if text[-1] in mult:
        try:
            return int(float(text[:-1]) * mult[text[-1]])
        except ValueError:
            return 0
    try:
        return int(text)
    except ValueError:
        return 0


def text_matches_all_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return all(kw.lower() in text_lower for kw in keywords)


def text_contains_any(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


# ─── Search page extraction (copied from x_scraper_pw.py) ─────────────────────

def extract_search_tweets(page) -> list[dict]:
    results = []
    tweets = page.query_selector_all('[data-testid="tweet"]')
    for tweet in tweets:
        try:
            user_name_elem = tweet.query_selector('[data-testid="User-Name"]')
            if not user_name_elem:
                continue

            profile_url = None
            for link in user_name_elem.query_selector_all("a"):
                href = link.get_attribute("href") or ""
                if "/status/" not in href and href not in ("/", "") and "?" not in href:
                    profile_url = href
                    break
            if not profile_url:
                continue

            spans = user_name_elem.query_selector_all("span")
            display_name = spans[0].inner_text() if spans else ""
            screen_name = ""
            for span in spans:
                t = span.inner_text()
                if t.startswith("@"):
                    screen_name = t.lstrip("@")
                    break

            tweet_url = None
            for link in tweet.query_selector_all("a"):
                href = link.get_attribute("href") or ""
                if "/status/" in href and "/analytics" not in href:
                    tweet_url = href
                    break
            if not tweet_url:
                continue
            tweet_id = tweet_url.split("/status/")[-1].split("?")[0]

            text_elem = tweet.query_selector('[data-testid="tweetText"]')
            short_text = text_elem.inner_text() if text_elem else ""

            def metric(sel):
                el = tweet.query_selector(sel)
                return parse_metric(el.inner_text()) if el else 0

            like_count   = metric("[data-testid='like']")
            rt_count     = metric("[data-testid='retweet']")
            reply_count  = metric("[data-testid='reply']")
            view_count   = metric("[data-testid='viewCount']")

            time_elem = tweet.query_selector("time")
            created_at = time_elem.get_attribute("datetime") if time_elem else None

            results.append({
                "tweet_id":       tweet_id,
                "tweet_url":      tweet_url,
                "profile_url":    profile_url,
                "author_name":    display_name,
                "author_screen":  screen_name,
                "short_text":     short_text,
                "created_at":     created_at,
                "favorite_count": like_count,
                "retweet_count":  rt_count,
                "reply_count":    reply_count,
                "view_count":     view_count,
            })
        except Exception:
            continue
    return results


# ─── Profile page ─────────────────────────────────────────────────────────────

def get_follower_count(context, profile_url: str) -> int:
    try:
        page = context.new_page()
        rnd_delay(100, 600)
        page.goto("https://x.com" + profile_url, timeout=30_000)
        page.wait_for_timeout(3_000)
        body = page.locator("body").inner_text()
        page.close()
        for line in body.split("\n"):
            if "Followers" in line:
                parts = line.strip().split()
                if parts:
                    return parse_metric(parts[0])
        return 0
    except Exception:
        try:
            page.close()
        except Exception:
            pass
        return 0


# ─── Tweet detail ──────────────────────────────────────────────────────────────

def get_tweet_detail(context, tweet_url: str) -> tuple[str, int]:
    try:
        page = context.new_page()
        rnd_delay(100, 600)
        page.goto("https://x.com" + tweet_url, timeout=30_000)
        page.wait_for_selector("[data-testid='tweetText']", timeout=15_000)
        full_text = page.locator("[data-testid='tweetText']").first.inner_text()
        view_count = 0
        view_elem = page.query_selector("[data-testid='viewCount']")
        if view_elem:
            view_count = parse_metric(view_elem.inner_text())
        page.close()
        return full_text, view_count
    except Exception:
        try:
            page.close()
        except Exception:
            pass
        return "", 0


# ─── Scroll + extract loop ─────────────────────────────────────────────────────

def scroll_and_scrape(page, max_scrolls: int = 200,
                     pause: float = 0.2,
                     stale_threshold: int = 8,
                     known_ids: set[str] = None) -> list[dict]:
    """
    One-scroll-one-scrape: X.com uses a virtualised timeline — only tweets visible
    in or near the viewport are kept in the DOM. Older tweets are evicted as you scroll.

    Instead of tracking incremental new tweets (which silently drops evicted ones),
    we keep a UNION of every tweet_id seen so far across all iterations.
    Once the union stops growing for stale_threshold iterations, we stop.

    Each iteration: one PageDown → brief pause → extract ALL tweets in DOM →
    add their IDs to the global union → continue.
    """
    if known_ids is None:
        known_ids = set()

    all_tweets_map: dict[str, dict] = {}   # tweet_id -> tweet dict
    seen_this_run: set[str] = set()        # tweet_ids seen in THIS scroll pass
    stale_count = 0
    last_scrollY = 0
    last_print_iter = 0

    for i in range(max_scrolls):
        # ── One scroll action ─────────────────────────────────────────────────
        page.keyboard.press("PageDown")
        _time.sleep(pause)

        # ── Scrape ALL tweets currently in DOM (union strategy) ─────────────
        tweets = extract_search_tweets(page)
        new_in_union = 0

        for t in tweets:
            tid = t["tweet_id"]
            if tid in known_ids:
                continue
            if tid not in seen_this_run:
                seen_this_run.add(tid)
                all_tweets_map[tid] = t
                new_in_union += 1

        # ── Diagnose scroll progress ─────────────────────────────────────────
        info = page.evaluate("""
            () => ({
                scrollY: window.scrollY,
                scrollH: document.documentElement.scrollHeight,
                innerH: window.innerHeight,
            })
        """)
        scrollY = info["scrollY"]
        scrollH = info["scrollH"]
        delta = scrollY - last_scrollY
        last_scrollY = scrollY
        at_bottom = (scrollY + info["innerH"]) >= scrollH - 50

        # ── Progress output every 20 iters (flush immediately) ──────────────
        if i - last_print_iter >= 20:
            elapsed = (i + 1) * pause
            print(f"    [iter {i+1}/{max_scrolls}] "
                  f"UNION={len(all_tweets_map)} new={new_in_union} "
                  f"scrollY={scrollY} at_bottom={at_bottom} "
                  f"(~{elapsed:.0f}s elapsed)", flush=True)
            last_print_iter = i

        # ── Stop conditions ──────────────────────────────────────────────────
        if new_in_union == 0:
            stale_count += 1
            if stale_count >= stale_threshold:
                print(f"  [DONE] Union stalled {stale_threshold}x (iter {i+1}). "
                      f"Total: {len(all_tweets_map)} tweets.", flush=True)
                break
        else:
            stale_count = 0

        if at_bottom:
            print(f"  [DONE] Reached bottom (iter {i+1}). "
                  f"Total: {len(all_tweets_map)} tweets.", flush=True)
            break
    else:
        print(f"  [DONE] Hit max_iter={max_scrolls}. "
              f"Total: {len(all_tweets_map)} tweets.", flush=True)

    return list(all_tweets_map.values())


# ─── Load config ───────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── Main ──────────────────────────────────────────────────────────────────────

def scrape_all(
    config_path: Path,
    cookies_path: Path,
    output_path: Path,
    proxy: str | None,
    headless: bool = True,
) -> list[dict]:
    cfg = load_config(config_path)
    queries = cfg.get("queries", [])
    negative_keywords = cfg.get("negative_keywords", [])
    min_likes = cfg.get("engagement", {}).get("min_likes", 50)
    min_followers = cfg.get("engagement", {}).get("min_followers", 1000)

    if not queries:
        print("ERROR: No queries found in config.")
        sys.exit(1)

    proxy_config = {"server": proxy} if proxy else None

    # Structured logger for Phase 1
    log = get_logger("phase1")
    log.info("START | config=%s queries=%d headless=%s", config_path.name, len(queries), headless)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            proxy=proxy_config,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        context.add_cookies(load_cookies(cookies_path))

        # Collect all tweets across queries, deduplicated by tweet_id
        all_tweets_map = {}   # tweet_id -> tweet dict
        tweet_to_query = {}   # tweet_id -> source query (for per-query stats)
        query_raw_counts = {}  # query -> count before likes filter
        query_dedup_counts = {}  # query -> count after likes filter + dedup

        # Load all known tweet IDs once (for dedup during scraping)
        init_db()
        known_ids = get_all_tweet_ids()
        print(f"\nKnown tweet IDs in DB: {len(known_ids)}")

        for query in queries:
            print(f"\n{'='*60}", flush=True)
            print(f"Query: {query}", flush=True)
            print(f"{'='*60}", flush=True)
            log.info("QUERY_START | query='%s'", query)

            page = context.new_page()
            search_url = f"https://x.com/search?q={query}&src=typed_query"
            log.info("NAV search | query='%s' url='%s'", query, search_url)
            page.goto(search_url, timeout=60_000)
            _time.sleep(5)

            try:
                page.wait_for_selector('[data-testid="primaryColumn"]', timeout=15_000)
                page.wait_for_selector('[data-testid="tweet"]', timeout=15_000)
                log.info("PAGE_LOADED | query='%s' status=OK", query)
            except Exception as e:
                log.info("PAGE_LOADED | query='%s' status=FAIL error='%s'", query, e)
                print(f"  Could not load search page: {e}", flush=True)
                page.close()
                continue

            # Scroll + extract with one-scroll-one-scrape union strategy
            raw_tweets = scroll_and_scrape(page, max_scrolls=150, pause=0.2,
                                            stale_threshold=8, known_ids=known_ids)
            page.close()

            query_raw_counts[query] = len(raw_tweets)
            log.info("QUERY raw=%d tweets", len(raw_tweets))

            # Round-1: likes filter
            filtered = [t for t in raw_tweets if t["favorite_count"] >= min_likes]
            log.info("QUERY likes_filter | passed=%d/%d", len(filtered), len(raw_tweets))

            # Add to global dedup map, track per-query
            dedup_count = 0
            for t in filtered:
                if t["tweet_id"] not in all_tweets_map:
                    all_tweets_map[t["tweet_id"]] = t
                    tweet_to_query[t["tweet_id"]] = query
                    dedup_count += 1
                else:
                    pass
            query_dedup_counts[query] = dedup_count
            log.info("QUERY done | query='%s' raw=%d dedup=%d total_unique=%d",
                     query, len(raw_tweets), dedup_count, len(all_tweets_map))

            rnd_delay(500, 1500)

        browser.close()

    # Convert to list
    all_tweets = list(all_tweets_map.values())
    print(f"\n{'='*60}")
    print(f"Total unique tweets before enrichment: {len(all_tweets)}")

    # ── Enrich each tweet (followers + full text) ────────────────────────────
    print(f"\nEnriching {len(all_tweets)} tweets (followers + full text)...")
    enriched = []

    # Reuse a single browser + context for all enrichment calls (huge speedup)
    with sync_playwright() as p2:
        mini_browser = p2.chromium.launch(
            headless=headless,
            proxy=proxy_config,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        mini_context = mini_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        mini_context.add_cookies(load_cookies(cookies_path))

        for i, t in enumerate(all_tweets):
            screen = t["author_screen"]
            log.info("ENRICH_START | tweet=%d/%d @%s id=%s",
                     i + 1, len(all_tweets), screen, t["tweet_id"])

            followers = get_follower_count(mini_context, t["profile_url"])
            log.info("ENRICH followers | @%s followers=%d min=%d",
                     screen, followers, min_followers)

            if min_followers > 0 and followers < min_followers:
                log.info("ENRICH FILTERED | @%s followers=%d < %d", screen, followers, min_followers)
                rnd_delay(200, 800)
                continue

            full_text, detail_views = get_tweet_detail(mini_context, t["tweet_url"])
            view_count = detail_views if detail_views > 0 else t["view_count"]
            log.info("ENRICH detail | @%s views=%d text_len=%d",
                     screen, view_count, len(full_text) if full_text else 0)

            if not full_text:
                full_text = t["short_text"]

            # Negative keyword filter
            is_veo = text_contains_any(full_text, ["veo", "gemini"]) or \
                     text_contains_any(t["short_text"], ["veo", "gemini"])
            has_negative = text_contains_any(full_text, negative_keywords)

            if has_negative and not is_veo:
                neg_kw = [kw for kw in negative_keywords if kw.lower() in full_text.lower()]
                log.info("ENRICH FILTERED | @%s negative=%s", screen, neg_kw)
                rnd_delay(200, 800)
                continue

            tweet_data = {
                "tweet_id":        t["tweet_id"],
                "url":             f"https://x.com{t['tweet_url']}",
                "text":            full_text,
                "created_at":      t["created_at"],
                "favorite_count":  t["favorite_count"],
                "retweet_count":   t["retweet_count"],
                "reply_count":     t["reply_count"],
                "view_count":      view_count,
                "quote_count":     0,
                "bookmark_count":  0,
                "author": {
                    "name":            t["author_name"],
                    "screen_name":     t["author_screen"],
                    "followers_count": followers,
                },
            }
            enriched.append(tweet_data)
            log.info("ENRICH PASSED | @%s likes=%d followers=%d views=%d",
                     screen, t["favorite_count"], followers, view_count)
            rnd_delay(200, 1000)

        mini_browser.close()

    log.info("PHASE1_END | total_enriched=%d queries=%d", len(enriched), len(queries))
    print(f"\nTotal tweets after all filters: {len(enriched)}", flush=True)

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result_data = {
        "scrape_time": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "filters": {
            "min_likes":      min_likes,
            "min_followers":   min_followers,
            "negative_keywords": negative_keywords,
        },
        "total_deduped": len(enriched),
        "tweets": enriched,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    # Build query_stats before writing to DB
    query_stats = {}
    for query in queries:
        query_stats[query] = {
            "raw": query_raw_counts.get(query, 0),
            "dedup": query_dedup_counts.get(query, 0),
            "prompts": 0,  # filled in by Phase 2
        }

    # Write to SQLite database
    init_db()
    config_yaml = config_path.read_text(encoding="utf-8")
    scrape_id = create_scrape(
        queries=queries,
        config_yaml=config_yaml,
        total_raw=len(enriched),
        total_dedup=len(enriched),
        query_stats=query_stats,
    )
    log.info("DB_WRITE | scrape_id=%d tweets=%d", scrape_id, len(enriched))

    # Group enriched tweets by source query (from tweet_to_query map)
    tweets_by_query = {}
    for t in enriched:
        q = tweet_to_query.get(t["tweet_id"], None)
        if q not in tweets_by_query:
            tweets_by_query[q] = []
        tweets_by_query[q].append(t)

    for query, tweets_list in tweets_by_query.items():
        insert_tweets(scrape_id, tweets_list, source_query=query)

    for query, tweets_list in tweets_by_query.items():
        log.info("DB_TWEETS | query='%s' count=%d", query, len(tweets_list))

    log.info("COMPLETE | output=%s scrape_id=%d tweets=%d", output_path, scrape_id, len(enriched))
    print(f"Saved to {output_path}", flush=True)
    print(f"Written to database: scrape_id={scrape_id}, query_stats={query_stats}", flush=True)
    return enriched


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-query X scraper with dedup and negative filtering")
    parser.add_argument("--config",   type=str, default="configs/x-scraper.yaml",
                        help="Path to config YAML")
    parser.add_argument("--cookies",  type=str, default=str(DEFAULT_COOKIES),
                        help="Cookies JSON path")
    parser.add_argument("--output",   type=str, default=str(DEFAULT_RAW_OUTPUT),
                        help="Output JSON path")
    parser.add_argument("--proxy",    type=str, default=DEFAULT_PROXY,
                        help=f"HTTP proxy (default: {DEFAULT_PROXY}, '' to disable)")
    parser.add_argument("--headless",  action="store_true", default=True,
                        help="Run browser in headless mode (default, use --no-headless for visible)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Force visible browser for debugging")
    args = parser.parse_args()

    config_path = PROJECT_ROOT / args.config
    cookies_path = Path(args.cookies)
    output_path = Path(args.output)

    print("=" * 60)
    print("X Multi-Query Scraper")
    print("=" * 60)
    print(f"  Config:    {config_path}")
    print(f"  Cookies:   {cookies_path}")
    print(f"  Output:    {output_path}")
    print(f"  Proxy:     {args.proxy or '(none)'}")
    headless = not args.no_headless  # default True (headless)
    print(f"  Headless:  {headless}")
    print("=" * 60 + "\n")

    scrape_all(
        config_path=config_path,
        cookies_path=cookies_path,
        output_path=output_path,
        proxy=args.proxy if args.proxy else None,
        headless=headless,
    )


if __name__ == "__main__":
    main()
