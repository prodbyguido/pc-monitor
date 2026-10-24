"""Read-only data sources for the monitor.

- Windows (psutil): CPU usage, RAM, disk and network byte counters
- NVIDIA driver (NVML via nvidia-ml-py): GPU usage, temperature, VRAM, power
- HWiNFO (its "Gadget" registry values): CPU temperature and anything else you tick

Nothing here writes anywhere or opens a network connection.
"""
import ctypes
import re
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass

import psutil

try:
    import pynvml
except ImportError:
    pynvml = None

HWINFO_KEY = r"Software\HWiNFO64\VSB"
HWINFO_CHECK_SECONDS = 5

# NVML clock-event reason bits (why the GPU is running below full clocks)
GPU_POWER_CAP = 0x04
GPU_HW_SLOWDOWN = 0x08 | 0x80          # hardware slowdown / power brake
GPU_THERMAL = 0x20 | 0x40              # software / hardware thermal slowdown

# Your own apps (lowercase .exe names), from Settings → About your PC: never treated as games,
# and always given a full minute-by-minute timeline in Reports → Apps. Kept up to date by the app.
MY_APPS: set[str] = set()


def set_my_apps(names: set[str]):
    MY_APPS.clear()
    MY_APPS.update(names)


def is_not_game(process_name: str) -> bool:
    name = process_name.lower()
    return name in NOT_GAMES or name in MY_APPS


# Apps that are never treated as games: they're ignored by the FPS card and never start a
# game session (their CPU and memory use is still tracked like any other app). Add more here
# (lowercase, with .exe) if something else gets mistaken for a game.
NOT_GAMES = {
    # Windows
    "explorer.exe", "dwm.exe", "searchhost.exe", "shellexperiencehost.exe",
    "applicationframehost.exe", "taskmgr.exe", "startmenuexperiencehost.exe", "lockapp.exe",
    # This monitor and HWiNFO
    "python.exe", "pythonw.exe", "hwinfo64.exe",
    # Everyday apps that redraw a lot
    "claude.exe", "discord.exe", "spotify.exe", "code.exe", "obs64.exe",
    # Clipping, recording and overlay apps
    "medal.exe", "medalencoder.exe", "overwolf.exe", "gamebar.exe", "gamebarftserver.exe",
    "xboxgamebarwidgets.exe", "nvidia share.exe", "nvsphelper64.exe",
    # Device and RGB software
    "icue.exe", "icuedevicepluginhost.exe", "razerappengine.exe", "razer synapse 3.exe",
    "lghub.exe", "signalrgb.exe",
    # Animated wallpapers
    "wallpaper32.exe", "wallpaper64.exe", "webwallpaper32.exe", "webwallpaper64.exe",
    "lively.exe",
    # Web browsers
    "chrome.exe", "msedge.exe", "msedgewebview2.exe", "firefox.exe", "brave.exe", "opera.exe",
    # Game launchers and overlays (the games they start still count)
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "nvidia app.exe",
    "nvidia overlay.exe",
}


@dataclass
class HwinfoReading:
    sensor: str
    label: str
    value: float
    unit: str


@dataclass
class Sample:
    cpu_total: float
    cpu_per_core: list[float]
    ram_used_gb: float
    ram_total_gb: float
    ram_percent: float
    disk_read_mb: float
    disk_write_mb: float
    net_down_mbit: float
    net_up_mbit: float
    gpu: dict | None
    hwinfo: list[HwinfoReading] | None


# HWiNFO writes its shared readings to either your user section of the registry or the
# machine-wide one, depending on how it was started. Both can exist, and the one it isn't using
# keeps old, frozen values, so PC Monitor reads both and uses whichever is actually updating.
HWINFO_ROOTS = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
_last_change: dict[int, tuple[tuple, float]] = {}  # root -> (latest values, when they last changed)


def read_hwinfo() -> list[HwinfoReading] | None:
    """Values HWiNFO reports to Gadget, or None if it isn't sharing anything."""
    now = time.monotonic()
    best, best_changed = None, float("-inf")
    for root in HWINFO_ROOTS:
        readings = _read_hwinfo_key(root)
        if readings is None:
            continue
        values = tuple((r.label, r.value) for r in readings)
        seen = _last_change.get(root)
        changed = seen[1] if seen and seen[0] == values else (now if seen else float("-inf"))
        _last_change[root] = (values, changed)
        if best is None or changed > best_changed:
            best, best_changed = readings, changed
    return best


