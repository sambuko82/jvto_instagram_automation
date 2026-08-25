# JVTO Instagram Automation

This repository contains a reusable starter for turning JVTO reviews into Instagram carousel assets.

## What it does
- parses review text into structured fields (guest type, destinations, guide/driver names, quote, highlight)
- renders a reusable 1080x1350 carousel with four fixed-position cards
- writes PNG assets to disk and can optionally upload them to ImgBB / Instagram

## Quick start
1. Create a Python environment and install dependencies:
   `python -m venv .venv`
   `source .venv/bin/activate` (or `.venv\Scripts\Activate.ps1` on Windows)
   `pip install -e .`
2. Copy `.env.example` to `.env` and fill the values you need.
3. Run the CLI:
   `python -m jvto_instagram_automation --local-json data/sample_review.json`

## Project layout
- `src/jvto_instagram_automation/` - application package
- `data/` - sample review inputs
- `tests/` - lightweight regression tests

## Environment variables
- `FILE_ID_GOOGLE_REVIEW_PAGE_1` - legacy placeholder; not required for the current flow
- `GOOGLE_DRIVE_FOLDER_ID` - optional folder ID hint for the review drive folder
- `GOOGLE_DRIVE_FOLDER` - folder name used by the Composio connector (defaults to `JVTO Reviews`)
- `COMPOSIO_API_KEY` - required for the Composio-based Drive auth flow
- `COMPOSIO_USER_ID` - optional user/session identity for the Composio connection
- `IMGBB_API_KEY` - optional ImgBB upload key
- `INSTAGRAM_ACCESS_TOKEN` - optional Instagram publishing token
- `INSTAGRAM_USER_ID` - optional Instagram account ID
- `OUTPUT_DIR` - output directory for generated cards

## Google Drive ingestion
The preferred path is to use Composio so the workflow does not call Google APIs directly.

```bash
python -m jvto_instagram_automation --oauth --auth-provider drive
python -m jvto_instagram_automation --oauth --auth-provider instagram
python -m jvto_instagram_automation --drive-export
```

The first command starts a Composio Google Drive authorization flow. The second starts a Composio Instagram authorization flow. The export command uses the authenticated Composio session to read review JSON files from the configured Drive folder and export them into `data/drive_reviews.json`, then generates the carousel from that exported data.

If Composio is not configured yet, the workflow still works with the local sample review file. For real publishing, both a Composio connection for Instagram and an ImgBB upload key (or a direct Instagram token) are recommended.

## Agentic review extraction
If you want richer social captions and visual prompts, enable the agentic mode with Composio:

```bash
AGENTIC_EXTRACTION=1 COMPOSIO_API_KEY=... python -m jvto_instagram_automation --agentic --local-json data/sample_review.json
```

The agentic path uses Composio-backed agent tooling when a Composio API key is available. If not, it automatically falls back to the rule-based parser so the workflow still runs without any external model key.
