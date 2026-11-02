# Manga Arabic Web

A prototype web app for loading a supported manga chapter URL, extracting page images, running OCR, and translating detected English text into Arabic.

## Current MVP scope

- Paste a supported chapter URL.
- Fetch chapter page images from MangaK.
- Run OCR and retain line-level text coordinates.
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

Set `OCR_SPACE_API_KEY` and `GEMINI_API_KEY` in the environment before using OCR and translation.