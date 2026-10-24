"""The setup guide's "Set up HWiNFO for me" button: install and configure HWiNFO.

HWiNFO is installed from its official source with winget (Windows' app installer; HWiNFO's
license doesn't allow bundling it). Because HWiNFO lives in Program Files and runs as admin,
the work happens in one small PowerShell script that Windows asks permission for once
(the usual "Do you want to allow this app to make changes" prompt). It:
  1. installs HWiNFO if needed,
  2. closes HWiNFO if it's running, so it can't overwrite the new settings,
  3. sets HWiNFO's options: Sensors-only, minimized to the tray, sharing ("Gadget") on,
     readings every second,
  4. optionally makes HWiNFO start with Windows (the same scheduled task HWiNFO's own
     "Auto Start" option creates),
  5. starts HWiNFO.
Which readings HWiNFO shares still has to be ticked by hand (the setup guide shows how).
"""
import ctypes
import json
import shutil
import tempfile
import winreg
from pathlib import Path

WINGET_ID = "REALiX.HWiNFO"
DEFAULT_DIR = Path(r"C:\Program Files\HWiNFO64")
# HWiNFO's own settings file (HWiNFO64.INI, [Settings] section)
INI_SETTINGS = {
    "SensorsOnly": "1",             # open straight into the sensor readings
    "OpenSensors": "1",             # show sensors on startup (they keep updating)
    "MinimalizeMainWnd": "1",       # start minimized...
    "MinimalizeSensors": "1",       # ...to the tray
    "MinimalizeSensorsClose": "1",  # X hides it instead of quitting
    "ShowWelcomeAndProgress": "0",
    "SensorInterval": "1000",       # update every second, like PC Monitor
}

UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def install_dir() -> Path | None:
    """Where HWiNFO is installed, or None."""
    for root, path in UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(root, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as app:
                            name = winreg.QueryValueEx(app, "DisplayName")[0]
                            if "hwinfo" in str(name).lower():
                                folder = Path(str(winreg.QueryValueEx(app, "InstallLocation")[0]))
                                if (folder / "HWiNFO64.exe").exists():
                                    return folder
                    except OSError:
                        continue
        except OSError:
            continue
    return DEFAULT_DIR if (DEFAULT_DIR / "HWiNFO64.exe").exists() else None


def can_install() -> bool:
    return shutil.which("winget") is not None


def _ps(text: str) -> str:
    """Quote for a PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def script_text(status_file: Path, folder: Path | None, autostart: bool, dry_run_ini: Path | None = None) -> str:
    """The PowerShell script that does the work. dry_run_ini: only update that INI copy (testing)."""
    settings = "; ".join(f"{_ps(k)} = {_ps(v)}" for k, v in INI_SETTINGS.items())
    if autostart:
        settings += f"; 'Autorun' = '1'"
    dry = dry_run_ini is not None
    return f"""$ErrorActionPreference = 'Stop'
$status = {_ps(status_file)}
function Say($step, $message) {{
    @{{ step = $step; message = $message }} | ConvertTo-Json | Set-Content -Path $status -Encoding UTF8
}}
try {{
    $dir = {_ps(folder or '')}
    $dry = ${str(dry).lower()}
    if (-not $dry -and (-not $dir -or -not (Test-Path (Join-Path $dir 'HWiNFO64.exe')))) {{
        Say 'installing' 'Installing HWiNFO from its official source (this can take a minute)...'
        winget install --id {WINGET_ID} -e --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        $dir = 'C:\\Program Files\\HWiNFO64'
        $entry = Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', 'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue |
            Where-Object {{ $_.DisplayName -like '*HWiNFO*' -and $_.InstallLocation }} | Select-Object -First 1
        if ($entry) {{ $dir = $entry.InstallLocation.TrimEnd('\\') }}
    }}
    $exe = Join-Path $dir 'HWiNFO64.exe'
    if (-not $dry -and -not (Test-Path $exe)) {{ throw 'HWiNFO could not be installed.' }}

    Say 'configuring' 'Setting HWiNFO up...'
    if (-not $dry) {{
        Get-Process -Name 'HWiNFO64' -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 1
    }}
    $ini = $(if ($dry) {{ {_ps(dry_run_ini or '')} }} else {{ Join-Path $dir 'HWiNFO64.INI' }})
    $want = [ordered]@{{ {settings} }}
    $lines = @(); if (Test-Path $ini) {{ $lines = @(Get-Content $ini) }}
    if (-not ($lines -match '^\\[Settings\\]')) {{ $lines = @('[Settings]') + $lines }}
    $out = New-Object System.Collections.Generic.List[string]
    $inSettings = $false
    foreach ($line in $lines) {{
        if ($line -match '^\\[') {{
            if ($inSettings) {{ foreach ($k in $want.Keys) {{ $out.Add("$k=$($want[$k])") }}; $want.Clear() }}
            $inSettings = $line -match '^\\[Settings\\]'
            $out.Add($line); continue
        }}
        if ($inSettings -and $line -match '^([^=]+)=') {{
            $key = $Matches[1]
            if ($want.Contains($key)) {{ $out.Add("$key=$($want[$key])"); $want.Remove($key); continue }}
        }}
        $out.Add($line)
    }}
    foreach ($k in $want.Keys) {{ $out.Add("$k=$($want[$k])") }}
    Set-Content -Path $ini -Value $out -Encoding ASCII
    # Turn on sharing with other programs ("Enable reporting to Gadget").
    # Only create the key if it's missing: New-Item -Force on an existing registry key REPLACES
    # it, wiping every HWiNFO sensor setting stored under it.
    $key = 'HKCU:\\Software\\HWiNFO64\\Sensors'
    if (-not (Test-Path $key)) {{ New-Item -Path $key | Out-Null }}
    Set-ItemProperty -Path $key -Name 'EnableVSB' -Value 1 -Type DWord

    if (-not $dry -and ${str(autostart).lower()}) {{
        $action = New-ScheduledTaskAction -Execute $exe
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"
        $who = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Highest
        $opts = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName 'HWiNFO' -Action $action -Trigger $trigger -Principal $who -Settings $opts -Force | Out-Null
    }}
    if (-not $dry) {{
        Say 'starting' 'Starting HWiNFO...'
        Start-Process -FilePath $exe -WorkingDirectory $dir
    }}
    Say 'done' 'HWiNFO is installed, set up and running.'
}} catch {{
    Say 'failed' $_.Exception.Message
}}
"""


def start_setup(autostart: bool) -> Path | None:
    """Run the setup script with Windows' permission prompt. Returns the status file to watch,
    or None if the user said No to the prompt."""
    work = Path(tempfile.mkdtemp(prefix="pcmonitor_hwinfo_"))
    status = work / "status.json"
    script = work / "setup_hwinfo.ps1"
    script.write_text(script_text(status, install_dir(), autostart), encoding="utf-8-sig")
    status.write_text(json.dumps({"step": "waiting", "message": "Waiting for Windows' permission..."}),
                      encoding="utf-8")
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "powershell.exe",
        f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}"', None, 0)
    return status if result > 32 else None


def read_status(status_file: Path) -> dict:
    try:
        return json.loads(status_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"step": "working", "message": "Working..."}
