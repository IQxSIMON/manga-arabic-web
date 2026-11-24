# Android architecture decision
Date: 2026-11-16

## Decision
Use a hybrid architecture based on Kotatsu Redo 9.8.7, with independent Arabic translation functionality added inside its Android application. The user explicitly accepted GPL-3.0 integration. Kotatsu supplies the mature reader, local/remote library and parser framework; Manga Arabic Translator work is kept in an isolated translator feature package and will call engine interfaces rather than modify the Windows app.

The fork lives in `android-kotatsu-redo/`. The current integration adds an Arabic translation entry in Settings and a local CBZ workbench. The original inspection clone remains under ignored `ocr_test_results/`; no Windows/Python files were overwritten.

## Why this option
- Kotatsu already has Android reading modes, history/library and sources, reducing risk compared with rebuilding those foundations.
- Independent Android translation modules keep OCR, bubble segmentation, Gemini and rendering evolvable.
- Direct upstream app/parser use is licensed GPL-3.0; user approved this. Preserve notices, mark changes and provide corresponding source/build instructions with any distribution.
- Parser sources are shipped/updated as signed bundles upstream; the fork's bundle update trust settings must be reviewed before public distribution.

## Current implementation versus target
Implemented: native Kotatsu fork tree; integration entry in Settings; Arabic RTL CBZ importer/viewer feature (current reader slice is limited and separate from Kotatsu's own reader state).
Not yet implemented: OCR inference, bubble model, review editor, workflow wiring to Gemini, renderer, chapter job persistence and translated CBZ export. A standalone Gemini API client and secure model/key settings now exist; it supports only Gemini 3.1 Flash-Lite and Gemini 3.5 Flash-Lite, has not been invoked, and is not connected to OCR/UI translation.

## Build/tooling
Project uses upstream Gradle/Android configuration. A project-local JDK 17 and Android SDK are now configured. Debug build and unit tests completed successfully (425 tests, 0 failures); debug APK is 43,087,804 bytes. No Android device was attached, so installation/runtime remains unverified.
