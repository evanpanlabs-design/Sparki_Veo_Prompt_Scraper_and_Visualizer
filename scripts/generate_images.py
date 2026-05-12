"""
Phase 3 — Generate cover images for extracted prompts via Gemini 2.5 Flash Image.

Reads prompts from DB (that don't yet have images), generates a cover image for each
using Vertex AI Gemini 2.5 Flash Image, uploads to GCS, and writes the GCS URL back
to the prompts table.

Usage:
    python scripts/generate_images.py                    # all ungenerated prompts
    python scripts/generate_images.py --scrape-id 1      # specific scrape
    python scripts/generate_images.py --limit 5          # test with N prompts
    python scripts/generate_images.py --local-only        # save locally only (no GCS)
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from threading import Lock
from PIL import Image

from dotenv import load_dotenv
load_dotenv()

# ── Vertex AI / GCP auth ────────────────────────────────────────────────────────
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "sparki-2")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

from google import genai
from google.genai.types import GenerateContentConfig, Modality
from google.cloud import storage

from scripts.db import (
    init_db,
    get_prompts_without_images,
    update_prompt_image,
)

GCS_BUCKET = "sparki-market-test"
DEFAULT_CONCURRENCY = 5


# ─── Style system ─────────────────────────────────────────────────────────────

STYLE_KEYWORDS = {
    "cinematic": [
        "film grain", "anamorphic", "shallow depth of field", "f-stop",
        "lens flare", "35mm", "kodak portra", "cinematic lighting",
        "aspect ratio 2.39:1", "film stock",
    ],
    "character-design": [
        "character sheet", "turnaround", "expression sheet", "consistent anatomy",
        "animation ready", "sprite sheet", "emotion study",
    ],
    "product-photography": [
        "product shot", "studio lighting", "white background", "commercial photography",
        "advertising style", "high-key lighting", "reflective surface",
    ],
    "image-generation": [
        "detailed illustration", "digital art", "8k resolution", "highly detailed",
        "masterpiece", "award winning",
    ],
    "video-generation": [
        "motion blur", "dynamic pose", "action shot", "film still",
        "photorealistic", "cinematic frame",
    ],
    "other": [
        "high quality", "detailed", "vibrant colors", "professional composition",
    ],
}


def build_image_prompt(prompt_text: str, category: str, title: str) -> str:
    """Build a cover-image prompt with category style keywords."""
    style_kws = STYLE_KEYWORDS.get(category, STYLE_KEYWORDS["other"])
    import random
    selected = random.sample(style_kws, min(3, len(style_kws)))
    style_str = ", ".join(selected)

    return (
        f"Generate a cover image for the following AI generation prompt. "
        f"The cover should visually represent this prompt in a compelling, marketable way.\n\n"
        f"IMPORTANT: Center the main subject/content in the middle of the image. "
        f"Do NOT place important elements near the top or bottom edges.\n"
        f"Style keywords: {style_str}\n\n"
        f"Prompt title: {title}\n"
        f"Prompt text: {prompt_text}"
    )


# ─── GCS helpers ───────────────────────────────────────────────────────────────

def upload_to_gcs(local_path: Path, dest_blob: str) -> str:
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(dest_blob)
    blob.upload_from_filename(str(local_path), content_type="image/png")
    return f"gs://{GCS_BUCKET}/{dest_blob}"


# ─── Image generation ───────────────────────────────────────────────────────────

def generate_cover_image(
    prompt: dict,
    local_dir: Path,
    dry_run: bool = False,
    local_only: bool = False,
) -> tuple[int, str]:
    """
    Generate a cover image for a single prompt.
    Returns (prompt_id, gcs_url or local_path or "dry-run").
    """
    prompt_text = prompt["prompt_text"]
    category = prompt.get("category", "other")
    title = prompt.get("title", "")
    prompt_id = prompt["id"]
    scrape_id = prompt["scrape_id"]

    image_prompt = build_image_prompt(prompt_text, category, title)

    if dry_run:
        print(f"  [DRY RUN] prompt_id={prompt_id} title={title!r}")
        print(f"    -> {image_prompt[:120]}...")
        return prompt_id, "dry-run"

    client = genai.Client()

    # Retry on 429 Resource Exhausted with exponential backoff
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=image_prompt,
                config=GenerateContentConfig(
                    response_modalities=[Modality.TEXT, Modality.IMAGE],
                ),
            )
            break
        except Exception as e:
            if "RESOURCE_EXHAUSTED" in str(e) and attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  [!] prompt_id={prompt_id} rate-limited, retrying in {wait}s...")
                _time.sleep(wait)
            else:
                raise

    # Extract image from response
    image_data = None
    for part in response.candidates[0].content.parts:
        if part.inline_data:
            image_data = part.inline_data.data
            break

    if image_data is None:
        print(f"  [!] No image in response for prompt_id={prompt_id}")
        return prompt_id, ""

    # Save locally — save Gemini's native output as-is
    filename = f"{prompt_id}.png"
    local_path = local_dir / filename
    img = Image.open(BytesIO(image_data)).convert("RGB")
    img.save(local_path, format="PNG")

    if local_only:
        return prompt_id, str(local_path)

    # Upload to GCS (production mode) — path: prompts/{prompt_id}.png
    # DB id is globally unique (auto-increment PK), so no scrape_id prefix needed
    gcs_blob = f"prompts/{prompt_id}.png"
    gcs_url = upload_to_gcs(local_path, gcs_blob)

    return prompt_id, gcs_url


def process_batch(
    prompts: list[dict],
    local_dir: Path,
    concurrency: int,
    dry_run: bool,
    local_only: bool = False,
) -> list[tuple[int, str]]:
    results = []
    done = 0
    total = len(prompts)
    lock = Lock()

    def on_result(pid_result):
        nonlocal done
        prompt_id, url = pid_result
        with lock:
            done += 1
            status = "OK" if url and url != "dry-run" else ("DRY" if url == "dry-run" else "FAIL")
            print(f"  [{done}/{total}] prompt_id={prompt_id} [{status}] {url[:80] if url and len(url) > 80 else url}")

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {}
        for p in prompts:
            fut = ex.submit(generate_cover_image, p, local_dir, dry_run, local_only)
            futures[fut] = p["id"]

        for fut in as_completed(futures):
            try:
                pid_result = fut.result()
            except Exception as e:
                pid = futures[fut]
                print(f"  [!] prompt_id={pid} exception: {e}")
                pid_result = (pid, "")
            on_result(pid_result)
            results.append(pid_result)

    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

def generate_images(
    scrape_id: int = None,
    limit: int = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    local_only: bool = False,
    dry_run: bool = False,
    local_dir: Path = None,
):
    init_db()

    prompts = get_prompts_without_images(scrape_id=scrape_id, limit=limit)
    if not prompts:
        print("No prompts without images found.")
        return

    total = len(prompts)
    print(f"Found {total} prompt(s) without images")
    if dry_run:
        print("DRY RUN MODE — no images will be generated")
    print(f"Concurrency: {concurrency}")
    print()

    if local_dir is None:
        local_dir = PROJECT_ROOT / "outputs" / "generated_images"
    local_dir.mkdir(parents=True, exist_ok=True)

    print(f"Local output dir: {local_dir}")
    print(f"GCS bucket: {GCS_BUCKET} (upload {'disabled (local only)' if local_only else 'enabled'})")
    print()

    results = process_batch(prompts, local_dir, concurrency, dry_run, local_only)

    # Write URLs back to DB
    updated = 0
    for prompt_id, url in results:
        if url and url != "dry-run" and url != "":
            update_prompt_image(prompt_id, url)
            updated += 1

    print(f"\nDone. Updated {updated}/{total} prompts in DB.")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate cover images for prompts via Gemini 2.5 Flash Image (Vertex AI)"
    )
    parser.add_argument("--scrape-id",   type=int,  default=None,
                        help="Only process prompts from a specific scrape ID")
    parser.add_argument("--limit",       type=int,  default=None,
                        help="Limit number of prompts to process")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Parallel generation calls (default: {DEFAULT_CONCURRENCY})")
    parser.add_argument("--local-only",  action="store_true",
                        help="Save images locally only, skip GCS upload")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Print prompts without generating images")
    parser.add_argument("--output-dir",  type=str,  default=None,
                        help="Local output directory for images")
    args = parser.parse_args()

    local_dir = Path(args.output_dir) if args.output_dir else None

    print("=" * 60)
    print("Phase 3 — Gemini 2.5 Flash Image Generation")
    print("=" * 60)
    print(f"  Scrape ID:   {args.scrape_id or 'all'}")
    print(f"  Limit:       {args.limit or 'all'}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Mode:        {'DRY RUN' if args.dry_run else ('local-only' if args.local_only else 'GCS upload')}")
    print("=" * 60 + "\n")

    generate_images(
        scrape_id=args.scrape_id,
        limit=args.limit,
        concurrency=args.concurrency,
        local_only=args.local_only,
        dry_run=args.dry_run,
        local_dir=local_dir,
    )


if __name__ == "__main__":
    main()