def _read_hwinfo_key(root) -> list[HwinfoReading] | None:
    try:
        key = winreg.OpenKey(root, HWINFO_KEY, 0, winreg.KEY_READ)
    except OSError:
        return None

    entries: dict[int, dict[str, str]] = {}
    with key:
        i = 0
        while True:
            try:
                name, data, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            m = re.fullmatch(r"(Sensor|Label|Value|ValueRaw)(\d+)", name)
            if m:
                entries.setdefault(int(m.group(2)), {})[m.group(1)] = str(data)

    readings = []
    for idx in sorted(entries):
        e = entries[idx]
        raw = e.get("ValueRaw", "").strip()
        if raw.lower() in ("yes", "no"):  # e.g. HWiNFO's "Thermal Throttling" flags
            value = 1.0 if raw.lower() == "yes" else 0.0
        else:
            try:
                value = float(raw.replace(",", "."))
            except ValueError:
                continue
        display = e.get("Value", "")
        unit = display.split(" ", 1)[1].strip() if " " in display else ""
        readings.append(HwinfoReading(e.get("Sensor", ""), e.get("Label", ""), value, unit))
    return readings


def find_reading(readings: list[HwinfoReading] | None, label_part: str) -> HwinfoReading | None:
    for r in readings or []:
        if label_part.lower() in r.label.lower():
            return r
    return None


def is_cpu_temp(r: HwinfoReading) -> bool:
    """The main CPU temperature: "CPU (Tctl/Tdie)" on AMD, "CPU Package" on Intel."""
    return "tctl/tdie" in r.label.lower() or (r.label == "CPU Package" and r.unit == "°C")


def cpu_temp_reading(readings: list[HwinfoReading] | None) -> HwinfoReading | None:
    candidates = [r for r in readings or [] if is_cpu_temp(r)]
    return candidates[0] if candidates else None


# For graphics cards NVIDIA's driver can't read (AMD, Intel): HWiNFO's GPU readings instead.
# Each needs "Report value in Gadget" ticked in HWiNFO. First label found wins.
HWINFO_GPU_LABELS = {
    "util": ("GPU Utilization", "GPU Core Load", "GPU Load", "GPU D3D Usage"),
    "temp": ("GPU Temperature",),
    "power_w": ("Total Board Power", "GPU ASIC Power", "GPU PPT", "GPU Power", "GPU Package Power"),
    "clock_mhz": ("GPU Clock", "GPU Core Clock"),
    "vram_used_mb": ("GPU D3D Memory Dedicated", "GPU Memory Allocated", "GPU Memory Used"),
    "vram_pct": ("GPU Memory Usage",),
}


def is_hwinfo_gpu(r: HwinfoReading) -> bool:
    return r.sensor.startswith("GPU [")


def gpu_from_hwinfo(readings: list[HwinfoReading] | None) -> dict | None:
    """GPU stats from HWiNFO, shaped like Gpu.read()'s. None if the basics aren't ticked."""
    found = {}
    for key, labels in HWINFO_GPU_LABELS.items():
        for label in labels:
            r = next((r for r in readings or [] if is_hwinfo_gpu(r) and r.label == label), None)
            if r:
                found[key] = r
                break
    if "util" not in found or "temp" not in found:
        return None
    used_mb = found["vram_used_mb"].value if "vram_used_mb" in found else None
    if used_mb is not None and found["vram_used_mb"].unit.upper() == "GB":
        used_mb *= 1024
    pct = found["vram_pct"].value if "vram_pct" in found else None
    total_gb = used_mb / 1024 / (pct / 100) if used_mb and pct else None
    name_parts = found["util"].sensor.split(": ")
    return {"util": found["util"].value, "temp": found["temp"].value,
            "vram_used_gb": used_mb / 1024 if used_mb is not None else None,
            "vram_total_gb": total_gb,
            "power_w": found["power_w"].value if "power_w" in found else 0.0,
            "clock_mhz": round(found["clock_mhz"].value) if "clock_mhz" in found else 0,
            "fan_pct": None, "throttle": 0,
            "name": name_parts[1] if len(name_parts) > 1 else "GPU"}


def foreground_process() -> str | None:
    """The .exe name of the window in front, e.g. "rocketleague.exe" (lowercase)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return None


def foreground_app() -> str | None:
    """Name of the app in the foreground (used to name game sessions), e.g. "Cyberpunk2077"."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        name = psutil.Process(pid.value).name()
    except psutil.Error:
        return None
    if is_not_game(name):
        return None
    return name[:-4] if name.lower().endswith(".exe") else name


def cpu_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        return "CPU"


