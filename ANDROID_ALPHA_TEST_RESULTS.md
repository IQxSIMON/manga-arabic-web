# Android Alpha test results
Date: 2026-11-17

## Build
- `:app:compileDebugKotlin`: BUILD SUCCESSFUL with Android OCR and page review/translation flow.
- `:app:assembleDebug`: BUILD SUCCESSFUL; final packaging after the label change took 2m 10s.
- Version `0.1.0-alpha`; debug package `org.mangaarabic.translator.debug`.
- APK: `android-kotatsu-redo/app/build/outputs/apk/debug/app-debug.apk` (81,522,718 bytes / about 77.7 MiB).
- SHA-256: `16724552271548C01B4FBDFA8B39B21D67A1C5958CC2CD82CBB123BE1AAF1434`.
- `aapt` verified the installed label `Manga Arabic Translator Alpha`; the APK includes the PP-OCRv6 manga model assets.
- Existing Android SDK warnings are non-fatal.
- `minSdk` is 24 to meet ONNX Runtime Android 1.22.0's minimum.
- Non-fatal warnings: SDK XML version mismatch, AGP tested only through compileSdk 36.1 while project uses 37, Kotlin-metadata rewrite warnings in D8, Gradle deprecations and native-library packaging advice. APK packaging still succeeded.

## Unit tests
- `:app:testDebugUnitTest`: BUILD SUCCESSFUL in 6m 1s.
- Includes two new `TranslationReviewStoreTest` cases for persistence and duplicate-ID rejection, as well as CBZ safety and Gemini model allowlist tests.
- Windows repeatedly refused writes to the existing `bundleDebugClassesToCompileJar/classes.jar` because another process held the file. Without stopping any process or changing project build settings, a temporary ignored Gradle init script redirected just this task's output to a distinct file under `app/build/intermediates`; tests then passed. The temporary script has been removed.

## Not tested
- ADB reports no attached Android device. No emulator executable is present at the configured local SDK path.
- APK install/launch, CBZ import and reading, Android Keystore persistence, real OCR inference, Gemini API requests, bubble segmentation, image rendering and CBZ export have not been validated on an Android device.
- No Gemini API request was issued and no quota was consumed.
