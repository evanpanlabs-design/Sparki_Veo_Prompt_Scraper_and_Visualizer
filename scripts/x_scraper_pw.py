"""
Complete Playwright-based X scraper.

Flow:
  1. Launch browser with saved cookies
  2. Navigate to search page (try Top/Latest tabs), wait 5s
  3. Scroll-and-extract loop: scroll to bottom → wait 5s → extract → repeat
     Stops early when no new tweets appear for 3 consecutive scrolls
  4. Round-1 filter: likes >= min_likes
  5. For each surviving tweet:
       a. Visit author profile → get followers_count
       b. Visit tweet detail  → get full text + views
       c. Local keyword filter: all query keywords must appear in text
       d. Round-2 filter: followers >= min_followers, views >= min_views
  6. Sort by word count (descending), output as JSON

Usage:
    python scripts/x_scraper_pw.py --query "veo prompt" --min-likes 50 --min-views 1000 --min-followers 1000
"""

import argparse
import random
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path

import json

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COOKIES = PROJECT_ROOT / "outputs" / "cookies.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "results.json"
DEFAULT_PROXY = "http://127.0.0.1:7897"


# ─── Helpers ────────────────────────────────────────────────────────────────────

def rnd_delay(min_ms: int = 50, max_ms: int = 1000):
    _time.sleep(random.uniform(min_ms, max_ms) / 1000.0)


def load_cookies(cookies_path: Path) -> list[dict]:
    with open(cookies_path, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    return [{"name": k, "value": v, "domain": ".x.com", "path": "/"} for k, v in cookies.items()]


def parse_metric(text: str) -> int:
    """Parse metric strings like '1.29K', '3.5M', '1,234' to an integer."""
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
    """Return True if text contains ALL keywords (case-insensitive)."""
    text_lower = text.lower()
    return all(kw.lower() in text_lower for kw in keywords)


def word_count(text: str) -> int:
    """Count words in text (split on whitespace)."""
    return len(text.split())


# ─── Search page extraction ────────────────────────────────────────────────────

def extract_search_tweets(page) -> list[dict]:
    """Extract all [data-testid='tweet'] elements currently in the DOM."""
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

def scroll_and_extract(page, max_scrolls: int = 20, stale_threshold: int = 3) -> list[dict]:
    """Scroll-to-bottom loop with extraction at each step.

    Stops early when no new tweets appear for stale_threshold consecutive scrolls.
    Returns deduplicated list of all extracted tweets.
    """
    all_tweets = []
    seen_ids = set()
    stale_count = 0

    for i in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _time.sleep(5)

        tweets = extract_search_tweets(page)

        new_count = 0
        for t in tweets:
            if t["tweet_id"] not in seen_ids:
                seen_ids.add(t["tweet_id"])
                all_tweets.append(t)
                new_count += 1

        print(f"    Scroll {i+1}/{max_scrolls} — {len(tweets)} in DOM, {new_count} new, total: {len(all_tweets)}")

        if new_count == 0:
            stale_count += 1
            if stale_count >= stale_threshold:
                print(f"  No new tweets for {stale_threshold} scrolls — stopping early.")
                break
        else:
            stale_count = 0

        rnd_delay(100, 500)

    return all_tweets


# ─── Main scraper ──────────────────────────────────────────────────────────────

def scrape(
    query: str,
    min_likes: int,
    min_views: int,
    min_followers: int,
    scroll_times: int,
    proxy: str | None,
    cookies_path: Path,
    output_path: Path,
) -> list[dict]:
    proxy_config = {"server": proxy} if proxy else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
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

        # ── Step 1: navigate to search page, try Top + Latest tabs ──────────
        page = context.new_page()
        tabs = ["Top", "Latest"]
        search_loaded = False

        for tab in tabs:
            search_url = f"https://x.com/search?q={query}&src=typed_query"
            print(f"\nNavigating to search ({tab} tab)...")
            page.goto(search_url, timeout=60_000)
            _time.sleep(5)

            try:
                page.wait_for_selector('[data-testid="primaryColumn"]', timeout=15_000)
                page.wait_for_selector('[data-testid="tweet"]', timeout=15_000)
                print(f"  {tab} tab loaded successfully.")
                search_loaded = True
                break
            except Exception as e:
                print(f"  {tab} tab failed: {e}")
                try:
                    other = "Latest" if tab == "Top" else "Top"
                    page.locator(f'span:has-text("{other}")').first.click()
                    _time.sleep(5)
                    page.wait_for_selector('[data-testid="tweet"]', timeout=15_000)
                    print(f"  Switched to {other} tab — loaded.")
                    search_loaded = True
                    break
                except Exception:
                    continue

        if not search_loaded:
            print("ERROR: Could not load search page (tried Top and Latest).")
            browser.close()
            sys.exit(1)

        # ── Step 2: scroll + extract loop ─────────────────────────────────
        print(f"\nScrolling and extracting (up to {scroll_times} scrolls, auto-stop on stale)...")
        all_tweets = scroll_and_extract(page, max_scrolls=scroll_times, stale_threshold=3)
        print(f"\nTotal extracted (after dedup): {len(all_tweets)}")

        if not all_tweets:
            page.close()
            browser.close()
            _save_output(output_path, query, min_likes, min_views, min_followers, [])
            return []

        # ── Step 3: round-1 filter (likes only) ────────────────────────────
        print(f"\nRound-1 filter: likes >= {min_likes}")
        r1 = [t for t in all_tweets if t["favorite_count"] >= min_likes]
        print(f"  Passed: {len(r1)}")

        if not r1:
            page.close()
            browser.close()
            _save_output(output_path, query, min_likes, min_views, min_followers, [])
            return []

        # ── Step 4: enrich each surviving tweet ─────────────────────────────
        print(f"\nEnriching {len(r1)} tweets...")
        keywords = query.split()   # split search query into individual keywords
        final_results = []

        for i, t in enumerate(r1):
            screen = t["author_screen"]
            print(f"  [{i+1}/{len(r1)}] @{screen} ({t['tweet_id']})...", end=" ", flush=True)

            followers = get_follower_count(context, t["profile_url"])
            print(f"followers={followers}", end=" | ", flush=True)

            if min_followers > 0 and followers < min_followers:
                print("FILTERED (followers)")
                rnd_delay(200, 800)
                continue

            full_text, detail_views = get_tweet_detail(context, t["tweet_url"])
            view_count = detail_views if detail_views > 0 else t["view_count"]
            print(f"views={view_count}", end=" | ", flush=True)

            if view_count > 0 and min_views > 0 and view_count < min_views:
                print("FILTERED (views)")
                rnd_delay(200, 800)
                continue

            if not full_text:
                full_text = t["short_text"]

            # Local keyword filter: all keywords must appear in text (case-insensitive)
            if not text_matches_all_keywords(full_text, keywords):
                print("FILTERED (keywords)")
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
                "hashtags":        [],
                "media":           [],
                "author": {
                    "name":            t["author_name"],
                    "screen_name":     t["author_screen"],
                    "followers_count": followers,
                },
            }
            final_results.append(tweet_data)
            print(f"PASSED (likes={t['favorite_count']}, followers={followers}, views={view_count})")

            rnd_delay(200, 1000)

        page.close()
        browser.close()

        # Sort by word count descending (more words = higher rank)
        final_results.sort(key=lambda x: word_count(x["text"]), reverse=True)
        _save_output(output_path, query, min_likes, min_views, min_followers, final_results)
        return final_results


