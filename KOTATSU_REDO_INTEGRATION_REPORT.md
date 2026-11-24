# Kotatsu Redo integration report
Date: 2026-11-16

## Inspected source
Repository: https://github.com/Kotatsu-Redo/Kotatsu-Redo
Tag: `9.8.7` • Commit: `9ef70777557e96c136c767b136a3022f7aff9dc4`
Integration tree: `android-kotatsu-redo/` (full source snapshot copied without `.git`).
Reference clone: `ocr_test_results/kotatsu_redo_source_inspection/` retained unchanged.

## Integrated
- Upstream Android app, Gradle structure, reader/library/source code and licenses retained in the fork tree.
- New translator workbench code added under `app/src/main/kotlin/org/koitharu/kotatsu/translator/`.
- A new Arabic entry in Kotatsu's root Settings opens the translator workbench.
- Android manifest registers the feature Activity.

Kotatsu advertises vertical/Webtoon and page reading, local CBZ/ZIP, library, reading history and a large parser catalogue. The source inventory and advertised parser count are not proof every source works today.

## Verification status
Debug APK build succeeded; 425 unit tests passed. APK: `android-kotatsu-redo/app/build/outputs/apk/debug/app-debug.apk` (43,087,804 bytes). ADB reported no connected device or emulator, so installation/runtime, source search/download and integration through Kotatsu's Room/library remain unverified. The workbench stores its imported archive separately in app-private storage.

## Integration risks
- Upstream uses compile/target SDK 37 and a sizeable dependency graph.
- Parser library and signed source bundle must remain pinned/trust-checked. Forks must use only a signing identity controlled by this project before publishing dynamic parser bundles.
- Upstream patches must be tracked and marked; keep original credits and license files.
- No source has been claimed tested in this fork.
