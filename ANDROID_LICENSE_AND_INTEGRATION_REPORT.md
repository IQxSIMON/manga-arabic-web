# Android license and integration report
Date: 2026-11-16

## GPL decision
User approved GPL-3.0 integration on 2026-11-16. This permits proceeding with the Kotatsu Redo fork under its license conditions; it is not a waiver of those obligations.

## Upstream components
Kotatsu Redo tag 9.8.7 at commit `9ef70777557e96c136c767b136a3022f7aff9dc4` is licensed GPL-3.0-only. The parser project is separate and also GPL-3.0. The source snapshot, its `LICENSE`, notices and attribution remain in `android-kotatsu-redo/`. Do not replace or remove upstream credits.
The translator workbench files added to the Android application are project changes; record them as changed files in the fork.

## Distribution checklist
Before any APK is shared:
- Provide the exact corresponding source for the binary and build/install scripts.
- Include GPL-3.0 and all upstream/dependency notices.
- Identify modifications and their dates.
- Preserve source/parser version and release provenance.
- Review dynamic parser bundle URL and public signing key; do not publish code-signing authority unless owned by this fork.
- Generate a complete transitive license report for the resulting APK.
No public distribution or upload has occurred. Debug APK build is verified, but it has not been installed or distributed. The generated APK is 43,087,804 bytes. This report is not legal advice.
