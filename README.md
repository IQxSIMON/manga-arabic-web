# Manga Arabic Web

A prototype web app for loading a supported manga chapter URL, extracting page images, running OCR, and translating detected English text into Arabic.

## Current MVP scope

- Paste a supported chapter URL.
- Fetch chapter page images from MangaK.
- Run OCR and display Arabic translations alongside each page.

## Limitations

This MVP does not erase original lettering or typeset Arabic text inside speech bubbles. Those require text-region detection, image inpainting, and Arabic lettering layout. Site anti-bot protection or JavaScript-only page rendering may also prevent extraction.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Set `OCR_SPACE_API_KEY` in the environment before using OCR. This is a prototype and has not been deployed.