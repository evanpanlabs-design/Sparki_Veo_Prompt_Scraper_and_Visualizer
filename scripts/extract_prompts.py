"""
Prompt extraction via LLM — reads raw_tweets.json, calls OpenAI-compatible API
concurrently for each tweet, classifies whether it contains a usable generation
Prompt, and extracts title/category/prompt_text.

Usage:
    python scripts/extract_prompts.py
    python scripts/extract_prompts.py --input outputs/raw_tweets.json --output outputs/prompts.json --concurrency 15
"""

import argparse
import json
import os
import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "raw_tweets.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "prompts.json"
DEFAULT_MODEL = "MiniMax-M2.7"
DEFAULT_CONCURRENCY = 15
SYSTEM_PROMPT = 'You are a prompt extraction specialist for AI video and image generation. Respond with valid JSON only, no markdown, no explanation: {"is_prompt": true or false, "category": "video-generation" | "image-generation" | "cinematic" | "character-design" | "product-photography" | "other" | null, "title": "short title" or null, "prompt_text": "text" or null, "notes": "note" or null}.'


def load_tweets(input_path: Path) -> list[dict]:
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tweets", [])


def call_llm(
    text: str,
    api_base: str,
    api_key: str,
    model: str,
    timeout: int = 60,
) -> Optional[dict]:
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Tweet:\n{text}"},
        ],
        "temperature": 0.0,
        "max_tokens": 500,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None
        raw = resp.json()["choices"][0]["message"]["content"]
        # Strip </think> thinking blocks to isolate the JSON response
        json_text = raw
        last_brace = json_text.rfind("}")
        first_brace = json_text.find("{")
        if first_brace != -1 and last_brace != -1 and last_brace >= first_brace:
            json_text = json_text[first_brace : last_brace + 1]
        return json.loads(json_text)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def process_tweet(tweet: dict, api_base: str, api_key: str, model: str) -> tuple[dict, bool]:
    """Returns (prompt_dict, was_extracted)"""
    screen = tweet.get("author", {}).get("screen_name", "?")
    result = call_llm(tweet.get("text", ""), api_base, api_key, model)

    if result is None or not result.get("is_prompt", False):
        return None, False

    return {
        "tweet_id":     tweet.get("tweet_id", ""),
        "url":          tweet.get("url", ""),
        "category":     result.get("category"),
        "title":        result.get("title"),
        "prompt_text":  result.get("prompt_text"),
        "notes":        result.get("notes"),
        "author": {
            "name":        tweet.get("author", {}).get("name", ""),
            "screen_name": screen,
            "followers":   tweet.get("author", {}).get("followers_count", 0),
        },
        "engagement": {
            "likes":    tweet.get("favorite_count", 0),
            "retweets": tweet.get("retweet_count", 0),
            "replies":  tweet.get("reply_count", 0),
            "views":    tweet.get("view_count", 0),
        },
    }, True


def extract_prompts(
    input_path: Path,
    output_path: Path,
    api_base: Optional[str],
    api_key: Optional[str],
    model: str,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[dict]:
    tweets = load_tweets(input_path)
    total = len(tweets)
    print(f"Loaded {total} tweets from {input_path}")
    print(f"Concurrency: {concurrency}")

    if not tweets:
        _save_output(output_path, [], [], model)
        return []

    api_base = api_base or os.environ.get("OPENAI_API_BASE", "https://api.minimaxi.com/v1")
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set. Create a .env file from .env.example and add your API key.")
        sys.exit(1)

    prompts = []
    done = 0
    lock = Lock()

    def on_result(wid: int, tweet: dict, result_tuple: tuple):
        nonlocal done, prompts
        prompt_dict, was_extracted = result_tuple
        with lock:
            done += 1
            print(f"[{done}/{total}] {tweet.get('author', {}).get('screen_name', '?')} -> {'EXTRACTED' if was_extracted else 'skipped'}")
            if was_extracted:
                prompts.append(prompt_dict)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {}
        for i, tweet in enumerate(tweets):
            screen = tweet.get("author", {}).get("screen_name", "?")
            print(f"  Queuing [{i+1}/{total}] @{screen}...")
            fut = ex.submit(process_tweet, tweet, api_base, api_key, model)
            futures[fut] = (i, tweet)

        for fut in as_completed(futures):
            i, tweet = futures[fut]
            try:
                result_tuple = fut.result()
            except Exception as e:
                result_tuple = (None, False)
            on_result(i, tweet, result_tuple)

    print(f"\nExtracted {len(prompts)} prompts from {total} tweets")
    _save_output(output_path, prompts, tweets, model)
    return prompts


def _save_output(output_path: Path, prompts: list[dict], tweets: list[dict], model: str):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "extract_time":    datetime.now(timezone.utc).isoformat(),
        "llm_model":       model,
        "concurrency":     DEFAULT_CONCURRENCY,
        "total_input":     len(tweets),
        "total_extracted": len(prompts),
        "prompts":         prompts,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved to {output_path}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract generation Prompts from scraped tweets via LLM (concurrent)")
    parser.add_argument("--input",       type=str, default=str(DEFAULT_INPUT),
                        help="Input JSON from x_multi_search.py")
    parser.add_argument("--output",      type=str, default=str(DEFAULT_OUTPUT),
                        help="Output JSON with extracted prompts")
    parser.add_argument("--api-base",    type=str, default=None,
                        help="API base URL (or OPENAI_API_BASE env var)")
    parser.add_argument("--api-key",     type=str, default=None,
                        help="API key (or OPENAI_API_KEY env var)")
    parser.add_argument("--model",        type=str, default=DEFAULT_MODEL,
                        help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--concurrency",  type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Number of parallel LLM calls (default: {DEFAULT_CONCURRENCY})")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    print("=" * 60)
    print("Prompt Extraction via LLM (Concurrent)")
    print("=" * 60)
    print(f"  Input:       {input_path}")
    print(f"  Output:      {output_path}")
    print(f"  Model:       {args.model}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  API:         {args.api_base or 'from env'}")
    print("=" * 60 + "\n")

    extract_prompts(
        input_path=input_path,
        output_path=output_path,
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        concurrency=args.concurrency,
    )


if __name__ == "__main__":
    main()
