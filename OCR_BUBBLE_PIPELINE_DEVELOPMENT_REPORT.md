# OCR and bubble pipeline test report

## Scope and constraints

This report covers the local OCR test chapter work. OCR used the existing RapidOCR small path and the installed optional medium retry where the pipeline selected it. No Index translation model, external API, or model download was used. Chapter originals were read from CBZ archives and were not modified. Generated data is under the ignored `ocr_test_results/` directory.

## Corpus inventory

`tools/index_ocr_test_chapters.py` indexes archive members and SHA-256 hashes without extracting images. It reads ZIP-based CBZ archives and recognizes raster suffixes `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, and `.tiff`; it does not read true RAR-based CBR archives. The six available archives were all CBZ with WebP pages: 10 (31), 11 (37), 12 (31), and 931, 932, 933 (7 each). The proposed `datasets/ocr_test_chapters` directory is currently absent. The 931–933 samples are confirmed vertical long-strip chapters; the remaining chapter names alone do not prove layout, though their sampled pages are vertical-scroll content. Archive pages are natural-sorted by member path; this ordering matches the tested chapter sequences.

## Chapter 10 baseline

`tools/run_local_chapter_ocr.py` processed the full 31-page archive serially twice. The initial run recorded 152 regions and 398.458 seconds. A second full run with the corrected Windows process-handle signature recorded 410.852 seconds and peak working set (RSS) of 1,534.2 MiB. Mean per-page time in the second run was 13.25 seconds. Both runs completed 31/31 pages. Their per-region OCR text and per-page region counts were identical. This is a repeatability and RAM-instrumentation comparison, not a before/after quality improvement comparison: the OCR implementation did not change between these runs.

`tools/render_ocr_review.py` now renders the saved regions over source pages, including external text boxes and reading-order numbers. The complete visual contact sheet is `ocr_test_results/baseline/10/contact_sheet.jpg`.

## Chapter 11 baseline

After Chapter 10 was complete and documented, Chapter 11 was processed serially through all 37 pages. It produced 161 regions and 398 OCR lines in 440.825 seconds, with a measured peak RSS of 1,688.0 MiB. All 37 annotated pages are saved under `ocr_test_results/baseline/11/overlays/`. These are baseline measurements only; they do not establish detection accuracy without additional human annotations.

## Chapter 931 baseline

The first three pages of chapters 931–933 were visually sampled. Their 1,200-pixel-wide, 11,000–13,500-pixel-tall images are vertical scrolling manhwa pages; this sample does not provide traditional manga page layouts. Chapter 931 was then processed completely (7/7 pages) before starting another chapter. It produced 84 OCR regions and 223 text lines in 307.764 seconds, with peak RSS of 1,656.1 MiB. Seven annotated page images are under `ocr_test_results/baseline/931_Chapter_930/overlays/`.

## Chapter 932 baseline

Chapter 932 completed all 7 pages before starting another chapter. It produced 84 regions and 232 OCR lines in 301.530 seconds, with peak RSS of 1,654.9 MiB. Its 7 annotated pages are under `ocr_test_results/baseline/932_Chapter_931/overlays/`.

## Chapter 933 baseline

Chapter 933 completed all 7 pages before starting another chapter. It produced 69 regions and 169 OCR lines in 248.765 seconds, with peak RSS of 1,484.0 MiB. Its 7 annotated pages are under `ocr_test_results/baseline/933_Chapter_932/overlays/`.

## Chapter 12 baseline

Chapter 12 completed all 31 pages after Chapter 933 was complete. It produced 168 regions and 455 OCR lines in 398.898 seconds, with peak RSS of 1,573.0 MiB. All 31 annotated pages are under `ocr_test_results/baseline/12/overlays/`.

## Human-reviewed reference

Page 24 was visually inspected against the original. Three visible dialogue balloons are two dark ovals and one irregular white burst. Their reference boxes were independently redrawn approximately around the visible outer contours. Comparison with detector boxes gives IoU 0.901, 0.924, and 0.495; therefore only 2/3 meet the explicit IoU ≥0.5 match rule. The irregular burst is a borderline miss under that strict box metric even though the dialogue region is detected. OCR text in the second dark bubble contains a recognition error. This single-page, three-region sample is too small for a general accuracy claim.

Page 10 was visually inspected. It contains English overlay dialogue and a decorative Korean sound effect; the OCR output misses words in one dialogue phrase and does not transcribe the decorative lettering. The numeric boxes in `ocr_test_results/baseline/10/manual_reference_page10.json` were based on the detector overlay and are not independent reference annotations. Therefore the earlier 7/8 detection and 6/7 OCR figures are withdrawn and must not be cited as accuracy metrics. Re-draw boxes from the original pages before scoring.

Chapter 931 page 2 received an independent human transcription review from the source image. Eleven dialogue regions and three publisher overlay regions are visible and detected; the dialogue sequence is visually top-to-bottom. Ten dialogue transcripts match exactly among the ten unambiguous entries. One proper-name spelling is ambiguous and excluded; one isolated OCR fragment (`a`) remains unattributed and is not counted as a false positive. Details are in `ocr_test_results/baseline/931_Chapter_930/manual_reference_page02.json`. This is one page-level sample and not a chapter/corpus accuracy estimate.

## Observed error classification (human review)

| Sample | Classification | Evidence and limits |
|---|---|---|
| Chapter 10, page 10: dialogue phrase omits “JUST NEED” | OCR error / partial text loss | The speech bubble is detected, but recognition omits words. Local preprocessing variants did not recover them. |
| Chapter 10, page 10: decorative Korean lettering | Missed text or OCR error, unresolved | It is visible in the source but absent from the OCR transcript; its independent detection status was not annotated. |
| Chapter 11, page 10: decorative sound effect read as `b` | OCR error; broad filter caused text loss | The large region covered stylized Korean lettering. The broad single-letter filter removed that region. The final size-limited rule retains it, while the OCR transcription remains wrong. |
| Chapter 11, pages 12 and 30: decorative Korean lettering | Missed text | Both pages visually contain stylized lettering, but the full-chapter run returned zero OCR regions. |
| Chapter 11, page 10: external result `K` | Probable false detection | Visual review places its box over foliage artwork without visible lettering. |
| Chapter 931, page 2: isolated `a` | Candidate false detection, unresolved | Its 14×14-pixel box was removed by the final filter. Human review did not establish whether it was an image artifact or real tiny text; excluded from accuracy scoring. |
| Chapter 933, page 6: isolated `m` | Candidate OCR error or false detection, unresolved | The 230×241-pixel region is retained by the final filter. Review is insufficient to decide whether this is stylized writing or artwork. |
| Chapter 10, page 24: irregular burst bubble | Inaccurate bubble boundary | Independent approximate box IoU was 0.495, just below the 0.5 match threshold; the other two reviewed boxes passed. This single page is not an aggregate score. |
| Chapter 931, page 2 | Reading order | The reviewed dialogue sequence is top-to-bottom and no ordering error was observed on this page only. |
| Reviewed pages | Bubble splitting / merging | No confirmed split or merge failure was annotated in this small reference set; this remains unmeasured across the corpus. |

## Verification

Focused OCR and detector tests passed: `88 passed in 26.75s` for `test_speech_bubbles.py`, `test_local_ocr.py`, and `test_comic_text_detector.py`, with pytest temp files under the ignored result directory. The first attempt had 87 passes and one environment permission error because pytest scanned the default user Temp directory; the rerun passed after setting `--basetemp` within the project.

After reviewing the missed phrase on Chapter 10 page 10, four local OCR variants (source crop, 2x resize, CLAHE, and adaptive threshold) did not recover `JUST NEED`; no OCR text was changed by guesswork. Added a review hint for speech-bubble regions below 0.85 OCR confidence and a regression test. The focused local OCR and bubble tests then passed (`86 passed in 23.23s`). A complete second pass on Chapter 10 processed 31/31 pages in 395.588 seconds, peak RSS 1,532.9 MiB. Compared with the original full pass, all region texts and bounding boxes were identical. The change added review flags to three low-confidence regions (page 10: 0.8242; page 26: 0.8320; page 29: 0.8326). This is a reviewability improvement, not a detection or recognition accuracy gain. Before/after outputs and overlays are stored in `ocr_test_results/baseline/10/` and `ocr_test_results/post_review_flag/10/`.

Chapter 931 page 2 had a standalone lowercase `a` outside balloons in the baseline; its OCR box measured 14×14 pixels. The first experimental filter removed all lone lowercase glyphs, but later regressions showed that rule was too broad. The current rule only drops lowercase single-character OCR results when both box dimensions are at most 24 pixels. A complete rerun of Chapter 931 finished 7/7 pages in 411.875 seconds with 1,653.2 MiB peak RSS. It produced 83 regions versus 84 baseline; the sole text change is removal of that tiny `a`, with every other page and transcription unchanged. Outputs are under `ocr_test_results/post_tiny_lowercase_filter/931_Chapter_930/`. The independent human review did not resolve whether `a` was text or an image artifact, so this is a candidate false-positive reduction only and is excluded from accuracy scoring.

To check for regressions, Chapter 932 was rerun completely (7/7 pages) with the initial broader filter. All 84 region texts remained identical to baseline. Its rendered pages are under `ocr_test_results/post_single_lowercase_filter/932_Chapter_931/`. The current narrower rule is a subset of that filter.

An initial broad filter removed a lone lowercase `m` on page 6, but its OCR region was large (230×241 pixels) and may represent stylized lettering or artwork; there was not enough evidence to call it noise. The revised 24×24-pixel rule retains this result. A complete rerun of Chapter 933 finished 7/7 pages in 361.949 seconds with 1,484.4 MiB peak RSS; all 69 region texts exactly match baseline. Final overlays are under `ocr_test_results/post_tiny_lowercase_filter/933_Chapter_932/`. The remaining `m` is a candidate OCR/transcription error requiring human review, not a removed detection.

The first broad singleton filter was also run across all 37 pages of Chapter 11. It removed a lowercase `b` whose 314×248-pixel box covered a large decorative Korean sound effect on page 10. Because that could discard stylized text, the rule was narrowed to lowercase one-character results with both box dimensions ≤24 pixels. The updated local OCR tests passed (`82 passed in 39.06s`). Chapter 11 was rerun completely (37/37 pages) in 594.110 seconds with 1,686.7 MiB peak RSS; its 161 region texts exactly match baseline, and the page-10 `b` result is retained. The run reported 66 bubble masks, 65 speech-bubble text regions, 96 external-text regions, 398 OCR lines, and 1,051 whitespace-separated tokens across all extracted text. Pages 12 and 30 returned no OCR regions despite visible decorative Korean lettering, so those are confirmed missed-text examples. Page 37 is visually blank and does not count as a miss. Page 10 also contains a probable false external-text result `K` over foliage artwork. Thus the full chapter was processed, but its text was not fully extracted. Final annotations are under `ocr_test_results/post_tiny_lowercase_filter/11/`. This run validates that the revised filter avoids the observed large-region loss; it does not establish a general accuracy gain. Runtime varies across runs and is not treated as a speed improvement.

## Limitations and next work

- No before/after detector comparison exists yet on the same annotated corpus. Historical checkpoint results cover other images and cannot be substituted as a matched baseline for these six archives.
- Human reference coverage is one page. Expand it across light/dark bubbles, external text, decorated sound effects, complex backgrounds, and tall pages before calculating aggregate recall or OCR accuracy.
- Per the user's latest scope clarification, the project targets vertical webtoon/long-strip work regardless of country of origin. Traditional manga page layouts are out of scope; no further coverage gap is recorded for them.
- Chapter 10's second complete run measured peak RSS at 1,534.2 MiB. Further peak measurements should use the same process-level method.
- Chapters 11, 931, and 933 have now been fully reprocessed with the revised filter, each completed and compared before proceeding to the next.
- Classify page errors in the reference set as missed text, false detection, OCR error, reading-order error, or inaccurate bubble segmentation.
- All six supplied archives have now been processed completely one at a time. Additional chapters in `datasets/ocr_test_chapters` were not present at inventory time.

## Changed/added files

- `tools/index_ocr_test_chapters.py`: read-only CBZ inventory and SHA-256 manifest.
- `tools/run_local_chapter_ocr.py`: sequential, checkpointed full-chapter OCR and annotated pages.
- `tools/render_ocr_review.py`: rerender annotations from saved OCR output.
- `.gitignore`: ignores `ocr_test_results/`.
- `ocr_test_results/`: ignored manifests, chapter output, and hand-reviewed reference.


## Full-chapter renderer validation with mock Arabic — 2026-11-13

A separate, deterministic rendering pass used the latest saved Chapter 11 OCR output; it did not rerun OCR or call a translation model/API. The renderer validates image repair, Arabic shaping/placement, output layers, and memory use. Its Arabic fixtures are explicitly test strings, not translations.

- Added tools/render_saved_chapter_mock.py and review-sheet tools. Each archive page is verified against the SHA-256 stored with OCR before rendering; outputs are written separately under ignored ocr_test_results/mock_render/.
- The final pass completed **37/37 pages**, consuming **238.060 seconds** and peaking at **974.7 MiB RSS**. It created 37 PNG page outputs and 37 sets of four layers (source, removal mask, repaired underlay, translated-text overlay). Pages 12, 30, and 37 had no OCR regions; the tool exports them unchanged with empty layers so the chapter output still includes every page.
- Across the 161 saved OCR regions, **158 rendered and 3 were skipped**. The first pass rendered 155 because its Arabic fixture was deliberately too long; after shortening the fixture, both the second and final complete passes rendered 158/161. This is a layout-fixture correction, not an OCR or translation-quality gain.
- Visual comparison of a normal speech bubble (page 2) and a spiky white bubble (page 10) showed the fixture centered and legible, with original artwork and bubble boundaries preserved in the inspected crops. Review image: ocr_test_results/mock_render/chapter11_20261113_final/representative_review.jpg.
- The final skipped-region sheet shows two page-24 external OCR regions (YOH, Hol Noy) over decorative Korean sound effects; they are not English dialogue and should be classified as SFX/non-English rather than sent for translation. One long speech-bubble region on page 27 matched a detected bubble but the Arabic layout preflight could not fit the test string above its minimum readable font size; the source lettering was safely retained and flagged. This needs a human translation/layout decision or improved editor placement.
- Chapter 11 pages 12 and 30 still have visible decorative lettering with no OCR regions; page 37 is blank. The mock renderer cannot recover missed OCR text.

This completes a full-chapter **image-rendering** validation using test fixtures, not the requested real translation or a general accuracy benchmark. It does not prove every OCR region is valid English or that every real Arabic translation will fit. The API translation experiment remains unstarted until a local key and Free Tier status/quota are confirmed.
