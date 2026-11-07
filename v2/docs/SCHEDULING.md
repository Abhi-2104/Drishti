# Scheduling Drishti jobs

Data collection needs no model and no always-on process: the OS scheduler runs `drishti run <job>`. Every job is safe to
repeat (files already stored are not fetched again), so "retry until the file is published" is just a repeating trigger.

`uv run drishti jobs` lists the jobs with a suggested time (IST). `uv run drishti health` shows what is fresh and what failed
(exit code 1 when anything is degraded). `uv run drishti run <job>` exits 1 if any step failed.

## Windows (Task Scheduler), PowerShell

Run once per job. Edit `-At` and the repetition to taste. `-StartWhenAvailable` is the catch-up: if the machine was off at the
scheduled time, the task runs when it comes back.

```powershell
$job   = "eod_bhavcopy"
$uv    = (Get-Command uv).Source
$proj  = "$HOME\Documents\Drishti\v2"
$act   = New-ScheduledTaskAction -Execute $uv -Argument "run drishti run $job" -WorkingDirectory $proj
$trg   = New-ScheduledTaskTrigger -Daily -At 6:45pm
$trg.Repetition = (New-ScheduledTaskTrigger -Once -At 6:45pm -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 4)).Repetition
$set   = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Drishti $job" -Action $act -Trigger $trg -Settings $set
```

Remove with `Unregister-ScheduledTask -TaskName "Drishti eod_bhavcopy" -Confirm:$false`. Test one by hand first with
`Start-ScheduledTask -TaskName "Drishti eod_bhavcopy"` and then `uv run drishti health`.

## Linux / WSL (cron)

```
45 18 * * 1-5  cd ~/Drishti/v2 && uv run drishti run eod_bhavcopy
```
(Repeat the line for 19:15, 19:45, ... if you want retries.)

## macOS (launchd)

Create `~/Library/LaunchAgents/com.drishti.eod_bhavcopy.plist` running `uv run drishti run eod_bhavcopy` from the project
directory with `StartCalendarInterval` Hour 18 / Minute 45, then `launchctl load` it.

## Suggested schedule

| Job | When (IST) | Why |
|---|---|---|
| `eod_bhavcopy` | 18:45, repeat every 30 min until 22:45 | NSE/BSE publish between about 18:00 and 19:00 |
| `eod_flows` | 19:15, repeat every 30 min until 23:00 | participant OI and FII/DII are published after the close |
| `insider` | 07:30 and 20:30 | filings arrive through the evening; only new ones are downloaded |
| `shp_master` | 07:45 daily | filings arrive around quarter-end |
| `macro_weekly` | Sat 10:00 | CFTC publishes Friday; FRED updates weekly |
| `security_master` | Sun 10:00 | refreshes listings and picks up BSE scrip codes not yet seen |

Not verified on a real Task Scheduler yet. Try one job and look at `drishti health` before registering the rest.
