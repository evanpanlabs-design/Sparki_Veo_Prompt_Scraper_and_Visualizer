---
name: x-veo-prompt-scraper
description: >
  Search X/Twitter for Veo/AI video generation prompts across multiple related queries,
  filter by engagement and author quality, exclude competing model mentions (unless Veo is present),
  extract structured generation Prompts via LLM, generate a cover image for each prompt via
  Gemini 2.5 Flash Image (Vertex AI), upload to GCS, output a curated prompt list with image URLs.
trigger-phrases:
  - "scrape X prompts"
  - "find veo prompts"
  - "collect video prompts"
  - "X prompt scraper"
  - "extract prompts from X"
  - "veo prompt collector"
  - "scrape AI video prompts"
  - "generate cover images"
---

# X Veo Prompt Scraper — Three-Phase Pipeline

A three-phase pipeline that scrapes X/Twitter for AI generation prompts, extracts them as structured prompt texts, and generates a cover image for each prompt.

**Phase 1** — Multi-query scrape: runs multiple searches, deduplicates, filters by engagement and author quality.
**Phase 2** — LLM extraction: classifies each tweet as a usable Prompt or general commentary, extracts the prompt text, title, and category.
**Phase 3** — Image generation: generates a cover image for each extracted prompt using Gemini 2.5 Flash Image via Vertex AI, uploads to GCS, writes URL back to DB.

## When to Use

- User wants to collect Veo/video generation prompts from X
- User wants a curated prompt list sorted by engagement/author quality
- User wants to extract structured prompts (video-generation, cinematic, character-design, etc.)
- User asks to scrape AI video prompts, find veo prompts, or collect X prompts

## Prerequisites

### 0. Configure API Key (Required before Phase 2)

Create a `.env` file in the project root:

```bash
# 1. Copy the example
cp .env.example .env

# 2. Edit with your MiniMax credentials
#    Get your API key from https://platform.minimaxi.com
```

Then edit `.env`:
```bash
OPENAI_API_KEY=sk-your-api-key-here
OPENAI_API_BASE=https://api.minimaxi.com/v1
LLM_MODEL=MiniMax-M2.7
```

> **Never commit `.env`** — it's in `.gitignore`. If git still tracks it, run `git rm --cached .env`.

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Or manually: `pip install playwright twikit pyyaml requests python-dotenv google-genai google-cloud-storage Pillow`

### 2. Install Playwright Browsers

```bash
playwright install chromium
```

### 3. Authenticate X (First Time Only)

```bash
python scripts/browser_auth.py
```

This opens a Chromium browser for X login. At the email verification step, use `--email` if prompted:

```bash
python scripts/browser_auth.py --email your_email@example.com
```

Cookies are saved to `outputs/cookies.json` and reused on subsequent runs.

### 4. Authenticate GCP (Required for Phase 3)

```bash
gcloud auth login
gcloud auth application-default login
```

This configures Application Default Credentials (ADC) used by `google-genai` and `google-cloud-storage` libraries. The user has been granted access to project `sparki-2` and bucket `sparki-market-test`.

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
| `cinematic` | Video/image prompt with film-specific terms (lens, f-stop, depth of field, film grain, aspect ratio) |
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

## Phase 3 — Cover Image Generation

Generates a cover image for each extracted prompt using **Gemini 2.5 Flash Image** via Vertex AI. Images are resized to **2752×1536 px** (16:9) and uploaded to GCS.

### Image Prompt Strategy

The system builds a category-aware image prompt:
- Injects **3 random style keywords** from the category's keyword pool
- Guides aspect ratio per category: cinematic→21:9, character→2:3, product→3:2, image→1:1, video→16:9
- Tells Gemini to generate a "cover image" matching the prompt
- **Output is always resized to 2752×1536 px** after generation

