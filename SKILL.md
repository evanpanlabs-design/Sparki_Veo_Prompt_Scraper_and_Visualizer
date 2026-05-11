---
name: x-scraper
description: Scrape X/Twitter tweets by keyword with engagement filters (likes, views, author followers). Use when the user wants to search X/Twitter content, find viral tweets, analyze engagement metrics, or collect tweets matching specific criteria. Triggers on: "scrape X", "search Twitter", "find tweets", "X scraper", "tweet search", "爬取X", "搜索推特", "找推文".
---

# X/Twitter Scraper

Search and filter X/Twitter (Twitter) tweets by keyword with engagement-based filters using the twikit library.

## When to Use

- User wants to find tweets containing specific keywords (e.g., "veo prompt", "AI art")
- User needs tweets filtered by engagement metrics (likes, views, retweets)
- User wants to filter by author follower count
- User asks to scrape, search, or collect X/Twitter content
- User wants to analyze trending topics or viral content on X

## Prerequisites

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Or manually: `pip install twikit>=2.3.0`

### 2. Authenticate (First Time Only)

The scraper requires X/Twitter account credentials for authentication. This is a one-time setup:

```bash
python scripts/auth_setup.py
```

This will prompt for:
- Username
- Email (optional, helps bypass some challenges)
- Password

Session cookies are saved to `outputs/cookies.json` — subsequent runs use cookies only, no password needed.

**If cookies already exist**, skip this step.

## Invocation

### Basic Search

```bash
python scripts/x_scraper.py --query "veo prompt"
```

Uses default filters: likes >= 50, views >= 1000, followers >= 1000.

### Custom Filters

```bash
python scripts/x_scraper.py --query "veo prompt" --min-likes 100 --min-views 5000 --min-followers 5000
```

### Set Followers Filter to 0 (Skip Follower Check)

```bash
python scripts/x_scraper.py --query "veo prompt" --min-followers 0
```

### Search Latest Tweets Instead of Top

```bash
python scripts/x_scraper.py --query "veo prompt" --product Latest
```

### More Pages, Custom Output

```bash
python scripts/x_scraper.py --query "veo prompt" --max-pages 10 --output outputs/veo_results.json
```

### All Options

| Flag | Default | Description |
|------|---------|-------------|
| `--query` | required | Search keyword or phrase |
| `--min-likes` | 50 | Minimum likes threshold |
| `--min-views` | 1000 | Minimum views threshold (0 to skip) |
| `--min-followers` | 1000 | Minimum author followers (0 to skip) |
| `--max-pages` | 5 | Max pagination pages |
| `--product` | Top | Result type: `Top`, `Latest`, `Media` |
| `--delay` | 2.0 | Seconds between page requests |
| `--cookies` | outputs/cookies.json | Cookie file path |
| `--output` | outputs/results.json | Output JSON path |

## Output Schema

The output JSON has this structure:

```json
{
  "scrape_time": "2024-01-15T10:30:00+00:00",
  "query": "veo prompt",
  "search_query": "veo prompt min_faves:50",
  "filters": {
    "min_likes": 50,
    "min_views": 1000,
    "min_followers": 1000
  },
  "total_filtered": 23,
  "tweets": [
    {
      "tweet_id": "1234567890",
      "url": "https://x.com/i/status/1234567890",
      "text": "Check out this Veo prompt...",
      "created_at": "2024-01-14T18:22:00.000Z",
      "favorite_count": 152,
      "retweet_count": 34,
      "reply_count": 12,
      "quote_count": 8,
      "view_count": 15420,
      "bookmark_count": 45,
      "hashtags": ["veo", "aivideo"],
      "media": [{"type": "photo", "url": "https://..."}],
      "author": {
        "id": "9876543210",
        "name": "AI Creator",
        "screen_name": "aicreator",
        "followers_count": 5200,
        "following_count": 340,
        "statuses_count": 8900,
        "description": "Making AI videos",
        "profile_image_url": "https://...",
        "is_verified": true
      }
    }
  ]
}
```

## Workflow

1. **Check prerequisites**: Verify `twikit` is installed and `outputs/cookies.json` exists
2. **If no cookies**: Run `python scripts/auth_setup.py` first
3. **Run scraper**: Execute `python scripts/x_scraper.py --query "<keyword>"` with desired filters
4. **Read results**: Parse the output JSON file to analyze the scraped tweets
5. **Summarize**: Present key findings to the user (top tweets by engagement, notable authors, etc.)

## Common Issues

### "No cookies found"
Run `python scripts/auth_setup.py` to authenticate first.

### Rate Limited (TooManyRequests)
X limits search to ~50 requests per 15 minutes. Increase `--delay` to 3-5 seconds, or reduce `--max-pages`.

### Empty Results
- Try `--product Latest` instead of `Top` for recent tweets
- Lower `--min-likes` or `--min-views` thresholds
- The keyword may not have matching content — try broader terms

### Account Locked
Too many requests may trigger X's anti-abuse system. Wait 24-48 hours, or use a secondary account.

### `view_count` is None
Some users disable view counts. The scraper treats `None` view counts as not passing the `--min-views` filter. Lower `--min-views` to 0 if this excludes too many results.

## Advanced: X Search Operators

The `--query` parameter supports X's advanced search syntax for server-side filtering:

```bash
# Exclude retweets
python scripts/x_scraper.py --query "veo prompt -filter:retweets"

# Date range
python scripts/x_scraper.py --query "veo prompt since:2024-06-01 until:2024-12-31"

# Only tweets with media
python scripts/x_scraper.py --query "veo prompt filter:media"

# From specific user
python scripts/x_scraper.py --query "veo prompt from:google"

# Language filter
python scripts/x_scraper.py --query "veo prompt lang:en"
```

Note: `min_faves:N` is automatically appended to the query based on `--min-likes` for server-side pre-filtering.

## Security Notes

- Credentials are used only during `auth_setup.py` and are never stored
- Only session cookies are persisted in `outputs/cookies.json`
- Do NOT commit `cookies.json` to version control (it's in `.gitignore`)
- Use a secondary/dedicated X account for scraping to protect your primary account
