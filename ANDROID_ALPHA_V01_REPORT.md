# Android Alpha 0.1 report
Date: 2026-11-17

## Current delivered slice
- Kotatsu Redo 9.8.7 fork, with GPL-3.0 use explicitly approved by the user.
- Arabic RTL entry from Kotatsu Settings to a separate translator workbench.
- Local CBZ/ZIP import through Android Storage Access Framework with bounded extraction, path traversal rejection, image-only entries and staged commit.
- App-private chapter library and vertical-scroll reader, previous/next controls, saved page position and downscaled background decoding.
- Gemini settings expose only Gemini 3.1 Flash-Lite and Gemini 3.5 Flash-Lite; the key is encrypted using Android Keystore AES-GCM.
- The page OCR review UI lets the user select/deselect regions (including omitting sound effects) and explicitly confirm before sending selected text to Gemini. It displays model, elapsed time, usage tokens and missing IDs.
- OCR regions, selection state and Arabic translations are saved per page with region IDs and coordinates. No Gemini API request was made during development.
- A CPU Kotlin OCR prototype now uses the existing PP-OCRv6 manga detector/recognizer ONNX files. The files are packaged as assets and copied into app-private no-backup storage on first engine initialization. OCR runs only from an explicit reader action.
- No local translation engine/model was added and no Gemini API request was made.

## Build and tests
- `:app:compileDebugKotlin` succeeded after adding the OCR and page-review/translation flow.
- `:app:assembleDebug` succeeded; package version is `0.1.0-alpha` (debug application ID `org.mangaarabic.translator.debug`).
- Debug APK: `android-kotatsu-redo/app/build/outputs/apk/debug/app-debug.apk`, 81,522,718 bytes (~77.7 MiB).
- SHA-256: `16724552271548C01B4FBDFA8B39B21D67A1C5958CC2CD82CBB123BE1AAF1434`.
- Unit tests: `:app:testDebugUnitTest` succeeded; 38 suites, 427 tests, 0 failures, 0 errors. Two tests cover region selection/translation persistence and duplicate-ID rejection.
- The Windows file-lock issue was bypassed with a temporary ignored Gradle init script that redirected only the test task output; that script was removed.
- APK assets include PP-OCRv6 manga detector (1,816,958 bytes), recognizer (21,334,702 bytes) and dictionary (76,327 bytes).
- ONNX Runtime Android 1.22.0 requires minSdk 24. Gradle emitted non-fatal compileSdk/deprecation and D8 Kotlin-metadata warnings.

## Validation limits and remaining scope
- ADB has no attached device and no local emulator executable was found. APK installation, launch, device inference, import/reading UI and key persistence remain untested at runtime.
- OCR compiles and is packaged but has not been executed on Android; accuracy, Windows parity, order, latency and peak RAM are unknown. Bubble segmentation and its masks are not ported.
- Bubble segmentation/masks and Renderer v7 are not ported. The app supports OCR review and page-level Gemini translation, but does not yet compose Arabic on images or export translated CBZ.
- It is an installable reader/OCR/page-translation prototype, not yet a complete image-translation Alpha or release-ready application.

## Database choice
The source snapshot omitted Room schema JSON files required for historical auto-migrations. The fork is a separate application ID/private data namespace, starts at schema v7, and does not import Kotatsu app data. Future schema changes still need explicit migrations.