| Category | Aspect Ratio | Style Keywords (sample) |
|----------|-------------|------------------------|
| `cinematic` | 21:9 | film grain, anamorphic, f-stop, lens flare |
| `character-design` | 2:3 | character sheet, turnaround, expression sheet |
| `product-photography` | 3:2 | product shot, studio lighting, white background |
| `image-generation` | 1:1 | detailed illustration, 8k, masterpiece |
| `video-generation` | 16:9 | motion blur, dynamic pose, film still |
| `other` | 16:9 | high quality, vibrant colors |

### Run Phase 3

```bash
# Dry run — see which prompts would be processed
python scripts/generate_images.py --dry-run

# Local-only (test mode, no GCS upload)
python scripts/generate_images.py --local-only --limit 3

# Full GCS upload (concurrency=5, with 429 retry)
python scripts/generate_images.py --scrape-id 1

# Conservative (single-threaded, avoid rate limits)
python scripts/generate_images.py --scrape-id 1 --concurrency 1
```

### GCS Output Path

```
gs://sparki-market-test/prompts/{prompt_id}.png
```

Example: `gs://sparki-market-test/prompts/42.png`

> The prompt `id` is a global auto-increment primary key — unique across all scrapes. One prompt always equals one image, no collisions.

---

## Full Pipeline

```bash
# Phase 1: scrape and filter
python scripts/x_multi_search.py

# Phase 2: extract prompts via LLM
python scripts/extract_prompts.py --scrape-id 1

# Phase 3: generate cover images via Gemini 2.5 Flash Image
python scripts/generate_images.py --scrape-id 1
```

---

## Database Schema

SQLite database at `data/veo_prompts.db`. All tables are created automatically on first run.

```sql
-- Each Phase 1 run
CREATE TABLE scrapes (
    id           INTEGER PRIMARY KEY,
    scrape_time  TEXT,
    queries      TEXT,      -- JSON array
    config_yaml  TEXT,      -- snapshot of x-scraper.yaml
    total_raw    INTEGER,
    total_dedup  INTEGER
);

-- Phase 1 output: raw tweets (deduplicated by tweet_id)
CREATE TABLE tweets (
    id               INTEGER PRIMARY KEY,
    scrape_id        INTEGER,
    tweet_id         TEXT UNIQUE,   -- X's original tweet ID
    url              TEXT,
    text             TEXT,
    created_at       TEXT,
    favorite_count   INTEGER DEFAULT 0,
    retweet_count    INTEGER DEFAULT 0,
    reply_count      INTEGER DEFAULT 0,
    view_count       INTEGER DEFAULT 0,
    author_name      TEXT,
    author_screen    TEXT,
    followers_count  INTEGER DEFAULT 0
);

-- Phase 2 output: extracted prompts
CREATE TABLE prompts (
    id               INTEGER PRIMARY KEY,
    tweet_id         TEXT,
    scrape_id        INTEGER,
    url              TEXT,
    category         TEXT,          -- approved category name
    title            TEXT,
    prompt_text      TEXT,
    notes            TEXT,
    author_name      TEXT,
    author_screen    TEXT,
    followers_count  INTEGER DEFAULT 0,
    likes_count      INTEGER DEFAULT 0,
    retweet_count    INTEGER DEFAULT 0,
    reply_count      INTEGER DEFAULT 0,
    view_count       INTEGER DEFAULT 0,
    extracted_at     TEXT,
    image_gcs_url    TEXT,          -- Phase 3: gs:// URL after image generation
    image_generated_at TEXT         -- Phase 3: timestamp
);

-- Approved category definitions
CREATE TABLE categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE,        -- e.g. "video-generation"
    description TEXT,
    examples    TEXT,               -- JSON array of prompt_text examples
    created_at  TEXT,
    updated_at  TEXT,
    is_active   INTEGER DEFAULT 1   -- 0 = deprecated
);

-- LLM-suggested new categories (pending human approval)
CREATE TABLE category_suggestions (
    id              INTEGER PRIMARY KEY,
    suggested_name  TEXT,
    suggested_desc  TEXT,
    reason          TEXT,           -- why LLM thinks this is a new category
    suggested_by    TEXT,           -- model name
    prompt_text     TEXT,           -- triggering tweet/prompt text
    status          TEXT DEFAULT 'pending',  -- pending | approved | rejected
    created_at      TEXT,
    reviewed_at     TEXT,
    reviewed_by     TEXT
);
```

