# Android renderer compatibility report
Date: 2026-11-16

## Existing baseline
The Windows renderer depends on Python, OpenCV, NumPy, Pillow, arabic-reshaper, python-bidi and Windows fonts. Renderer v7 fixes remain in the Windows project and were not changed by this Android slice.

## Android status
No renderer port exists yet. The Android app currently displays original image pages only. Arabic shaping, RTL line layout, masking, background reconstruction and visual comparison have not been implemented or measured on Android.

## Port plan
Implement an Android renderer behind a page-level interface. Reuse saved OCR polygons/masks where available; do not rediscover bubbles. Preserve untouched pixels outside approved masks, use Android shaping/RTL APIs with explicit font fallback, and keep the original page immutable. Validate each renderer stage against the Windows v7 output using the same saved OCR and translation data. Flag uncertain regions rather than erasing artwork.

## Performance
No Android timing or memory data is available. Windows timings are not Android estimates.
