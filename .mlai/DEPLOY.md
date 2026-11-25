# Deployment

## Current state (2026-11-25, commit c7d2deb)
- The app is deployed nowhere; it only runs inside the workspace.
- Preview now works: the managed Preview button runs Vite (`vite.config.js`, port 3000, `host: true` + `allowedHosts: true`) which proxies `/api/*` and the app to Flask on port 5000. Start Flask with:
  `MANGA_ARABIC_FONT=/usr/share/fonts/truetype/freefont/FreeSerif.ttf MANGA_BIND_HOST=127.0.0.1 PORT=5000 .venv/bin/python app.py`
- Commit `c7d2deb` is pushed to branch `preview-vite` on GitHub (IQxSIMON/manga-arabic-web); `master` is at `8c6718b`.
- Tests: `. .venv/bin/activate && MANGA_ARABIC_FONT=... python -m pytest translator_engine/tests -q` → 331 passed + 7 errors (all in test_engine_regression.py, missing trained model `translator_engine/models/opus_en_ar`). Without that file: 331 passed, 0 failed.

## Known preview limitation
- The public `*.vercel.run` tunnel works only while the managed preview process is alive and dies with the session. Terminal curl cannot reach the tunnel; only the built-in browser tool can.

## Pending
- Production hosting: deploy to a Python-capable host (Vercel is static-only; Railway/Render/Fly fit Flask). User has not connected an account.
- Missing artifact: re-train or restore `translator_engine/models/opus_en_ar` to clear the 7 test errors.
