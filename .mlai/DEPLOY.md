# Deployment

## Current state (2026-11-25, commit fc380b9)
- The app is deployed nowhere; it only runs inside the workspace.
- Running locally: `MANGA_ARABIC_FONT=/usr/share/fonts/truetype/freefont/FreeSerif.ttf PORT=3000 MANGA_BIND_HOST=169.254.0.2 PREVIEW_ALLOWED_HOST=preview-check.vercel.run .venv/bin/python app.py`
  - Bind host 169.254.0.2 is required for the preview proxy to reach the app.
- Tests: `. .venv/bin/activate && MANGA_ARABIC_FONT=/usr/share/fonts/truetype/freefont/FreeSerif.ttf python -m pytest translator_engine/tests -q` → expect 330 passed + 7 errors (missing trained model `translator_engine/models/opus_en_ar`).

## Known preview limitation
- The managed Preview button serves static files only and never runs Flask, even with a package.json start script; the public `sb-*.vercel.run` tunnel works only while the managed preview process is alive and dies with the session.

## Pending
- Production hosting: deploy to Vercel (user has not connected their account yet; user was sent the Connect Vercel link twice).
- Missing artifact: re-train or restore `translator_engine/models/opus_en_ar` to clear the 7 test errors.
