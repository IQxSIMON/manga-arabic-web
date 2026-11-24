# Five-bubble separate vs numbered Batch validation — 2026-11-11

Task: `f62d0085-64e3-4e20-bde0-df19de27a1ce`.

**Outcome: BLOCKED before inference.** The configured local Index endpoint refused connections. No speedup or Arabic-quality result can be inferred. Stopped promptly under the 10-minute limit; no model loading was attempted.

## Evidence and raw measurements

- Worker task start: 2026-11-11T06:49:11+03:00.
- Existing benchmark scripts configure `http://127.0.0.1:9941/v1/chat/completions`, model `Index-Translate-9B.Q4_K_M`.
- Unauthenticated loopback `GET /health`, timeout 5 seconds, proxies disabled: `requests.exceptions.ConnectionError`, underlying `ConnectionRefusedError [WinError 10061]`; no HTTP response.
- Independent TCP confirmation at **2026-11-11T06:50:17+03:00**: `connect_ex(('127.0.0.1', 9941)) = 10061`, timeout 5 seconds, probe wall-clock **2.041742 seconds**. This is connection-probe latency, not translation latency.
- Translation requests submitted: **0**; completed bubbles: **0**; Batch requests: **0**.

| Requested metric | Five separate requests | One numbered Batch |
|---|---|---|
| Translation wall-clock | Not measured | Not measured |
| Prompt processing time / tokens | Not available | Not available |
| Output tokens / tokens per second | Not available | Not available |
| Arabic quality | Not evaluated | Not evaluated |
| Missing / mixed bubble IDs | Not evaluated | Not evaluated |
| Inference RAM / VRAM | Not measured | Not measured |

No generated-token count is asserted: zero requests is not a measured zero-token inference result. A refused connection establishes endpoint unavailability at the check time, not whether the weights exist or another server is running elsewhere.

## Prior work reviewed

Reviewed `SPEED_VALIDATION_2026-11-10.md`, `performance_report.md`, `PROGRESS_CHECKPOINT_2026-11-10.md`, `tools/benchmark_index_msa_candidate.py`, `tools/benchmark_index_concurrency.py`, and bridge results. No completed result for this task was present (its worker record was `running`). No completed five-bubble numbered-Batch comparison was identified in these sources.

The previous four-prompt serial-versus-two-slot experiment reported 68.34 vs 62.72 seconds at six GPU layers, and 67.18 vs 55.57 seconds at eight layers. Those are separate concurrent requests, not one numbered prompt. They cannot answer this Batch question. No old sample or full-chapter run was repeated.

## Shortest next action and comparison limits

Have the operator make the existing local Index service ready on port 9941 using the project's established local startup procedure. `tools/start_index_cuda_server.py` is the existing launcher, but it reads a saved credential and may wait up to 300 seconds for loading; it was not executed under this task's no-credential-reading and stop-if-unready boundaries. A later inference run also needs an authorized credential-free interface or an explicitly approved existing authentication path.

Once ready, run only five fresh short Webtoon bubbles in source order and the same five in one prompt with explicit IDs 1–5. Keep identical reference context, model, temperature, top_p and thinking settings; disclose the necessary prompt-format difference and comparable total output allowance. Capture raw server timings/usage, exact outputs and finish reasons; verify all five IDs and meaning manually. Report prompt-cache/order effects and any competing load; a single comparison cannot establish a general speedup or chapter latency. Do not substitute prior concurrency figures.

Only this report and this task's bridge result are intended outputs. No production files, Git state, user library, server lifecycle, credentials or system settings were changed; no external service or paid API was used.
