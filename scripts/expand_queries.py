"""
Query Expansion Tool — uses LLM to generate new search queries based on historical yield.

Reads query_stats from the latest scrape in DB, finds top-performing queries by prompt count,
and asks the LLM to generate 5-10 new related search terms.

Usage:
    python scripts/expand_queries.py                    # dry-run: show suggestions
    python scripts/expand_queries.py --write            # overwrite configs/x-scraper.yaml
    python scripts/expand_queries.py --scrape-id 3     # target specific scrape
    python scripts/expand_queries.py --top-n 3          # use top 3 queries as seed
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import os

from dotenv import load_dotenv
load_dotenv()

from scripts.db import init_db, get_all_scrapes, get_scrape_query_stats

DEFAULT_TOP_N = 5
DEFAULT_NEW_COUNT = 8


def get_top_queries(scrape_id: int = None, top_n: int = DEFAULT_TOP_N) -> list[dict]:
    """Fetch query_stats from DB, return top-N queries sorted by prompts descending."""
    init_db()
    scrapes = get_all_scrapes()
    if not scrapes:
        print("No scrapes found in DB.")
        return []

    target = scrapes[0] if scrape_id is None else next(
        (s for s in scrapes if s["id"] == scrape_id), scrapes[0]
    )

    stats_raw = target.get("query_stats", "")
    if not stats_raw:
        print(f"No query_stats for scrape_id={target['id']}")
        return []

    try:
        stats = json.loads(stats_raw) if isinstance(stats_raw, str) else stats_raw
    except json.JSONDecodeError:
        print(f"Invalid query_stats JSON for scrape_id={target['id']}")
        return []

    # Sort by prompts desc, then dedup desc
    sorted_queries = sorted(
        stats.items(),
        key=lambda x: (x[1].get("prompts", 0), x[1].get("dedup", 0)),
        reverse=True,
    )[:top_n]

    return [
        {"query": q, "prompts": v.get("prompts", 0), "tweets": v.get("dedup", 0)}
        for q, v in sorted_queries
    ]


def build_expansion_prompt(top_queries: list[dict], new_count: int = DEFAULT_NEW_COUNT) -> str:
    """Build the LLM prompt for query expansion."""
    query_lines = "\n".join(
        f'  - "{q["query"]}" → {q["prompts"]} prompts, {q["tweets"]} tweets'
        for q in top_queries
    )
    return (
        "You are a search query strategist for AI video generation content on X/Twitter.\n\n"
        f"Top-performing queries (by prompt yield):\n{query_lines}\n\n"
        "Generate exactly **10** new search queries that are likely to find more AI video generation prompts.\n"
        "Rules:\n"
        "  - Each query must be 2-6 words (short, punchy, X-friendly)\n"
        "  - Mix of: variations, synonyms, new model names, use-case terms\n"
        "  - Do NOT repeat any existing query exactly\n"
        "  - Include some queries for: new Veo versions, specific styles (cinematic, fashion, product), error/quality keywords\n"
        "  - Include queries about: prompting techniques, prompt sharing, showcase/comparison tweets\n\n"
        "Reply with ONLY a JSON array of query strings:\n"
        '["query 1", "query 2", ...]'
    )


def call_llm(prompt: str, api_base: str, api_key: str, model: str) -> list[str]:
    """Call LLM and return list of query strings."""
    import requests
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Reply with only JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 300,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text}")

    content = resp.json()["choices"][0]["message"]["content"]
    # Extract JSON array from response
    first_bracket = content.find("[")
    last_bracket = content.rfind("]")
    if first_bracket != -1 and last_bracket != -1:
        return json.loads(content[first_bracket : last_bracket + 1])
    return json.loads(content)


def expand_queries(scrape_id: int = None, top_n: int = DEFAULT_TOP_N,
                   dry_run: bool = True, write: bool = False) -> list[str]:
    """
    Main expansion logic. Returns list of new query strings.
    If dry_run=True, only returns suggestions. If write=True, updates x-scraper.yaml.
    """
    init_db()

    api_base = os.environ.get("OPENAI_API_BASE", "https://api.minimaxi.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "MiniMax-M2.7")

    if not api_key:
        print("ERROR: OPENAI_API_KEY not set. Create .env from .env.example.")
        return []

    top_queries = get_top_queries(scrape_id=scrape_id, top_n=top_n)
    if not top_queries:
        return []

    print(f"Top {len(top_queries)} queries:")
    for q in top_queries:
        print(f"  '{q['query']}' → {q['prompts']} prompts, {q['tweets']} tweets")

    print(f"\nAsking LLM to generate {DEFAULT_NEW_COUNT} new queries...")
    expansion_prompt = build_expansion_prompt(top_queries, DEFAULT_NEW_COUNT)
    new_queries = call_llm(expansion_prompt, api_base, api_key, model)

    print(f"\nSuggested new queries ({len(new_queries)}):")
    for q in new_queries:
        print(f"  + {q}")

    if dry_run and not write:
        print("\n(Dry run — no files written. Use --write to update configs/x-scraper.yaml)")
        return new_queries

    if write:
        config_path = PROJECT_ROOT / "configs" / "x-scraper.yaml"
        import yaml
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        existing = cfg.get("queries", [])
        all_queries = existing + [q for q in new_queries if q not in existing]
        cfg["queries"] = all_queries
        config_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
        print(f"\n[x-scraper.yaml updated] existing={len(existing)} new={len(new_queries)} total={len(all_queries)}")
        for q in new_queries:
            print(f"  + {q}")
        return new_queries

    return new_queries


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Expand search queries using LLM analysis of historical yield data"
    )
    parser.add_argument("--scrape-id", type=int, default=None,
                        help="Use specific scrape (default: latest)")
    parser.add_argument("--top-n",    type=int, default=DEFAULT_TOP_N,
                        help=f"Number of top queries to feed to LLM (default: {DEFAULT_TOP_N})")
    parser.add_argument("--dry-run",  action="store_true", default=False,
                        help="Show suggestions without writing files")
    parser.add_argument("--write",    action="store_true",
                        help="Write new queries to configs/x-scraper.yaml")
    args = parser.parse_args()

    print("=" * 60)
    print("Query Expansion Tool")
    print("=" * 60)
    print(f"  Scrape ID:  {args.scrape_id or 'latest'}")
    print(f"  Top-N:      {args.top_n}")
    print(f"  Mode:       {'DRY RUN' if not args.write else 'WRITE'}")
    print("=" * 60 + "\n")

    expand_queries(
        scrape_id=args.scrape_id,
        top_n=args.top_n,
        dry_run=not args.write,
        write=args.write,
    )


if __name__ == "__main__":
    main()