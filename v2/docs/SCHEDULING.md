# Scheduling Drishti jobs

Data collection needs no model and no always-on process: the OS scheduler runs `drishti run <job>`. Every job is safe to
repeat (files already stored are not fetched again), so "retry until the file is published" is just a repeating trigger.

`uv run drishti jobs` lists the jobs with a suggested time (IST). `uv run drishti health` shows what is fresh and what failed
(exit code 1 when anything is degraded). `uv run drishti run <job>` exits 1 if any step failed.

## Windows (Task Scheduler)

One script registers every job, and running it again updates them (it removes and re-adds each task):

```powershell
cd $HOME\Documents\Drishti\v2
powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
```

`-Remove` takes them all out again. Tasks run while you are logged on; `-StartWhenAvailable` runs a missed one when the
machine wakes. Try one by hand with `Start-ScheduledTask -TaskName "Drishti eod_bhavcopy"`, then `uv run drishti health`.
The job list in the script is checked by a test against `drishti jobs`, so a new job cannot be forgotten there.

## Linux / WSL (cron)

```
45 18 * * 1-5  cd ~/Drishti/v2 && uv run drishti run eod_bhavcopy
```
(Repeat the line for 19:15, 19:45, ... if you want retries.)

## macOS (launchd)

Create `~/Library/LaunchAgents/com.drishti.eod_bhavcopy.plist` running `uv run drishti run eod_bhavcopy` from the project
directory with `StartCalendarInterval` Hour 18 / Minute 45, then `launchctl load` it.

## Schedule (what the script registers)

| Job | When (IST) | Why |
|---|---|---|
| `eod_bhavcopy` | daily 18:45, every 30 min for 4 h | NSE/BSE publish between about 18:00 and 19:00; then split/bonus adjustments |
| `eod_flows` | daily 19:15, every 30 min for 4 h | participant OI, F&O ban list, index closes, bulk/block deals, FII/DII |
| `eod_corporate` | daily 19:45, hourly for 2 h | announcements, corporate actions, board meetings, ratings, large deals, IPOs |
| `signals_daily` | daily 21:45 | after the evening data is in |
| `premarket` | daily 08:45, every 30 min for 1 h | Gift Nifty, NSDL confirmed FPI flows |
| `macro_daily` | daily 07:15 | Yahoo and FRED daily series |
| `macro_india` | daily 18:10 | MoSPI CPI (about the 12th), WPI (14th), IIP (28th), RBI reserves; a series past its window is reported STALE |
| `insider` | daily 07:30 and 20:30 | filings arrive through the evening; only new ones are downloaded |
| `shp_master` | daily 07:45 | filings arrive around quarter-end |
| `calendars` | Mon 07:00 | holidays (incl. special sessions) and expiry dates |
| `macro_weekly` | Sat 10:00 | CFTC publishes Friday; FRED updates weekly |
| `security_master` | Sun 10:00 | listings and BSE scrip codes |

Daily jobs also run at weekends: a weekend run finds nothing new and costs a few requests, and it catches special
sessions such as Diwali Muhurat trading without a calendar of its own.