class Gpu:
    """First NVIDIA GPU via NVML. `error` is set if it couldn't be opened."""

    def __init__(self):
        self.handle = None
        self.from_hwinfo = False   # True when stats come from HWiNFO (non-NVIDIA cards)
        self.name = "GPU"
        self.vram_total_gb = 16.0
        self.power_limit_w = 300.0
        self.error = None
        if pynvml is None:
            self.error = "nvidia-ml-py is not installed"
            return
        try:
            pynvml.nvmlInit()
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(self.handle)
            self.name = name.decode() if isinstance(name, bytes) else name
            self.vram_total_gb = pynvml.nvmlDeviceGetMemoryInfo(self.handle).total / 1024**3
            self.power_limit_w = pynvml.nvmlDeviceGetEnforcedPowerLimit(self.handle) / 1000
        except pynvml.NVMLError as exc:
            self.handle = None
            self.error = f"NVML: {exc}"

    def read(self) -> dict | None:
        if self.handle is None:
            return None
        h = self.handle
        try:
            data = {
                "util": pynvml.nvmlDeviceGetUtilizationRates(h).gpu,
                "temp": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
                "vram_used_gb": pynvml.nvmlDeviceGetMemoryInfo(h).used / 1024**3,
                "power_w": pynvml.nvmlDeviceGetPowerUsage(h) / 1000,
                "clock_mhz": pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS),
            }
        except pynvml.NVMLError:
            return None
        try:
            data["fan_pct"] = pynvml.nvmlDeviceGetFanSpeed(h)
        except pynvml.NVMLError:
            data["fan_pct"] = None
        reasons = (getattr(pynvml, "nvmlDeviceGetCurrentClocksEventReasons", None)
                   or getattr(pynvml, "nvmlDeviceGetCurrentClocksThrottleReasons", None))
        try:
            data["throttle"] = reasons(h) if reasons else 0
        except pynvml.NVMLError:
            data["throttle"] = 0
        return data

    def close(self):
        if self.handle is not None:
            try:
                pynvml.nvmlShutdown()
            except pynvml.NVMLError:
                pass
            self.handle = None


class Sampler:
    def __init__(self):
        psutil.cpu_percent(percpu=True)  # first call primes the counters
        self.cpu_name = cpu_name()
        self.physical_cores = psutil.cpu_count(logical=False) or 0
        self.logical_cores = psutil.cpu_count() or 1
        self.gpu = Gpu()
        self._last_t = time.monotonic()
        self._disk = psutil.disk_io_counters()
        self._net = psutil.net_io_counters()
        self._hwinfo_checked = float("-inf")
        self._hwinfo_running = False

    def _hwinfo_is_running(self, now: float) -> bool:
        # HWiNFO can leave its last values in the registry after closing,
        # so only trust them while the process is actually running.
        if now - self._hwinfo_checked >= HWINFO_CHECK_SECONDS:
            self._hwinfo_checked = now
            self._hwinfo_running = any(
                (p.info["name"] or "").lower().startswith("hwinfo")
                for p in psutil.process_iter(["name"])
            )
        return self._hwinfo_running

    def sample(self) -> Sample:
        now = time.monotonic()
        dt = max(now - self._last_t, 1e-3)
        self._last_t = now

        per_core = psutil.cpu_percent(percpu=True)
        vm = psutil.virtual_memory()

        disk = psutil.disk_io_counters()
        read_b = write_b = 0.0
        if disk and self._disk:
            read_b = max(0, disk.read_bytes - self._disk.read_bytes) / dt
            write_b = max(0, disk.write_bytes - self._disk.write_bytes) / dt
        self._disk = disk

        net = psutil.net_io_counters()
        down_b = max(0, net.bytes_recv - self._net.bytes_recv) / dt
        up_b = max(0, net.bytes_sent - self._net.bytes_sent) / dt
        self._net = net

        hwinfo = read_hwinfo() if self._hwinfo_is_running(now) else None
        gpu = self.gpu.read()
        if gpu is None and self.gpu.handle is None:  # not NVIDIA: try HWiNFO's GPU readings
            gpu = gpu_from_hwinfo(hwinfo)
            if gpu:
                self.gpu.name = gpu["name"]
                if gpu["vram_total_gb"]:
                    self.gpu.vram_total_gb = gpu["vram_total_gb"]
                if gpu["vram_used_gb"] is None:
                    gpu["vram_used_gb"] = 0.0
                self.gpu.from_hwinfo = True

        return Sample(
            cpu_total=sum(per_core) / len(per_core),
            cpu_per_core=per_core,
            ram_used_gb=vm.used / 1024**3,
            ram_total_gb=vm.total / 1024**3,
            ram_percent=vm.percent,
            disk_read_mb=read_b / 1e6,
            disk_write_mb=write_b / 1e6,
            net_down_mbit=down_b * 8 / 1e6,
            net_up_mbit=up_b * 8 / 1e6,
            gpu=gpu,
            hwinfo=hwinfo,
        )

    def close(self):
        self.gpu.close()
