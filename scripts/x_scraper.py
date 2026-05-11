"""
X/Twitter Scraper using twikit.

Searches tweets by keyword, filters by engagement metrics and author followers,
and exports results as JSON.

Usage:
    python scripts/x_scraper.py --query "veo prompt" --min-likes 50 --min-views 1000 --min-followers 1000
    python scripts/x_scraper.py --query "veo prompt" --max-pages 5 --output outputs/results.json
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from twikit import Client

# Default paths relative to project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COOKIES = PROJECT_ROOT / "outputs" / "cookies.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "results.json"
DEFAULT_PROXY = "http://127.0.0.1:7897"


def setup_proxy(proxy: str | None):
    """Set proxy environment variables for httpx (used by twikit internally)."""
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy


def build_query(keyword: str, min_likes: int = 0) -> str:
    """Build X search query with optional server-side filters."""
    parts = [keyword]
    if min_likes > 0:
        parts.append(f"min_faves:{min_likes}")
    return " ".join(parts)


def tweet_to_dict(tweet) -> dict:
    """Convert a twikit Tweet object to a serializable dict."""
    author = tweet.author if hasattr(tweet, "author") else None
    return {
        "tweet_id": tweet.id,
        "url": f"https://x.com/i/status/{tweet.id}",
        "text": tweet.text,
        "created_at": tweet.created_at,
        "favorite_count": tweet.favorite_count,
        "retweet_count": tweet.retweet_count,
        "reply_count": tweet.reply_count,
        "quote_count": tweet.quote_count,
        "view_count": tweet.view_count,
        "bookmark_count": tweet.bookmark_count if hasattr(tweet, "bookmark_count") else None,
        "hashtags": tweet.hashtags if hasattr(tweet, "hashtags") else [],
        "media": [
            {
                "type": m.type if hasattr(m, "type") else "unknown",
                "url": m.media_url_https if hasattr(m, "media_url_https") else str(m),
            }
            for m in (tweet.media if hasattr(tweet, "media") and tweet.media else [])
        ],
        "author": {
            "id": author.id if author else None,
            "name": author.name if author else None,
            "screen_name": author.screen_name if author else None,
            "followers_count": author.followers_count if author else None,
            "following_count": author.following_count if author else None,
            "statuses_count": author.statuses_count if author else None,
            "description": author.description if author else None,
            "profile_image_url": author.profile_image_url_https if author else None,
            "is_verified": author.is_blue_verified if author else None,
        } if author else None,
    }


def passes_filters(tweet, min_likes: int, min_views: int, min_followers: int) -> bool:
    """Check if a tweet passes all client-side filter criteria."""
    if tweet.favorite_count < min_likes:
        return False
    if min_views > 0:
        if tweet.view_count is None or tweet.view_count < min_views:
            return False
    if min_followers > 0:
        author = tweet.author if hasattr(tweet, "author") else None
        if author is None or author.followers_count is None or author.followers_count < min_followers:
            return False
    return True


async def scrape(
    query: str,
    min_likes: int = 50,
    min_views: int = 1000,
    min_followers: int = 1000,
    max_pages: int = 5,
    product: str = "Top",
    delay: float = 2.0,
    proxy: str | None = DEFAULT_PROXY,
    cookies_path: Path = DEFAULT_COOKIES,
    output_path: Path = DEFAULT_OUTPUT,
) -> list[dict]:
    """Run the scraping pipeline."""
    setup_proxy(proxy)
    # Extended timeout needed for proxy connections; twikit's default is too short
    client = Client("en-US", proxy=proxy, timeout=httpx.Timeout(30.0, read=60.0))

    # Authenticate via cookies
    if not cookies_path.exists():
        print(f"Error: No cookies found at {cookies_path}")
        print("Run `python scripts/auth_setup.py` first to authenticate.")
        sys.exit(1)

    print(f"Loading cookies from {cookies_path}")
    client.load_cookies(str(cookies_path))

    # Build search query with server-side pre-filtering
    search_query = build_query(query, min_likes=min_likes)
    print(f"Search query: {search_query}")
    print(f"Filters: likes>={min_likes}, views>={min_views}, followers>={min_followers}")

    all_results = []
    seen_ids = set()

    # First page
    print(f"Fetching page 1 (product={product})...")
    try:
        result = await client.search_tweet(search_query, product=product, count=100)
    except Exception as e:
        print(f"Error on page 1: {e}")
        sys.exit(1)

    page_num = 1

    while True:
        page_tweets = []
        for tweet in result:
            if tweet.id in seen_ids:
                continue
            seen_ids.add(tweet.id)
            if passes_filters(tweet, min_likes, min_views, min_followers):
                page_tweets.append(tweet_to_dict(tweet))

        all_results.extend(page_tweets)
        print(f"  Page {page_num}: {len(result)} fetched, {len(page_tweets)} passed filters, total: {len(all_results)}")

        if page_num >= max_pages:
            print(f"Reached max pages ({max_pages})")
            break

        # Try next page
        try:
            result = await result.next()
            if result is None:
                print("No more results available")
                break
        except Exception as e:
            print(f"Pagination ended: {e}")
            break

        page_num += 1
        print(f"Fetching page {page_num}...")
        await asyncio.sleep(delay)

    # Sort by favorite_count descending
    all_results.sort(key=lambda t: t["favorite_count"], reverse=True)

    # Save output
    output_data = {
        "scrape_time": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "search_query": search_query,
        "filters": {
            "min_likes": min_likes,
            "min_views": min_views,
            "min_followers": min_followers,
        },
        "total_filtered": len(all_results),
        "tweets": all_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\nResults saved to {output_path}")
    print(f"Total tweets matching filters: {len(all_results)}")
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Scrape X/Twitter tweets with filters")
    parser.add_argument("--query", required=True, help='Search keyword (e.g., "veo prompt")')
    parser.add_argument("--min-likes", type=int, default=50, help="Minimum likes threshold (default: 50)")
    parser.add_argument("--min-views", type=int, default=1000, help="Minimum views threshold (default: 1000)")
    parser.add_argument("--min-followers", type=int, default=1000, help="Minimum author followers threshold, 0 to skip (default: 1000)")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pagination pages (default: 5)")
    parser.add_argument("--product", choices=["Top", "Latest", "Media"], default="Top", help="Search result type (default: Top)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between pages in seconds (default: 2.0)")
    parser.add_argument("--proxy", type=str, default=DEFAULT_PROXY, help=f"HTTP proxy URL (default: {DEFAULT_PROXY}, set to '' to disable)")
    parser.add_argument("--cookies", type=str, default=str(DEFAULT_COOKIES), help="Path to cookies.json")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output JSON path")

    args = parser.parse_args()

    asyncio.run(scrape(
        query=args.query,
        min_likes=args.min_likes,
        min_views=args.min_views,
        min_followers=args.min_followers,
        max_pages=args.max_pages,
        product=args.product,
        delay=args.delay,
        proxy=args.proxy if args.proxy else None,
        cookies_path=Path(args.cookies),
        output_path=Path(args.output),
    ))


if __name__ == "__main__":
    main()
