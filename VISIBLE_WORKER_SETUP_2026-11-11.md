# Visible Worker — 2026-11-11

## Lock collision correction — 07:40 +03:00

PID **4856** was left running throughout. Before inspection, status at
07:38:32 showed `monitoring`, no active task/error, and `stopped_at: null`.
After offline tests, status at 07:39:52 still showed the same PID/start time,
monitoring, no task/error, and no stop. `Get-Process` confirmed PID 4856.
CIM command-line inspection remains access denied. No live second worker,
stop request, restart, or live-lock open/read/write/delete was performed.

The actual lock is
`C:\MangaArabic\manga-arabic-web\.ai_bridge\worker.lock`.
Metadata inspection before the fix showed length 1 and last write
2026-11-11 06:33:48. The old singleton read byte zero before acquiring it;
Windows byte-range locks deny that read to a second process, producing the
reported `PermissionError` at `handle.read(1)` before lock acquisition.

Only `.ai_bridge/bridge_worker.py` was modified; an offline regression test
`.ai_bridge/test_lock_collision.py` was added. No production app changes.
The singleton now uses nonblocking exclusive Windows `LockFileEx` on byte zero,
which also works beyond EOF. It never reads, writes, or truncates lock contents.
Native error 33 (lock violation) produces a dedicated already-running outcome,
exit code 2, and a friendly English message. Access-denied/open failures remain
distinct, produce an actionable permission message and exit code 1, without a
traceback. Unlocking runs only after successful acquisition; closing the handle
also releases the OS lock on process exit. The lifetime scope still includes
shutdown status writes. Compatibility with the old byte-zero `msvcrt` lock
was verified using a separate process.

Inspected `Start-VisibleWorker.cmd` and `Start-VisibleWorker.ps1`: the CMD opens
the PowerShell launcher, which invokes console Python with `run`, displays its
exit code, and already uses `Read-Host 'Press Enter to close this window'`.
No launcher edits were necessary.

Backups of both modified existing files (worker and this report) are in
`.ai_bridge/backups/lock-collision-20261111/`. An initial shell backup attempt
failed because nested shell expansion removed its variable; the corrected
explicit-path backup completed before edits. Its stray identical root-level
worker copy was removed.

Offline verification (all passed; no network/model requests):

```powershell
rtk proxy .venv\Scripts\python.exe .ai_bridge\test_lock_collision.py
# 2 tests, OK (0.515s); contention includes native and legacy fake holders
rtk proxy .venv\Scripts\python.exe .ai_bridge\test_visible_worker.py
# 5 tests, OK (0.744s)
```

The new tests use only temporary lock paths: a fake holder stays alive while a
second process returns code 2, with no traceback and no entry to the worker loop
or status writing. After holder release, acquisition/release succeeds twice;
both empty and nonempty lock contents are unchanged. A directory used as the
temporary lock path exercises a genuine access failure (code 1, permissions
message, not already-running). The first legacy-holder test fixture omitted
`seek(0)` and locked EOF instead; that fixture was corrected to match the actual
old implementation, then all tests passed.

The running PID retains its already-loaded old code. Newly launched processes
load the fix immediately and are compatible with its existing lock; no restart
is needed to fix future collision windows. To have the monitoring worker itself
load the new code later, at a time you choose:

1. From the project directory, request a safe stop:
   `.venv\Scripts\python.exe .ai_bridge\bridge_worker.py stop`.
2. Wait for the existing window to report `Stopped. No further tasks will be
   claimed.` Verify `Get-Process -Id 4856 -ErrorAction SilentlyContinue` returns
   nothing and `.ai_bridge/worker_status.json` reports `stopped`.
3. Press Enter in the old window, then double-click `Start-VisibleWorker.cmd`
   once. Never delete or replace `worker.lock` to bypass contention.

These restart steps were documented only, not executed.

Implemented and launched from an independent Codex session. No production app,
Git state, model downloads, Index inference, or existing task results changed.

## Verified outcome

- Visible PowerShell PID **1960**, window handle **5178758**, title
  `Manga Arabic - Visible Codex Worker`, verified using `EnumWindows` and
  `IsWindowVisible` in interactive desktop **session 2** around 07:10:47 +03:00.
- Worker **4856** is a live Python process in session 2; status is `monitoring`,
  no active task, no error, `stopped_at: null`.
- This is OS window/process verification, not screenshot inspection. CIM
  command-line inspection was denied and was not used as evidence.
- Earlier visible test worker **960** stopped safely at 07:09:52 +03:00,
  consumed its stop request, and exited. Its idle PowerShell **16636** was closed.
- No hidden worker started. The lifetime OS lock excludes duplicate workers,
  including legacy launch methods, and covers shutdown status writing.

## Implementation and checks

`finish_task` preserves completed/failed records without rewriting anything;
missing records and invalid states still error. Execution finalizes once outside
its exception handler, preventing a second finalization attempt on failure.

Concurrent stdout/stderr readers flush Codex JSON events and diagnostics live,
while retaining complete redacted `.stdout.jsonl`, `.stderr.txt`, and `.result.txt`
files per run in `.ai_bridge/worker_tmp/`. `results.json` retains its existing
32,000-character result limit; `.result.txt` retains the full reply. Raw transient
CLI final-output files are deleted. Timeout/interrupt cleanup terminates the
worker-owned CLI process tree on Windows.

Redaction covers known API-key/Bearer patterns and secret environment values;
API-key environment variables remain excluded from the CLI environment. No
credential files are read. Unknown credential formats are not guaranteed
detectable; task instructions prohibit accessing credentials.

Five isolated offline smoke tests pass: finalized success/failure preservation
and missing-record error; live streaming before EOF with redaction; fake CLI
stdout/stderr capture and full 36,000-character reply capture; singleton lock
and shutdown; timeout recorded once. No real Codex/model request was made for
smoke tests. Actual visible-worker safe shutdown also passed.

All four top-level bridge JSON files parse. Seven task IDs and seven result IDs
are unique; `tasks.json` and `results.json` remain byte-identical to the backup.

Backup: `.ai_bridge/backups/visible-20261111-original/`. The initial shell backup
command failed from quoting; the original worker source was reconstructed and
restored, then copied with the other originals before prepared changes were
reapplied. The previous setup report is preserved there.

## Launch and stop

Double-click **`Start-VisibleWorker.cmd`** in the project root. It opens a visible
PowerShell window, sets the correct cwd, and runs unbuffered console Python.
The `.ps1` uses `Start-Process -WindowStyle Normal`, never `pythonw`.
ExecutionPolicy Bypass is process-local; no persistent policy change is made.
Direct `.ps1` execution without that flag was blocked by existing machine policy.

If a later automated launch cannot display a window, run once from a normal
desktop PowerShell terminal. Do not launch again while a worker owns the lock:

```powershell
Set-Location C:\MangaArabic\manga-arabic-web
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-VisibleWorker.ps1 -InWindow
```

Safe stop finishes the active task before claiming another:

```powershell
.venv\Scripts\python.exe .ai_bridge\bridge_worker.py stop
```

Offline verification commands:

```powershell
.venv\Scripts\python.exe .ai_bridge\test_visible_worker.py
.venv\Scripts\python.exe .ai_bridge\verify_visible.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .ai_bridge\inspect_visible.ps1
```

Forced terminal closure cannot guarantee a final status update. The OS lock
releases on exit; the next worker marks interrupted tasks failed without rerunning
them. Use safe stop for ordinary shutdown.