def _save_output(output_path, query, min_likes, min_views, min_followers, tweets):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "scrape_time": datetime.now(timezone.utc).isoformat(),
        "query":       query,
        "filters": {
            "min_likes":      min_likes,
            "min_views":      min_views,
            "min_followers":  min_followers,
        },
        "total_filtered": len(tweets),
        "tweets": tweets,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {output_path}")
    print(f"Total tweets matching all filters: {len(tweets)}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="X scraper (Playwright)")
    parser.add_argument("--query",          required=True,  help='Search keyword (e.g., "veo prompt")')
    parser.add_argument("--min-likes",      type=int, default=50,   help="Minimum likes (default: 50)")
    parser.add_argument("--min-views",      type=int, default=1000, help="Minimum views (default: 1000)")
    parser.add_argument("--min-followers",  type=int, default=1000, help="Minimum author followers (default: 1000)")
    parser.add_argument("--scroll",         type=int, default=20,  help="Max scroll operations (default: 20)")
    parser.add_argument("--proxy",          type=str, default=DEFAULT_PROXY,
                        help=f"HTTP proxy (default: {DEFAULT_PROXY}, '' to disable)")
    parser.add_argument("--cookies",        type=str, default=str(DEFAULT_COOKIES), help="cookies.json path")
    parser.add_argument("--output",         type=str, default=str(DEFAULT_OUTPUT), help="Output JSON path")
    args = parser.parse_args()

    print("=" * 60)
    print("X Scraper (Playwright)")
    print("=" * 60)
    print(f"  Query:        {args.query}")
    print(f"  Filters:      likes>={args.min_likes}, views>={args.min_views}, followers>={args.min_followers}")
    print(f"  Max scrolls:  {args.scroll}")
    print(f"  Proxy:        {args.proxy or '(none)'}")
    print(f"  Cookies:      {args.cookies}")
    print(f"  Output:       {args.output}")
    print("=" * 60 + "\n")

    results = scrape(
        query=args.query,
        min_likes=args.min_likes,
        min_views=args.min_views,
        min_followers=args.min_followers,
        scroll_times=args.scroll,
        proxy=args.proxy if args.proxy else None,
        cookies_path=Path(args.cookies),
        output_path=Path(args.output),
    )

    print(f"\nDone. {len(results)} tweets saved.")


if __name__ == "__main__":
    main()
