""""What's eating my PC": records which apps use the most CPU and memory.

Every SAMPLE_SECONDS it reads each running program's CPU and memory (the same numbers Task
Manager shows), groups processes by app (all of Chrome's processes count as "Chrome"), and
once a minute adds them to that day's totals in history.db. Only app names and numbers are
stored: never window titles, websites or anything you're doing inside an app.
"""
import ctypes
import struct
import threading
import time

import psutil

from db import connect, prune
from sensors import MY_APPS

ntdll = ctypes.windll.ntdll
SYSTEM_PROCESS_INFORMATION = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


def all_processes(buffer: list) -> list[tuple[int, str, int, int]]:
    """Every process in one Windows call (the same one Task Manager uses).

    Returns (pid, name, cpu time in 100 ns units, private memory in bytes) for each process.
    buffer is a one-item list holding a reusable ctypes buffer.
    """
    size = ctypes.c_ulong()
    while True:
        buf = buffer[0]
        status = ntdll.NtQuerySystemInformation(SYSTEM_PROCESS_INFORMATION, buf, len(buf),
                                                ctypes.byref(size)) & 0xFFFFFFFF
        if status != STATUS_INFO_LENGTH_MISMATCH:
            break
        buffer[0] = ctypes.create_string_buffer(size.value + 256 * 1024)
    if status != 0:
        return []
    procs, offset = [], 0
    while True:
        # Field offsets of SYSTEM_PROCESS_INFORMATION on 64-bit Windows
        next_offset, = struct.unpack_from("<I", buf, offset)
        private_mem, = struct.unpack_from("<q", buf, offset + 8)
        user, kernel = struct.unpack_from("<qq", buf, offset + 40)
        name_len, = struct.unpack_from("<H", buf, offset + 56)
        name_ptr, = struct.unpack_from("<Q", buf, offset + 64)
        pid, = struct.unpack_from("<Q", buf, offset + 80)
        name = ctypes.wstring_at(name_ptr, name_len // 2) if name_ptr else ""
        procs.append((pid, name, user + kernel, private_mem))
        if not next_offset:
            return procs
        offset += next_offset

SAMPLE_SECONDS = 5
TIMELINE_TOP = 8   # apps per minute kept for the timeline chart

# Friendlier names for common processes (lowercase name without .exe -> display name)
FRIENDLY = {
    "chrome": "Chrome", "msedge": "Edge", "msedgewebview2": "Edge WebView", "firefox": "Firefox",
    "brave": "Brave", "opera": "Opera", "discord": "Discord", "claude": "Claude",
    "spotify": "Spotify", "steam": "Steam", "steamwebhelper": "Steam",
    "epicgameslauncher": "Epic Games Launcher", "fortniteclient-win64-shipping": "Fortnite",
    "explorer": "File Explorer", "dwm": "Windows desktop", "memcompression":
    "Windows memory", "system": "Windows", "svchost": "Windows services",
    "msmpeng": "Microsoft Defender", "searchhost": "Windows Search", "pythonw": "PC Monitor",
    "services": "Windows services", "webwallpaper64": "Wallpaper Engine",
    "medal": "Medal", "medalencoder": "Medal", "icue": "Corsair iCUE",
    "icuedevicepluginhost": "Corsair iCUE", "razerappengine": "Razer",
    "wallpaper64": "Wallpaper Engine", "wallpaper32": "Wallpaper Engine",
    "hwinfo64": "HWiNFO", "code": "VS Code", "obs64": "OBS", "nvidia app": "NVIDIA App",
    "nvcontainer": "NVIDIA services", "nvdisplay.container": "NVIDIA services",
}
SKIP = {"system idle process", "idle", ""}


def app_name(process_name: str) -> str:
    base = process_name[:-4] if process_name.lower().endswith(".exe") else process_name
    return FRIENDLY.get(base.lower(), base)


def my_app_names() -> set[str]:
    """Your apps' display names, lowercase (from Settings → About your PC)."""
    return {app_name(p).lower() for p in MY_APPS}


class AppSampler:
    """Each app's CPU and memory use right now. Used by the tracker and the App usage tile."""

    def __init__(self):
        self.cores = psutil.cpu_count() or 1
        self.buffer = [ctypes.create_string_buffer(1024 * 1024)]
        self.last_cpu: dict[tuple[int, str], int] = {}  # (pid, name) -> cpu time at last sample
        self.last_time = None

    def sample(self) -> dict[str, list[float]]:
        """app -> [cpu % of the whole CPU, memory bytes] since the previous sample."""
        now = time.perf_counter()
        elapsed = (now - self.last_time) if self.last_time else None
        self.last_time = now
        usage: dict[str, list[float]] = {}
        current = {}
        for pid, name, cpu_time, mem in all_processes(self.buffer):
            key = (pid, name)
            current[key] = cpu_time
            if not pid or name.lower() in SKIP:
                continue
            before = self.last_cpu.get(key)
            if before is None or not elapsed:  # new process: no CPU reading yet
                continue
            cpu = max(0, cpu_time - before) / 1e7 / elapsed / self.cores * 100
            u = usage.setdefault(app_name(name), [0.0, 0.0])
            u[0] += cpu
            u[1] += mem
        self.last_cpu = current
        return usage


class AppTracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="app-tracker")
        self.stop_event = threading.Event()
        self.sampler = AppSampler()
        self.cores = self.sampler.cores
        # The minute still being collected (not saved yet), for live Reports:
        # (minute, {app: [n, cpu_sum, cpu_peak, cpu_at, mem_sum, mem_peak, mem_at]}) or None
        self.live = None

    def stop(self):
        self.stop_event.set()

    def sample(self) -> dict[str, list[float]]:
        return self.sampler.sample()

    def run(self):
        con = connect()
        prune(con)
        minute = None
        acc: dict[str, list[float]] = {}  # app -> [n, cpu_sum, cpu_peak, cpu_at, mem_sum, mem_peak, mem_at]
        self.sample()  # prime CPU counters
        while not self.stop_event.wait(SAMPLE_SECONDS):
            try:
                now = time.time()
                usage = self.sample()
                m = int(now // 60 * 60)
                if minute is not None and m != minute and acc:
                    self.live = None  # cleared first so Reports never counts a minute twice
                    self.flush(con, minute, acc)
                    acc = {}
                minute = m
                for app, (cpu, mem) in usage.items():
                    a = acc.setdefault(app, [0, 0.0, -1.0, now, 0.0, -1.0, now])
                    a[0] += 1
                    a[1] += cpu
                    a[4] += mem
                    if cpu > a[2]:
                        a[2], a[3] = cpu, now
                    if mem > a[5]:
                        a[5], a[6] = mem, now
                self.live = (minute, {app: list(a) for app, a in acc.items()})
            except Exception:
                continue  # never let one bad sample stop the tracker
        self.live = None
        if acc and minute is not None:
            self.flush(con, minute, acc)
        con.close()

    def flush(self, con, minute: int, acc: dict[str, list[float]]):
        day = time.strftime("%Y-%m-%d", time.localtime(minute))
        for app, (n, cpu_sum, cpu_peak, cpu_at, mem_sum, mem_peak, mem_at) in acc.items():
            cpu_seconds = cpu_sum / 100 * self.cores * SAMPLE_SECONDS
            con.execute("""
                INSERT INTO app_day VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (day, app) DO UPDATE SET
                    samples = samples + excluded.samples,
                    cpu_sum = cpu_sum + excluded.cpu_sum,
                    cpu_peak_at = CASE WHEN excluded.cpu_peak > cpu_peak THEN excluded.cpu_peak_at
                                       ELSE cpu_peak_at END,
                    cpu_peak = MAX(cpu_peak, excluded.cpu_peak),
                    mem_sum = mem_sum + excluded.mem_sum,
                    mem_peak_at = CASE WHEN excluded.mem_peak > mem_peak THEN excluded.mem_peak_at
                                       ELSE mem_peak_at END,
                    mem_peak = MAX(mem_peak, excluded.mem_peak),
                    cpu_seconds = cpu_seconds + excluded.cpu_seconds""",
                        (day, app, n, cpu_sum, cpu_peak, int(cpu_at), mem_sum, mem_peak,
                         int(mem_at), cpu_seconds))
        averages = {app: (a[1] / a[0], a[4] / a[0]) for app, a in acc.items()}
        top = set(sorted(averages, key=lambda k: averages[k][0], reverse=True)[:TIMELINE_TOP])
        top |= set(sorted(averages, key=lambda k: averages[k][1], reverse=True)[:TIMELINE_TOP])
        mine = my_app_names()
        top |= {app for app in averages if app.lower() in mine}  # your apps: always
        con.executemany("INSERT INTO app_minute VALUES (?, ?, ?, ?)",
                        [(minute, app, *averages[app]) for app in top])
        con.execute("INSERT OR IGNORE INTO tracked_minute VALUES (?)", (minute,))
        con.commit()
