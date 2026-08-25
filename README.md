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
- `FILE_ID_GOOGLE_REVIEW_PAGE_1` - Drive file ID for a Google review JSON export
- `GOOGLE_DRIVE_FOLDER_ID` - optional folder ID for future Drive ingestion
- `IMGBB_API_KEY` - optional ImgBB upload key
- `INSTAGRAM_ACCESS_TOKEN` - optional Instagram publishing token
- `INSTAGRAM_USER_ID` - optional Instagram account ID
- `OUTPUT_DIR` - output directory for generated cards
