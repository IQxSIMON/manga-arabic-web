# Renderer Optimization Final Report — Teto x Egen Chapter 9

**Date:** 2026-11-16  
**Scope:** Repair and benchmark the existing renderer using the saved 11-page OCR and Gemini 3.1 Flash-Lite result. No OCR was rerun and no translation API was called.

## Result

The new renderer completed all 11 pages and exported a valid CBZ. It rendered 108 submitted translation regions with **zero renderer skips**. It also keeps the original page files unchanged; their SHA-256 hashes match the source hashes recorded in the manifest.

The 30-second goal is met only for a warm render that can reuse the saved bubble masks: **10.956 seconds wall time** for render plus CBZ export. A cold render that must run bubble detection took **118.220 seconds**. Neither figure includes OCR or Gemini translation, which were intentionally not run in this task. Therefore, 30 seconds for a fresh, full end-to-end chapter has not been achieved or demonstrated.

## Before and after

| Measurement | Previous renderer | Final v7, cold | Final v7, cached |
|---|---:|---:|---:|
| Pages | 11 | 11 | 11 |
| Regions submitted to renderer | 110 | 108 | 108 |
| Renderer successes | 103 | 108 | 108 |
| Renderer skips | 7 | 0 | 0 |
| Renderer time | 138.816 s | 117.561 s | 10.346 s |
| Wall time including CBZ export | 141.607 s | 118.220 s | 10.956 s |
| CBZ export | included in wall time | 0.248 s | 0.265 s |
| Peak process RAM | 1,025.5 MiB | 1,026.2 MiB | 235.9 MiB |
| CBZ size | 44,156,709 bytes | 62,091,971 bytes | 62,091,971 bytes |

Cold renderer time improved by about **15.3%**. The cached run is faster because it loads exact-page bubble masks from disk; it is not representative of processing a new chapter without cached detections. PNG output remains lossless. The faster PNG encoding increased the CBZ size by about 40.6%.

## Stage timings

Per-page stage times are summed from the manifests, so their sum can differ slightly from overall wall time.

| Stage | Previous | Final cold | Final cached |
|---|---:|---:|---:|
| Image decode + bubble detection/cache | 112.057 s | 107.897 s | 1.030 s |
| Arabic layout preflight | 0.831 s | 0.953 s | 0.915 s |
| Erasure-mask construction | 0.178 s | 0.173 s | 0.161 s |
| Text repair/inpainting | 3.204 s | 3.177 s | 3.086 s |
| Bubble fill/overlay | 1.066 s | 0.950 s | 0.993 s |
| Arabic composition | 3.565 s | 1.072 s | 0.970 s |
| PNG encoding | 17.792 s | 3.204 s | 3.061 s |

The cold-run bottleneck remains bubble segmentation at about 107.9 seconds. The saved mask cache reduces that stage to about 1.03 seconds on this chapter. A fresh-run target below 30 seconds needs a faster segmentation path or an appropriate reusable cache; the current result does not justify claiming otherwise.

## Fixes implemented

- Added a persistent bubble-mask cache keyed by the SHA-256 of each exact source image. Cache writes are atomic; corrupt cache entries fall back to detection.
- Improved source-text masks to cover the confirmed OCR polygon footprint and antialiased glyph edges.
- Corrected OCR angle errors for wide horizontal captions, preventing horizontal Arabic lines from being tilted.
- Added a conservative flat-background repair for external captions. It samples only the quiet area around a confirmed OCR box and paints only inside that box; busy backgrounds continue through the normal local inpainting path.
- Kept manual layout expansion constrained by page bounds and other OCR regions.
- Changed PNG encoding to a faster lossless setting.
- Preserved source pages and existing renderer results; final images and CBZ use new, versioned paths.

## Region and visual review

The previous seven renderer skips were **p01-r004, p01-r022, p02-r007, p08-r010, p09-r003, p11-r006, and p11-r007**. Four text regions that should have rendered now render: the name, the long caption, the chapter title, and the merged chapter caption. The remaining three are intentionally preserved in English under policy: the phone status-bar time and creator/translator credits.

The final manifest contains 37 review records, including preserved or excluded source regions and one caption merged into another region. The review sheet classifies them as:
- 12 correct exclusions, chiefly watermarks and a sound effect.
- 14 low-confidence OCR fragments or stylized glyphs, mostly sound-effect/artwork-like material, left untranslated.
- 7 watermark regions excluded before the translation request.
- 3 policy-preserved items: one UI time and two credits.
- 1 important caption that was merged into the adjacent caption and rendered there.

The merged region is a single source-caption correction, not a missing translation. Counts in these categories describe source-region review records; they should not be added to the number of successful output overlays as if they represented independent dialogue lines.

Visual assets include a full-chapter overview, the exclusion contact sheet, and before/after crops for the seven previous skips. These make the result auditable. The contact sheet is a screening review, not a substitute for a human proofread of every Arabic line. Watermarks and Korean sound effects remain untouched by design.

## Validation

- Focused renderer tests: **20 passed**.
- Python compilation of the renderer: passed.
- Final CBZ integrity check: 11 PNG pages, no corrupt ZIP entries.
- All 11 source-page hashes match the originals.
- Final manifest reports 108 submitted, 108 rendered, and zero skipped.
- No OCR, Gemini API, paid service, or local translation model was used.

## Output files

- Final Arabic CBZ (cached, complete): `ocr_test_results/web_chapter_teto_x_egen_ch09/Teto_x_Egen_Chapter_9_Arabic_Gemini31_optimized_v7_cached.cbz`
- Final page images: `ocr_test_results/web_chapter_teto_x_egen_ch09/rendered_pages_gemini31_optimized_v7_cached/`
- Cold-run manifest and images: `ocr_test_results/web_chapter_teto_x_egen_ch09/render_manifest_optimized_v7_cold.json`, `rendered_pages_gemini31_optimized_v7_cold/`
- Cached-run manifest: `ocr_test_results/web_chapter_teto_x_egen_ch09/render_manifest_optimized_v7_cached.json`
- Visual review assets: `ocr_test_results/web_chapter_teto_x_egen_ch09/renderer_comparisons_final/`

## Alpha readiness

The renderer is suitable to enter an **Alpha trial with a visual-review step**: this chapter now completes without renderer skips, and its cached render is quick. It is not yet demonstrated as a fully unattended production pipeline. Cold bubble detection dominates runtime and memory, several OCR/SFX decisions still require policy-aware review, and the full OCR-plus-translation-plus-render time was not measured here. The 30-second full-chapter objective remains open.
