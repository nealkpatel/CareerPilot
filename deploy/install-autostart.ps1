# Starts CareerPilot automatically whenever this Windows user logs on, and restarts it if it stops.
# Run once in PowerShell from the CareerPilot folder:   powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1
# Remove again with:                                     Unregister-ScheduledTask -TaskName CareerPilot -Confirm:$false
#
# The task runs in your desktop session (not as a service) on purpose: each user's DeepSeek sign-in opens a Chrome
# window on this computer's screen, which needs a logged-in desktop. On a VPS, disconnect Remote Desktop instead of
# signing out. Settings such as CAREERPILOT_PUBLIC_URL go in a .env file next to run.py.

param([string]$TaskName = "CareerPilot")

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction Stop).Source }

$action = New-ScheduledTaskAction -Execute $python -Argument "run.py" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Scheduled task '$TaskName' installed: CareerPilot starts at logon and is running now ($python run.py in $root)."
