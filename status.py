"""Status colours: how healthy a reading is. Thresholds you may want to tweak are here."""

COOL = "#4f9dff"   # blue:   well within the safe range for what the part is doing
GOOD = "#3dd68c"   # green:  normal
WARM = "#ff9f43"   # orange: getting high
HOT = "#ff5c5c"    # red:    too high
NEVER = float("inf")

# Temperatures in °C. For CPU and GPU the blue cut-off scales with load: at idle it's
# cool_idle, at 100% load it's cool_load, so blue means "cool for how hard it's working".
# The CPU's warm/hot levels are set from its own temperature limit by configure_cpu().
CPU_TEMP = {"warm": 75, "hot": 85, "cool_idle": 40, "cool_load": 65}
GPU_TEMP = {"warm": 75, "hot": 83, "cool_idle": 35, "cool_load": 65}
DRIVE_TEMP = {"warm": 60, "hot": 70, "cool": 40}                        # NVMe drives throttle ~80 °C
OTHER_TEMP = {"warm": 70, "hot": 85, "cool": 40}
# This PC's CPU: the temperature it slows itself down at, and its normal full-load power limit
CPU = {"throttle_c": 95, "power_w": 142, "model": "CPU"}


def configure_cpu(limits: dict):
    """Fit the CPU colours to this PC's processor: orange 20 °C below its limit, red 10 °C below."""
    CPU.update(limits)
    CPU_TEMP["warm"] = limits["throttle_c"] - 20
    CPU_TEMP["hot"] = limits["throttle_c"] - 10

# Frames per second: higher is better.
FPS = {"cool": 144, "good": 60, "warm": 30}


def status(value: float, warm: float, hot: float, cool_below: float = -NEVER) -> str:
    if value >= hot:
        return HOT
    if value >= warm:
        return WARM
    if value < cool_below:
        return COOL
    return GOOD


def temp_status(temp: float, limits: dict, load_pct: float | None = None) -> str:
    if load_pct is None:
        cool = limits["cool"]
    else:
        cool = limits["cool_idle"] + (limits["cool_load"] - limits["cool_idle"]) * load_pct / 100
    return status(temp, limits["warm"], limits["hot"], cool)


def headroom_status(pct: float) -> str:
    """CPU usage, RAM, VRAM: running out is what hurts."""
    return status(pct, warm=75, hot=90, cool_below=25)


def busy_status(pct: float) -> str:
    """GPU usage and power: running flat out is normal and safe, so never orange or red."""
    return status(pct, warm=NEVER, hot=NEVER, cool_below=30)


def wifi_status(score: float) -> str:
    """Wi-Fi strength (0-100%): higher is better."""
    if score >= 85:
        return COOL
    if score >= 60:
        return GOOD
    if score >= 35:
        return WARM
    return HOT


def fps_status(fps: float) -> str:
    if fps >= FPS["cool"]:
        return COOL
    if fps >= FPS["good"]:
        return GOOD
    if fps >= FPS["warm"]:
        return WARM
    return HOT
