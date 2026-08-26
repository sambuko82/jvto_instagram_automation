# JVTO Instagram Automation

This repository contains a reusable starter for turning JVTO reviews into Instagram carousel assets.

## What it does
- ranks incoming reviews by media-asset count (richest, most photographed reviews first) and 5-star status
- parses review text into structured fields (guest type, package length, destinations, guide/driver names, quote, highlight) - rule-based by default, with an optional context-locked LLM extraction mode
- renders a reusable 1080x1350 carousel with four fixed-position cards, including a QR code and truthfully-worded credibility link on the testimonial card
- writes PNG assets to disk and can optionally upload them to ImgBB and publish the carousel to Instagram via Composio

## Quick start
1. Create a Python environment and install dependencies:
   `python -m venv .venv`
   `source .venv/bin/activate` (or `.venv\Scripts\Activate.ps1` on Windows)
   `pip install -e .` (offline/local-only: rendering + QR codes, no Drive/Instagram/agentic)
   `pip install -e ".[composio]"` (adds Drive ingestion, Instagram publish, agentic extraction)
2. Copy `.env.example` to `.env` and fill the values you need.
3. Run the CLI:
   `python -m jvto_instagram_automation --local-json data/sample_review.json`

## Project layout
- `src/jvto_instagram_automation/` - application package
- `data/` - sample review inputs
- `tests/` - unit tests, including rendering output checks

## Environment variables
- `FILE_ID_GOOGLE_REVIEW_PAGE_1` - legacy placeholder; not required for the current flow
- `GOOGLE_DRIVE_FOLDER_ID` - optional folder ID hint for the review drive folder
- `GOOGLE_DRIVE_FOLDER` - folder name used by the Composio connector (defaults to `JVTO Reviews`)
- `COMPOSIO_API_KEY` - required for Drive ingestion, Instagram publishing, and agentic extraction
- `COMPOSIO_USER_ID` - optional user/session identity for the Composio connection
- `IMGBB_API_KEY` - optional ImgBB upload key
- `INSTAGRAM_ACCESS_TOKEN` / `INSTAGRAM_USER_ID` - optional direct Instagram Graph API fallback (used only if Composio publishing isn't available)
- `OUTPUT_DIR` - output directory for generated cards
- `REVIEW_PRIORITY_LIMIT` - how many top-ranked reviews to consider before picking one to post (default 5)
- `JVTO_GOOGLE_MAPS_PROFILE_URL` - JVTO's Google Maps profile link, used as a fallback credibility link when a review has no `reviewReplyUrl` of its own

## Google Drive ingestion & Instagram publishing (via Composio)
Auth is handled once, out-of-band, by the Composio CLI - not by this app:

```bash
composio link googledrive
composio link instagram
```

Once both are linked, set `COMPOSIO_API_KEY` and run:

```bash
python -m jvto_instagram_automation --drive-export
```

This reads review JSON files from the configured Drive folder, exports them to `data/drive_reviews.json`, ranks them by media-asset count, and generates the carousel from the top pick.

If Composio is not configured yet, the workflow still works with the local sample review file (`--local-json`). For real publishing, a Composio connection for Instagram (preferred) or a direct `INSTAGRAM_ACCESS_TOKEN` + ImgBB key are needed.

> Note: earlier versions of this tool stored Composio auth state in local
> `.instagram_auth.json` / `.drive_auth.json` files. That approach has been
> removed - one of those files was not reliably gitignored, which risked
> committing session tokens to the repo. The Composio CLI's own auth storage
> replaces it entirely.

## Priority filtering by visual assets
`get_priority_reviews()` scores each 5-star review by its media-item count
(with a small bonus for reviews that carry their own `reviewReplyUrl`), so
well-photographed, verifiable reviews are posted before plainer ones.

## Agentic review extraction
If you want richer, context-mapped social captions and visual prompts, enable the agentic mode:

```bash
AGENTIC_EXTRACTION=1 COMPOSIO_API_KEY=... python -m jvto_instagram_automation --agentic --local-json data/sample_review.json
```

The agentic path is instructed to extract every field (guest type, package, guide/driver names, destinations, quote) strictly from the source review text - it's told not to invent details that aren't there. If Composio/the agent SDK aren't available, it automatically falls back to the rule-based parser so the workflow still runs without any external model key.

## Credibility link strategy
Every generated caption and the testimonial card are built from a single
`review_url_kind` field so the credibility claim is always truthful:
- **specific** - the review's own `reviewReplyUrl` exists; caption/card say "read this exact review" and embed a QR code linking straight to it.
- **profile** - no per-review link exists; falls back to `JVTO_GOOGLE_MAPS_PROFILE_URL` and says "see more reviews on our profile" instead - it never claims a general link verifies one specific review.
- **none** - no link at all is available; the credibility line is omitted entirely rather than fabricated.
