# Android OCR compatibility report
Date: 2026-11-17

## Existing Windows pipeline
Windows uses Python preprocessing/postprocessing with PP-OCRv6_manga detection and recognition, plus optional comic text detection and speech-bubble segmentation. The surrounding pipeline depends on OpenCV/NumPy and project-specific region merging, reading order and bubble handling.

## Android status
A Kotlin CPU OCR port now exists in `android-kotatsu-redo/app/src/main/kotlin/org/koitharu/kotatsu/translator/AndroidMangaOcr.kt`. It loads the existing detector, recognizer and dictionary from APK assets, copies them into app-private no-backup storage with byte-length validation, tiles very tall pages, performs ONNX inference and greedy CTC decoding, and returns page-relative regions with IDs and confidence. The reader exposes this through an explicit local OCR action.

The build uses ONNX Runtime Android 1.22.0 and minSdk 24. The APK includes the existing detector (1,816,958 bytes), recognizer (21,334,702 bytes) and dictionary (76,327 bytes). No weights were downloaded and no local translation model or engine was added.

The chapter page now includes a review step. Region IDs, coordinates, OCR text, selection and Arabic output are saved per page. Only user-selected texts are sent to Gemini after a confirmation dialog; the app displays model, token usage, latency and missing IDs. No API call was made during this development.

## Validation limits
- `:app:compileDebugKotlin` and `:app:assembleDebug` succeeded.
- The OCR implementation has not run on Android hardware or an emulator; no Android device/emulator was attached.
- Preprocessing/postprocessing parity with Windows, accuracy, reading order, inference speed and peak RAM on ARM64 remain unmeasured.
- This port currently returns rectangular text detections. The speech-bubble segmentation model, dark-oval fallback, masks and Windows region-merging logic are not yet ported.
- Region selection and translation persistence are implemented; users can select/deselect regions, but OCR source text itself cannot yet be edited.

## Required next work
1. Add/port bubble segmentation and region-mask handling; compare against the Windows baseline.
2. Build a reproducible shared-page corpus and compare OCR text, missed detections, false positives, region overlap and order.
3. Run cold/warm inference and peak-RAM measurements on a physical ARM64 Android device.
4. Review long-page tiling and crop rotation against representative English webtoon text before claiming parity.

No Android OCR accuracy or speed claim is made.
