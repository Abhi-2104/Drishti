# Registers ONE Windows Task Scheduler task, "Drishti catchup", for the current user. It runs `drishti catchup`, which works out
# by itself which of the three slots (Morning 08:30, Evening 20:30, Weekly Sat 10:00, IST) are due and not yet done, so a laptop
# that was off simply catches up at the next trigger.
#   powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1            # register / update
#   powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1 -Remove    # remove it (and any old per-job tasks)
# It runs hidden (no terminal window). Triggers: at logon (2 min delay, for the network), daily 08:30, daily 20:30, Saturday 10:00. A missed start runs as soon as
# possible (StartWhenAvailable); it only runs with a network; a second copy never starts while one runs (IgnoreNew; catchup also
# holds a lock file); 2 hour limit. The task runs while you are logged on. Times are the laptop's clock, so keep it on IST.
param([switch]$Remove)

$taskName = "Drishti catchup"
$proj = Split-Path -Parent $PSScriptRoot

# the per-job tasks of the older version of this script: remove them so nothing runs twice
Get-ScheduledTask -TaskName "Drishti *" -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -ne $taskName } | ForEach-Object {
    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
    Write-Host "removed  $($_.TaskName) (replaced by $taskName)"
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    if ($Remove) { Write-Host "removed  $taskName"; exit 0 }
} elseif ($Remove) { Write-Host "nothing to remove"; exit 0 }

# pythonw.exe from the project's own environment: no console window ever opens. Output goes to logs\catchup-<date>.log next to the
# database (see `uv run drishti paths`) and the result of every slot is in the job log (`uv run drishti health`), which the app shows.
$pyw = Join-Path $proj ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Error "$pyw not found: run 'uv sync' in $proj first"; exit 1 }
$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m drishti.cli catchup --log" -WorkingDirectory $proj

$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = "PT2M"
$morning = New-ScheduledTaskTrigger -Daily -At "08:30"
$evening = New-ScheduledTaskTrigger -Daily -At "20:30"
$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "10:00"

$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $morning, $evening, $weekly) -Settings $set `
    -Description "drishti catchup (hidden): runs every Drishti slot that is due and not yet done (morning, evening, weekly)" | Out-Null
Write-Host "registered $taskName  (logon, 08:30, 20:30, Sat 10:00)"
Write-Host "Try it now:  Start-ScheduledTask -TaskName '$taskName'   (no window opens; watch the log or run  uv run drishti health)"
Write-Host "Or see what is due without running anything:  uv run drishti catchup --plan"
