# PC Monitor

A live hardware dashboard for Windows gaming PCs: CPU, GPU, memory, temperatures, disk, network, Wi-Fi and FPS on
one screen, made to sit on a second monitor. Graphs are coloured by health (blue = cool, green = good, orange =
getting high, red = too high), and after each game you get a recap of how your PC handled it, what held it back,
and how to fix it.

## Install

1. Download the latest **PC-Monitor-x.y.z.zip** from the [Releases page](../../releases/latest).
2. Unzip it and move the **PC Monitor** folder somewhere permanent (e.g. Documents).
3. Double-click **PC Monitor.exe**. Windows may say *"Windows protected your PC"* because the app isn't from a
   big company: click **More info → Run anyway**.
4. The setup guide opens and walks you through **HWiNFO** (free, from [hwinfo.com](https://www.hwinfo.com/download/)),
   which PC Monitor uses for some stats.

**Updates:** PC Monitor checks for a new version when it starts. When there is one, click **Update available**
(top right) and **Update now**. Your settings and history are kept.

Needs Windows 10 or 11.

## What it shows

- **Dashboard:** CPU usage (overall and per core), CPU and GPU temperature, GPU usage, VRAM, GPU power, memory,
  disk read/write, network down/up, Wi-Fi strength, the apps using the most CPU and memory, FPS (displayed and
  rendered, so frame generation is clear), plus any extra HWiNFO sensor you choose.
- **Game recaps:** after each game: verdict, what limited your FPS, frame drops with their likely causes,
  temperatures, and suggested fixes. Every recap is saved under Reports → Games, and **Copy for Claude** copies a
  detailed report to paste into an AI chat for a deeper look.
- **Reports:** pick a day to see which apps used your PC the most, your ping and internet drops, and speed tests.
- **Extras:** alerts, throttle badges, mini mode, overlay mode, tray icon, themes. Everything can be switched off
  in Settings.

## Privacy

- Everything stays on your PC: settings and history are stored in `%LOCALAPPDATA%\PC Monitor`, and history is
  kept for 90 days.
- **The app tracker** (off by default) records app names with their CPU and memory use. Never window titles,
  websites or what you do inside apps.
- **Network activity:**
  - the update check (one small request to GitHub when the app starts; can be turned off in Settings)
  - the optional **network watcher** (off by default; pings your router and 1.1.1.1 every 2 seconds, a few KB
    per minute)
  - speed tests, only when you press the button (about 140 MB, via speed.cloudflare.com)
- **Wi-Fi signal %** can only be read with Windows' `netsh` tool, which makes Windows show its location icon
  ("Network Command Shell is using your location"). PC Monitor never reads your location; it only checks signal
  once a minute while the network watcher is on.

## Keys

| Key | What it does |
|---|---|
| **Esc** | Back to the Dashboard |
| **Ctrl + ,** | Settings |
| **Ctrl + Shift + S** | Save the current window size and position as the one it always opens at |
| **Ctrl + Alt + O** | Overlay mode on/off (works from any window) |
| **Ctrl + Alt + M** | Mini mode on/off (works from any window) |
| **F11** | Fullscreen |

## For developers

Run from source (Python 3.11):

```
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv\Scripts\pythonw.exe app.py
```

Build the shareable zip with `.venv\Scripts\python.exe build.py` (output in `dist\`). A file named
`developer_copy` next to the code makes that copy keep its data in its own folder and never self-update.

| File | What it is |
|---|---|
| `app.py` | Main window, dashboard and the logic tying everything together |
| `widgets.py`, `theme.py`, `status.py` | Cards and graphs, colour themes, health colours and thresholds |
| `sensors.py`, `hardware.py` | Reading Windows, NVIDIA and HWiNFO; CPU limits per model |
| `reports.py`, `settings_page.py`, `setup_page.py`, `recap.py` | The other screens |
| `analysis.py`, `session.py`, `export.py` | Game sessions, recap analysis and exports |
| `tracker.py`, `netwatch.py`, `db.py` | App tracker, network watcher, Wi-Fi score and local history |
| `updater.py`, `version.py`, `startup.py`, `config.py` | Updates, version, start with Windows, settings and paths |
| `alerts.py`, `tray.py`, `mini.py`, `hotkeys.py`, `datalog.py` | One file per smaller feature |
