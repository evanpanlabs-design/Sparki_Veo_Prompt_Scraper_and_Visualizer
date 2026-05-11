---
name: x-veo-prompt-scraper
description: >
  Search X/Twitter for Veo/AI video generation prompts across multiple related queries,
  filter by engagement and author quality, exclude competing model mentions (unless Veo is present),
  extract structured generation Prompts via LLM, output a curated prompt list.
trigger-phrases:
  - "scrape X prompts"
  - "find veo prompts"
  - "collect video prompts"
  - "X prompt scraper"
  - "extract prompts from X"
  - "veo prompt collector"
  - "scrape AI video prompts"
---

# X Veo Prompt Scraper — Two-Phase Pipeline

A two-phase pipeline that scrapes X/Twitter for AI generation prompts and extracts them as structured, usable prompt texts.

**Phase 1** — Multi-query scrape: runs multiple searches, deduplicates, filters by engagement and author quality.
**Phase 2** — LLM extraction: classifies each tweet as a usable Prompt or general commentary, extracts the prompt text, title, and category.

## When to Use

- User wants to collect Veo/video generation prompts from X
- User wants a curated prompt list sorted by engagement/author quality
- User wants to extract structured prompts (video-generation, cinematic, character-design, etc.)
- User asks to scrape AI video prompts, find veo prompts, or collect X prompts

## Prerequisites

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Or manually: `pip install playwright twikit pyyaml requests`

### 2. Install Playwright Browsers

```bash
playwright install chromium
```

### 3. Authenticate (First Time Only)

```bash
python scripts/browser_auth.py
```

This opens a Chromium browser for X login. At the email verification step, use `--email` if prompted:

```bash
python scripts/browser_auth.py --email your_email@example.com
```

Cookies are saved to `outputs/cookies.json` and reused on subsequent runs.

### 4. Set LLM API Key (Phase 2 only)

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_API_BASE="https://api.openai.com/v1"   # or your proxy endpoint
export LLM_MODEL="gpt-4o"                              # default: gpt-4o
```

---

## Phase 1 — Multi-Query Scrape

### Configuration

Edit `configs/x-scraper.yaml` to add/remove queries and negative keywords — no code changes needed:

```yaml
queries:
  - "veo prompt"
  - "Veo 3 prompt"
  - "Gemini Veo prompt"
  - "Google Veo 3"
  - "Veo 3.1 prompt"

negative_keywords:
  - "ChatGPT"
  - "Doubao"
  - "Seedance"
  - "Kling"
  - "Sora"
  - "Runway"
  - "Pika"
  - "Luma Dream"
  - "Hailuo AI"

engagement:
  min_likes: 50
  min_followers: 1000
```

**How negative filtering works**: A tweet is excluded if it contains any negative keyword **AND** does not also mention "veo" or "gemini". This keeps tweets that compare Veo with competitors while filtering pure competitor posts.

### Run Phase 1

```bash
python scripts/x_multi_search.py
python scripts/x_multi_search.py --config configs/x-scraper.yaml --output outputs/raw_tweets.json
```

Output: `outputs/raw_tweets.json` — deduplicated tweets passing engagement filters.

### Phase 1 Output Schema

```json
{
  "scrape_time": "2026-05-11T14:30:00+00:00",
  "queries": ["veo prompt", "Veo 3 prompt", ...],
  "filters": {
    "min_likes": 50,
    "min_followers": 1000,
    "negative_keywords": ["ChatGPT", "Kling", ...]
  },
  "total_deduped": 47,
  "tweets": [
    {
      "tweet_id": "1234567890",
      "url": "https://x.com/i/status/1234567890",
      "text": "Created with Veo 3.1\n\nPrompt:\nUltra-realistic cinematic video, 4K...",
      "created_at": "2026-05-10T18:22:00.000Z",
      "favorite_count": 152,
      "retweet_count": 34,
      "reply_count": 12,
      "view_count": 15420,
      "author": {
        "name": "AI Creator",
        "screen_name": "aicreator",
        "followers_count": 5200
      }
    }
  ]
}
```

---

## Phase 2 — LLM Prompt Extraction

```bash
python scripts/extract_prompts.py
python scripts/extract_prompts.py --input outputs/raw_tweets.json --output outputs/prompts.json --model gpt-4o
```

### Category Definitions

| Category | Description |
|----------|-------------|
| `video-generation` | Prompt for AI video models (Veo, Sora, Kling) — may include motion, camera movement, duration |
| `image-generation` | Prompt for still image models (Midjourney, DALL-E, Flux) |
| `cinematic` | Video/image prompt with film-specific terms (f-stop, depth of field, film grain, aspect ratio) |
| `character-design` | Prompt focused on consistent character figures for animation/comics |
| `product-photography` | Prompt for realistic product shots, commercial advertising style |
| `other` | Prompt that doesn't fit above categories |

### Phase 2 Output Schema

```json
{
  "extract_time": "2026-05-11T15:00:00+00:00",
  "llm_model": "gpt-4o",
  "total_input": 47,
  "total_extracted": 12,
  "prompts": [
    {
      "tweet_id": "1234567890",
      "url": "https://x.com/i/status/1234567890",
      "category": "video-generation",
      "title": "Psychological horror forest cabin",
      "prompt_text": "Ultra-realistic cinematic video, 4K, 24fps, psychological horror style, foggy forest, abandoned cabin, single torch light",
      "notes": "Explicit Veo prompt with scene description and technical parameters.",
      "author": {
        "name": "AI Creator",
        "screen_name": "aicreator",
        "followers": 5200
      },
      "engagement": {
        "likes": 152,
        "retweets": 34,
        "replies": 12,
        "views": 15420
      }
    }
  ]
}
```

---

## Full Pipeline

```bash
# Phase 1: scrape and filter
python scripts/x_multi_search.py

# Phase 2: extract prompts via LLM
python scripts/extract_prompts.py --input outputs/raw_tweets.json --output outputs/prompts.json
```

---

## Workflow Orchestration (for Claude Code agent)

1. **Read config**: Load `configs/x-scraper.yaml` — get queries list and negative_keywords
2. **Phase 1**: Run `x_multi_search.py` → produces `outputs/raw_tweets.json`
3. **Verify Phase 1 output**: Check that tweets array is non-empty
4. **Set LLM env vars**: Confirm `OPENAI_API_KEY` is set; if not, prompt user
5. **Phase 2**: Run `extract_prompts.py` → produces `outputs/prompts.json`
6. **Present results**: Summarize total tweets scraped, prompts extracted, category breakdown

---

## Error Handling

### Phase 1 Errors

| Error | Solution |
|-------|----------|
| "Could not load search page" | X is showing a block page — wait a few minutes and retry |
| "No tweets found" | Lower `min_likes` or `min_followers` in config; try broader queries |
| Browser crashes | Reduce `--max-scrolls` or increase delays; check proxy stability |
| "Something went wrong" reload | X rate-limiting — add longer delays or reduce query count |

### Phase 2 Errors

| Error | Solution |
|-------|----------|
| "OPENAI_API_KEY not set" | Set `OPENAI_API_KEY` env var before running Phase 2 |
| LLM returns non-JSON | Script logs error and skips the tweet; check API key and model |
| Rate limit from LLM API | Reduce call frequency or use a slower model |
| Empty prompts output | Increase `min_likes` in Phase 1 to filter low-quality tweets that LLM skips |

---

## Security Notes

- Credentials used only in `browser_auth.py`, never stored
- Session cookies saved to `outputs/cookies.json` — do NOT commit to version control
- `cookies.json` is in `.gitignore`
- Use a secondary X account for scraping to protect your primary account