### DB File Location

- **Path**: `data/veo_prompts.db`
- **Git**: ignored (not committed) — contains production data

### Key DB Functions (`scripts/db.py`)

```python
from scripts.db import init_db, get_categories, get_pending_suggestions

init_db()                       # create/migrate tables
get_categories()                # list active categories
get_pending_suggestions()        # list pending category approvals
get_prompts_without_images()     # prompts needing Phase 3
```

---

## Category Library

The Category system is **dynamic** — the LLM classifies prompts against the current active category list, and can suggest new categories during Phase 2.

### Default Categories (seeded on first run)

| Name | Description |
|------|-------------|
| `video-generation` | AI video models (Veo, Sora, Kling) — motion, camera movement |
| `image-generation` | Still image models (Midjourney, DALL-E, Flux) |
| `cinematic` | Film terms: lens, f-stop, depth of field, film grain |
| `character-design` | Consistent character figures for animation/comics |
| `product-photography` | Realistic product shots, commercial advertising |
| `other` | Doesn't fit any category above |

### Category Suggestion Workflow

When Phase 2 encounters a prompt whose category is **not in the active list**:

1. The script **pauses** and prompts the user interactively:
   - `[y]` Approve — add the new category to the `categories` table
   - `[n]` Reject — assign `other` to these prompts instead
   - `[r]` Rename — type a different category name to use
2. The suggestion is recorded in `category_suggestions` with status `approved`/`rejected`
3. The LLM is re-prompted with the updated category list for remaining tweets

### Managing Categories Manually

```python
from scripts.db import add_category, deactivate_category, get_categories

# Add a new category
add_category(name="3d-render", description="Blender / 3D renderer prompts")

# Deactivate (soft-delete) a category
deactivate_category(category_id=7)

# List all active categories
cats = get_categories()
for c in cats:
    print(c['name'], c['description'])
```

---

## Workflow Orchestration (for Claude Code agent)

1. **Check API keys**: If `OPENAI_API_KEY` is not set, prompt user to configure `.env`. For Phase 3, verify GCP ADC via `gcloud auth application-default login`.
2. **Read config**: Load `configs/x-scraper.yaml` — get queries list and negative_keywords
3. **Phase 1**: Run `x_multi_search.py` → tweets written to DB (`scrape_id` returned)
4. **Verify Phase 1 output**: Check DB has tweets in `tweets` table
5. **Phase 2**: Run `extract_prompts.py --scrape-id <id>` → prompts written to DB
6. **Check pending category suggestions**: If `category_suggestions` table has pending rows, prompt user to approve/reject before continuing
7. **Phase 3**: Run `generate_images.py --scrape-id <id>` (dry-run first recommended) → images uploaded to GCS, URLs written to `prompts.image_gcs_url`
8. **Present results**: Summarize total tweets scraped, prompts extracted, category breakdown, images generated

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
| "OPENAI_API_KEY not set" | Create `.env` from `.env.example` and fill in your MiniMax API key |
| LLM returns non-JSON | Script logs error and skips the tweet; check API key and model |
| Rate limit from LLM API | Reduce call frequency or use a slower model |
| Empty prompts output | Increase `min_likes` in Phase 1 to filter low-quality tweets that LLM skips |

### Phase 3 Errors

| Error | Solution |
|-------|----------|
| `google.api_core.exceptions.Forbidden` | Run `gcloud auth application-default login`; verify project access |
| No image in Gemini response | Retry; some prompts may be too abstract for image generation |
| GCS upload fails | Check bucket permissions; `sparki-market-test` must be accessible |
| 0 prompts without images | All prompts already have images generated, or wrong scrape_id |

---

## Security Notes

- Credentials used only in `browser_auth.py`, never stored
- Session cookies saved to `outputs/cookies.json` — do NOT commit to version control
- `cookies.json` is in `.gitignore`
- Use a secondary X account for scraping to protect your primary account
