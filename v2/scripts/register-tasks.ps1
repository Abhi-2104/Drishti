# Registers (or re-registers) every Drishti job in Windows Task Scheduler for the current user.
#   powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1            # register / update all
#   powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1 -Remove    # remove them all
# Tasks run when you are logged on. -StartWhenAvailable runs a missed task when the machine wakes. Every job is safe
# to repeat, so the repetitions below are "retry until the exchange has published".
param([switch]$Remove)

$uv   = (Get-Command uv -ErrorAction Stop).Source
$proj = Split-Path -Parent $PSScriptRoot

# job, kind (Daily | Weekly), days (Weekly only), time(s), repeat every N minutes (0 = no repeat), for N hours
$jobs = @(
    @("eod_bhavcopy",    "Daily",  "",          @("18:45"),          30, 4),
    @("eod_flows",       "Daily",  "",          @("19:15"),          30, 4),
    @("eod_corporate",   "Daily",  "",          @("19:45"),          60, 2),
    @("signals_daily",   "Daily",  "",          @("21:45"),          0,  0),
    @("premarket",       "Daily",  "",          @("08:45"),          30, 1),
    @("macro_daily",     "Daily",  "",          @("07:15"),          0,  0),
    @("macro_india",     "Daily",  "",          @("18:10"),          0,  0),
    @("insider",         "Daily",  "",          @("07:30", "20:30"), 0,  0),
    @("shp_master",      "Daily",  "",          @("07:45"),          0,  0),
    @("us_daily",        "Daily",  "",          @("07:20"),          0,  0),
    @("calendars",       "Weekly", "Monday",    @("07:00"),          0,  0),
    @("macro_weekly",    "Weekly", "Saturday",  @("10:00"),          0,  0),
    @("security_master", "Weekly", "Sunday",    @("10:00"),          0,  0)
)

foreach ($j in $jobs) {
    $name = "Drishti $($j[0])"
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    if ($Remove) { Write-Host "removed  $name"; continue }
    $act = New-ScheduledTaskAction -Execute $uv -Argument "run drishti run $($j[0])" -WorkingDirectory $proj
    $triggers = foreach ($t in $j[3]) {
        if ($j[1] -eq "Weekly") { $trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $j[2] -At $t }
        else { $trg = New-ScheduledTaskTrigger -Daily -At $t }
        if ($j[4] -gt 0) {
            $trg.Repetition = (New-ScheduledTaskTrigger -Once -At $t -RepetitionInterval (New-TimeSpan -Minutes $j[4]) `
                                -RepetitionDuration (New-TimeSpan -Hours $j[5])).Repetition
        }
        $trg
    }
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $act -Trigger $triggers -Settings $set -Description "drishti run $($j[0])" | Out-Null
    Write-Host ("registered {0,-26} {1} {2} {3}" -f $name, $j[1], $j[2], ($j[3] -join ", "))
}
if (-not $Remove) { Write-Host "`nCheck later with: uv run drishti health" }
