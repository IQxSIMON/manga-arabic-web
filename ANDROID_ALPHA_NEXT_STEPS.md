# Android Alpha next steps

## Highest priority
1. Port speech-bubble segmentation and masks to the Android OCR flow; compare reviewed webtoon pages against the Windows baseline.
2. Port Renderer v7 and safe image cleanup, then render Arabic into reviewed regions and export translated CBZ chapters.
3. Add OCR text editing, page-level resumable jobs and retry/state handling beyond current per-page saved selections and translations.
4. Install and test CBZ import, vertical reading, OCR inference, Keystore persistence, explicit Gemini translation and export on a physical ARM64 Android device; measure latency and peak RAM.
5. Establish Room migration tests; this fork starts at its separate schema v7 and does not import Kotatsu app data.
6. Before public sharing, generate a complete dependency license inventory and provide GPL-3.0 source and notices.

Installable debug APK: `android-kotatsu-redo/app/build/outputs/apk/debug/app-debug.apk`, version `0.1.0-alpha`. It is a reader/OCR/page-translation prototype, not yet a complete image-translation Alpha. No Gemini API request was sent. All 427 unit tests pass.
