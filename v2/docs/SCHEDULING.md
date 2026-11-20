# Scheduling Drishti jobs

Data collection needs no model and no always-on process: the OS scheduler runs **`drishti catchup`**, which works out what is
due. Every job is safe to repeat (files already stored are not fetched again), so a retry is just another run.

`uv run drishti jobs` lists the jobs with a suggested time (IST). `uv run drishti health` shows what is fresh and what failed
(exit code 1 when anything is degraded). `uv run drishti run <job>` exits 1 if any step failed.

## How `drishti catchup` works

Three slots, IST. For each slot, the latest scheduled time already past is "due"; it runs when the job log shows no successful
completion of that slot since then. Slots run oldest first. So a laptop that was off catches up at the next trigger, and a
trigger that finds nothing due does nothing. A failed slot is retried at the next trigger (not within 30 minutes), until the
slot's next scheduled time replaces it.

| Slot | When (IST) | Jobs |
|---|---|---|
| morning | daily 08:30 | `macro_daily`, `us_daily`, `premarket`, `insider`, `shp_master`, `news`; Mon also `calendars`, Sun also `security_master` |
| evening | daily 20:30 | `eod_bhavcopy`, `eod_flows`, `eod_corporate`, `macro_india`, `insider`, `news`; then the **gap fill**; then `signals_daily` |
| weekly | Sat 10:00 | `macro_weekly`, the forward log, holdings import, the **weekly pack**, import of Claude's answers |

(`news` does nothing, and says so, while `settings.toml` lists no feeds.)

**Gap fill.** For prices, delivery %, participant OI and index closes it finds sessions missing from the last 30 days (and
everything since the newest stored day, up to 400 days) and fetches only those, through the same resumable code as
`drishti backfill`. Deals are re-fetched when the newest deal is more than 4 days old. A missing day the exchange has no file for
(a holiday) is remembered for 7 days so it is not asked for again on every run. An empty table is not a gap: the first fill is
`drishti backfill`. Known loss: NSE serves FII/DII cash for the latest day only, so a day the laptop stays off is gone
(NSDL FPI covers FPI history).

**Weekly pack.** `<private>/weekly/<last session>/` holds an evidence bundle per holding (exit gate) and per new-stock candidate
(entry gate), `pack.json`, and a README with the answer format. rules-v1 is run on the same cases first. The Saturday Claude
review writes `claude-<date>.jsonl` and `review-<date>.md` next to it; the next catch-up imports the calls once. `<private>` is
`--private-dir`, else `$DRISHTI_PRIVATE`, else `../private` next to `v2/`, else `./private`. It is git-ignored and never uploaded.

One catch-up at a time (a lock file next to the database), a time budget of 100 minutes (a slot that does not fit stays due),
exit code 1 if anything failed. Look before running: `uv run drishti catchup --plan`. Run one slot by hand:
`uv run drishti catchup --slot evening`.

## Windows (Task Scheduler)

One script registers one task, "Drishti catchup", and running it again updates it (it also removes the old per-job tasks):

```powershell
cd $HOME\Documents\Drishti\v2
powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
```

The task runs the project's own `.venv\Scripts\pythonw.exe -m drishti.cli catchup --log`, so **no terminal window opens**. Its output
goes to `logs\catchup-<date>.log` next to the database (the newest 30 are kept; `uv run drishti paths` shows the folder), and
every slot's result is in the job log (`uv run drishti health`), which is what the app's Data & Jobs page will show. Run `uv sync`
first so `.venv` exists; run it again after an update. `-Remove` takes the task out again. Triggers: at logon (2 minute delay), daily 08:30, daily 20:30, Saturday 10:00. A missed start runs
as soon as possible, it needs a network, a second copy never starts while one runs, and it stops after 2 hours. It runs while
you are logged on; keep the laptop's clock on IST. Try it with `Start-ScheduledTask -TaskName "Drishti catchup"`, then
`uv run drishti health`. A test pins the slot table to `drishti jobs`, so a new job cannot be forgotten, and pins the script to
`drishti catchup`, the hidden `pythonw` launch and its four triggers.

## Linux / WSL (cron)

```
*/30 * * * *  cd ~/Drishti/v2 && uv run drishti catchup
```
(It does nothing unless a slot is due, so a frequent trigger is fine.)

## macOS (launchd)

Create `~/Library/LaunchAgents/com.drishti.catchup.plist` running `uv run drishti catchup` from the project directory with
`RunAtLoad` and a `StartInterval` of 1800, then `launchctl load` it.

## Running a single job by hand

`uv run drishti jobs` lists the jobs with a suggested time; `uv run drishti run <job>` runs one and exits 1 if any step failed;
`uv run drishti health` shows what is fresh and what failed (exit code 1 when anything is degraded).
