# Manga Arabic Web

A prototype web app for loading a supported manga chapter URL, extracting page images, running OCR, and translating detected English text into Arabic.

## Current MVP scope

- Paste a supported chapter URL.
- Fetch chapter page images from MangaK.
- Run OCR using optional Google Cloud Vision, with OCR.space as a fallback, and retain approximate line-level text coordinates.
- Translate OCR lines in order and return each Arabic translation with its source text and image coordinates.
- Display OCR boxes and a per-line translation review list.

## Current limitations

- OCR rectangles mark text lines, not complete speech-bubble boundaries.
- Line-to-translation alignment is a first-pass mapping; review pages where OCR merges or splits lines or Gemini changes the line count.
- The app does not yet erase English lettering, inpaint the artwork, detect complete bubbles, or typeset Arabic inside bubbles.
- Site anti-bot protection or JavaScript-only page rendering may prevent chapter extraction.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Set `GEMINI_API_KEY` before using translation. OCR configuration:

- **No changes required:** keep `OCR_SPACE_API_KEY`; the app continues to use OCR.space by default when no Google Vision key is configured.
- **Optional Google OCR:** set `GOOGLE_CLOUD_VISION_API_KEY`. With `OCR_PROVIDER=auto` (default), Google Vision becomes the primary OCR provider and OCR.space is used as fallback when its key is also present.
- To force a provider, set `OCR_PROVIDER=google` or `OCR_PROVIDER=ocrspace`.

Example in Termux:

```bash
export GEMINI_API_KEY='your-existing-gemini-key'
export OCR_SPACE_API_KEY='your-existing-ocrspace-key'
# Optional: only if you have enabled Cloud Vision in a Google Cloud project
export GOOGLE_CLOUD_VISION_API_KEY='your-google-vision-key'
export OCR_PROVIDER=auto
```

**Free-use note:** Google Cloud lists 1,000 Cloud Vision units per month in its Free Tier. This is a monthly allowance, not unlimited usage; check the current Cloud Vision pricing and billing requirements in your Google Cloud project before enabling it. Set a quota/budget alert and do not enable paid overages if the requirement is strictly free-only. Gemini free-tier model availability and rate limits vary by model and project.

The Google Vision adapter uses `DOCUMENT_TEXT_DETECTION` word polygons and groups words into approximate horizontal lines. This line grouping is not speech-bubble detection and may need review for vertical text or stylized lettering.