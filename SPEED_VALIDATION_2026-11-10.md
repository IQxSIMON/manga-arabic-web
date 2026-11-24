# Index Speed Validation — 2026-11-10

## Full chapter run

- The 15-page vertical chapter completed using Index prompt v23 compact, pipeline 23.
- All 15 page results completed. Of 154 detected regions, 136 included regions had Arabic output; 18 credits or low-confidence regions were automatically excluded. No included region was left untranslated.
- Every page render request succeeded with zero skipped regions.
- The CBZ export contains 15 images and `ComicInfo.xml`. ZIP integrity check returned no corrupt member.
- Export: `C:\Users\BMW1975\AppData\Local\Temp\vertical-chapter-resume-20261110-184812.cbz`.

## Translation timing

For 11 comparable pages (2, 3, 5–7, 9–14), fresh page-level translation times were compared with the previous run. Cached prior pages 8 and 15 were excluded, as was page 4 whose previous timing was affected by the computer sleeping.

- Previous mean: **279.86 seconds**.
- Compact-prompt mean: **223.80 seconds**.
- Mean reduction: **20.0%**.
- Page 3 was **11.4% slower** in the compact run, so the mean does not guarantee a speedup on every page.

Page 2 specifically went from 347.14 seconds to 238.33 seconds. The same local Index model was used; model weights and decoding settings were unchanged.

## Current prompt quality adjustment

Index prompt v24 adds an explicit Modern Standard Arabic only rule and instructs the model to render short reactions naturally in context. Direct checks returned:

- `I CAN'T MISS TODAY'S REWARD...!` → `لا أستطيع تفويت مكافأة اليوم...!`
- `OBVIOUSLY.` → `بالطبع.`

The v24 full-chapter rerun completed in an isolated copy of the test library. All 15 pages completed and rendered successfully with zero skipped regions; 136 of 154 detected regions were included and produced Arabic, while 18 credits or low-confidence fragments were automatically excluded. Independent Unicode validation found no included region without Arabic letters. Page 7 required a resume after a noisy OCR fragment triggered an output-limit error. That failed attempt spent 440 seconds; after the bounded-recovery fix, the resumed page completed and rendered 12 of 13 regions. Its cumulative time includes the failed attempt and is not suitable as a clean speed comparison.

The v24 CBZ export is `C:\Users\BMW1975\AppData\Local\Temp\vertical-chapter-resume-20261110-201918.cbz` (15 images, `ComicInfo.xml`, 57,204,741 bytes). The independent ZIP integrity check returned no corrupt member. Selected rendered pages 2, 7, and 15 were visually inspected; this is a sample, not a full visual review.

For eight clean pages shared with the v23 full-chapter checkpoint (pages 5, 6, 9–14), v23 averaged 206.9 seconds and v24 averaged 243.3 seconds. The observed v24 run was 17.6% slower on that subset, but run conditions were not controlled, and v24 was not faster across the chapter. Keep this result explicit: the recovery fix removes pathological waits, while typical page latency remains a major open issue.

To prevent repeats of that pathological wait, Index now makes one context-free recovery attempt capped at 128 output tokens when a short source (up to 128 characters) hits the output limit. Normal translation settings and quality checks remain unchanged; if recovery is still invalid, the line fails quickly for review instead of generating up to 512 tokens and retrying again. Regression coverage includes the exact noisy OCR sentence from page 7.

The CUDA launcher previously used 8 offloaded layers instead of 4, based on a small same-model comparison: 17.31 seconds vs 18.03 seconds (about 4% faster), with approximately 1.7 GB of 2 GB VRAM used. This single-slot result has since been superseded by a two-slot benchmark; the current launcher defaults to 6 layers to retain VRAM headroom under concurrent inference.

The repeatable local check is `tools/benchmark_index_msa_candidate.py`; it reads the loopback Index key without printing it.

## Verification

- `pytest translator_engine/tests -q`: **140 passed in 92.40 seconds** after the bounded-recovery and CUDA default changes.
- `py_compile`, `node --check static/app.js`, and `git diff --check` passed after the final code edits.
- `py_compile`, `node --check static/app.js`, and `git diff --check` passed.
- Visual inspection covered selected pages only; a full visual review of all 15 pages remains open.

## One-minute chapter target

A guaranteed sub-60-second full-chapter translation is not feasible with the current Index setup. The validated 15-page sample had 154 detected regions (136 included); accumulated Index translation time was about 62.8 minutes, and the page-run timings plus failed attempts were about 63 minutes end to end. llama.cpp logs showed about 2.3 generated tokens/second and commonly 12–15 seconds of prompt processing per bubble. Two-slot inference yielded a 9% speedup on four fixed prompts, far short of what would be needed to translate that full chapter in one minute. The two slots do not change each bubble's previous-source context: its input context is assembled before dispatch.

Reaching one minute would require a bounded chapter size and much faster compute; parallel inference alone is insufficient. No code change can guarantee one minute for an arbitrary chapter length or across Android phones. Do not present one minute as a supported target on the current local configuration.

## Two-slot page translation experiment — 2026-11-11

- Independent text regions within a page are now dispatched in pairs. Their source text, page/neighbor dialogue context, glossary inputs, model prompt, temperature, token budget, and output validation remain the same as serial translation. Results are committed in source order, and completed lines remain resumable after an error.
- The router lock now protects engine selection and failure state, but not the actual Index request. This lets llama.cpp's two request slots run concurrently. The CUDA launcher uses 6 offloaded layers, two slots, and a 3072-token total context (1536 tokens per slot) on the 2 GiB GeForce 930MX.
- A four-prompt serial-vs-parallel benchmark returned byte-identical translations with Arabic output for every item. At 6 GPU layers: serial 68.34 seconds, paired 62.72 seconds (1.09× throughput). At 8 GPU layers and the same context: 67.18 vs 55.57 seconds (1.209×), but VRAM reached 1,972/2,048 MiB after warm-up, so 8 layers is not the default.
- A single bubble still takes roughly 14–20 seconds when alone in this four-prompt sample; while overlapping, individual latency ranges from about 20 to 43 seconds, though total batch throughput improves. The previous full chapter's 62.8-minute serial total suggests roughly 58 minutes for a comparable chapter at the conservative setting, but a full chapter has not yet been rerun with the two-slot pipeline; this is an estimate, not a validated chapter time.
- `pytest translator_engine/tests -q`: **143 passed** after the concurrency changes. `py_compile`, `node --check static/app.js`, and `git diff --check` passed. The regression suite includes barrier-based checks proving two page regions and two router requests can overlap while retaining region order/context.
