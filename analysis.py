"""Post-game analysis: how your PC handled a game, what held it back, and how to fix it.

Works from one sample per second recorded during the session. Each sample is a dict with
these keys (any may be missing or None):
    t       time (Unix seconds)            fps / rfps   displayed / rendered FPS
    ft      frame time (ms)                gb / cb      GPU busy / CPU busy per frame (ms)
    gpu     GPU usage %                    gt / gp      GPU temp °C / power W
    thr     GPU slow-down reason bits      cpu / core   CPU total % / busiest core %
    ct / cp CPU temp °C / power W          cthr         CPU thermal throttling (bool)
    vram    VRAM used %                    ram          RAM used %
    disk    disk read+write MB/s           ping / lost  internet ping ms / ping lost (bool)
    bg      {app: CPU %} for other busy apps, memtop  the app using the most RAM
    away    another window was in front of the game (tabbed out)

Seconds when you were tabbed out, or when the GPU was nearly idle compared with the rest of
the game (menus, pause and loading screens), aren't counted as gameplay: they're left out of
FPS stats and drop detection, along with a few seconds either side of them.
"""
from bisect import bisect_left
from dataclasses import dataclass, field
from statistics import median, pstdev

from hardware import cpu_limits
from sensors import GPU_HW_SLOWDOWN, GPU_POWER_CAP, GPU_THERMAL

GOOD, INFO, WARN, BAD = "good", "info", "warn", "bad"
RANK = {GOOD: 0, INFO: 1, WARN: 2, BAD: 3}

COMMON_CAPS = [30, 60, 75, 90, 120, 138, 141, 144, 157, 160, 165, 170, 180, 200, 224, 225, 237, 240]
LOADING_SECONDS = 90         # drops this early are usually loading / shader building
DIP_RATIO = 0.65             # a "big drop" = rendered FPS below 65% of the last 20 s typical
MIN_GAME_SAMPLES = 30        # seconds of frame data needed for an FPS analysis
IDLE_GPU_PCT = 25            # GPU below this (and well below the game's usual) = not really playing
IDLE_GPU_RATIO = 0.4
GUARD_SECONDS = 3            # also ignore drops this close to a tab-out / menu


@dataclass
class Finding:
    severity: str
    title: str
    detail: str
    fix: str = ""
    factor: str | None = None   # short name for "Limited by", e.g. "VRAM", "CPU", "Medal"


@dataclass
class Analysis:
    verdict: str
    severity: str
    findings: list[Finding]
    stats: dict = field(default_factory=dict)
    dips: list[float] = field(default_factory=list)   # times of big frame drops
    bottleneck: str = "unknown"
    limiter: tuple = ("—", "", INFO)                  # (what, explanation, severity)
    away: list[float] = field(default_factory=list)   # times not counted as gameplay


def not_playing(samples: list[dict]) -> set[float]:
    """Times of seconds that aren't real gameplay: tabbed out, no FPS, or GPU nearly idle."""
    gpu = [s["gpu"] for s in samples if s.get("fps") and s.get("gpu") is not None and not s.get("away")]
    usual = median(gpu) if gpu else None
    idle_below = min(IDLE_GPU_PCT, usual * IDLE_GPU_RATIO) if usual else None
    out = set()
    for s in samples:
        if not s.get("fps") or s.get("away"):
            out.add(s["t"])
        elif idle_below is not None and s.get("gpu") is not None and s["gpu"] < idle_below:
            out.add(s["t"])
    return out


def _values(samples, key):
    return [s[key] for s in samples if s.get(key) is not None]


def _pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))]


def _share(samples, test) -> float:
    return sum(1 for s in samples if test(s)) / len(samples) if samples else 0.0


# --- the individual checks -------------------------------------------------------------
def _find_dips(game: list[dict], start: float, skip: set[float]) -> list[dict]:
    """Big sudden frame-rate drops during real gameplay, each with its most likely cause."""
    skip_near = sorted(skip)
    events, last_t = [], None
    for i, s in enumerate(game):
        if _near(s["t"], skip_near):
            continue
        window = [g.get("rfps") or g["fps"] for g in game[max(0, i - 20):i]]
        if len(window) < 5:
            continue
        typical = median(window)
        now = s.get("rfps") or s["fps"]
        if typical < 30 or now >= typical * DIP_RATIO:
            continue
        if last_t is not None and s["t"] - last_t <= 2:  # same drop, still going
            last_t = s["t"]
            continue
        last_t = s["t"]
        nearby = game[max(0, i - 1):i + 2]
        events.append({"t": s["t"], "fps": now, "typical": typical,
                       "cause": _dip_cause(nearby, s["t"] - start)})
    return events


def _near(t: float, times: list[float]) -> bool:
    """Is t within GUARD_SECONDS of any time in the sorted list?"""
    i = bisect_left(times, t - GUARD_SECONDS)
    return i < len(times) and times[i] <= t + GUARD_SECONDS


def _dip_cause(nearby: list[dict], seconds_in: float) -> str:
    def any_(test):
        return any(test(s) for s in nearby)
    if any_(lambda s: (s.get("vram") or 0) >= 95):
        return "vram"
    if any_(lambda s: (s.get("ram") or 0) >= 90):
        return "ram"
    if any_(lambda s: (s.get("thr") or 0) & GPU_THERMAL or s.get("cthr")):
        return "thermal"
    if seconds_in < LOADING_SECONDS or any_(lambda s: (s.get("disk") or 0) >= 80):
        return "loading"
    busy_bg = [(app, pct) for s in nearby for app, pct in (s.get("bg") or {}).items() if pct >= 12]
    if busy_bg:
        return "background:" + max(busy_bg, key=lambda x: x[1])[0]
    if any_(lambda s: (s.get("core") or 0) >= 97):
        return "cpu"
    return "unknown"


DIP_FIXES = {
    "vram": ("your graphics card's memory (VRAM) was full",
             "Lower texture quality one step. Also close other apps that use the GPU while gaming "
             "(browsers with videos, Wallpaper Engine, Medal's recording preview)."),
    "ram": ("your PC's memory (RAM) was nearly full",
            "Close memory-hungry apps while you play (often browsers with many tabs). If it fills "
            "up even with little else open, the game may simply need more RAM than this PC has "
            "(16 GB is tight for many new games; 32 GB is comfortable)."),
    "thermal": ("a part was overheating and slowing itself down",
                "See the overheating finding below for what to check."),
    "loading": ("the game was loading data or building shaders",
                "Normal the first time you play after a game or driver update; it improves as the "
                "game caches shaders. Games load fastest from an internal NVMe SSD, rather than an "
                "external drive or a hard drive."),
    "cpu": ("your CPU was maxed out at that moment",
            "Lower CPU-heavy settings (view distance, crowds/NPC density, physics) or cap your FPS "
            "a little below your average so the CPU has headroom for busy moments."),
    "unknown": ("no clear cause in the once-a-second readings",
                "Often the game itself during busy moments, or very short hitches. Capping FPS a "
                "little below your average (in-game or NVIDIA Control Panel) usually makes frame "
                "pacing steadier."),
}


def _detect_cap(fps: list[float], gpu_med: float | None):
    """FPS sitting flat at a limit (V-Sync, Reflex, in-game cap)?"""
    if len(fps) < MIN_GAME_SAMPLES:
        return None
    med = median(fps)
    # Spread of the middle 80% of seconds, so a few wobbles don't hide an obvious cap.
    spread = (_pct(fps, 90) - _pct(fps, 10)) / med if med else 1
    near = min(COMMON_CAPS, key=lambda c: abs(c - med))
    if spread < 0.06 and abs(near - med) / near < 0.03 and (gpu_med is None or gpu_med < 92):
        return round(med)  # the real level it sat at, e.g. 237 for NVIDIA Reflex on 240 Hz
    return None


def _bottleneck(game: list[dict], cap) -> tuple[str, dict]:
    gpu_ratio = [s["gb"] / s["ft"] for s in game if s.get("gb") and s.get("ft")]
    cpu_ratio = [s["cb"] / s["ft"] for s in game if s.get("cb") and s.get("ft")]
    gpu_use = median(_values(game, "gpu")) if _values(game, "gpu") else None
    core = median(_values(game, "core")) if _values(game, "core") else None
    info = {"gpu_use": gpu_use, "core": core,
            "gpu_ratio": median(gpu_ratio) if len(gpu_ratio) >= len(game) / 2 else None,
            "cpu_ratio": median(cpu_ratio) if len(cpu_ratio) >= len(game) / 2 else None}
    if cap:
        return "cap", info
    r, c = info["gpu_ratio"], info["cpu_ratio"]
    if (r is not None and r >= 0.88) or (r is None and gpu_use is not None and gpu_use >= 94):
        return "gpu", info
    gpu_waiting = (r is not None and r <= 0.75) or (r is None and gpu_use is not None and gpu_use < 85)
    cpu_busy = (c is not None and c >= 0.85) or (core is not None and core >= 85)
    if gpu_waiting and cpu_busy:
        return "cpu", info
    if gpu_waiting:
        return "engine", info
    return "balanced", info


# --- the whole analysis ------------------------------------------------------------------
def analyze(samples: list[dict], meta: dict) -> Analysis:
    findings: list[Finding] = []
    if not samples:
        return Analysis("No data recorded", INFO, [])
    start = samples[0]["t"]
    skip = not_playing(samples)
    game = [s for s in samples if s.get("fps") and s["t"] not in skip]   # real gameplay only
    stats = {
        "duration": samples[-1]["t"] - start,
        "away_seconds": len(skip),
        "away_tabbed": sum(1 for s in samples if s.get("away")),
        "peak_cpu_temp": max(_values(samples, "ct"), default=None),
        "peak_gpu_temp": max(_values(samples, "gt"), default=None),
        "peak_cpu_power": max(_values(samples, "cp"), default=None),
        "peak_gpu_power": max(_values(samples, "gp"), default=None),
    }
    dips: list[dict] = []
    bottleneck = "unknown"
    if stats["away_seconds"] >= 30:
        tabbed = sum(1 for s in samples if s.get("away"))
        no_fps = sum(1 for s in samples if not s.get("fps"))
        idle = max(0, len(skip) - tabbed - no_fps)
        reasons = [f"{_minutes(n)} {why}" for n, why in (
            (tabbed, "tabbed out"), (idle, "in menus, paused or loading"),
            (no_fps, "with no FPS reading (the frame counter was measuring a different app)"))
            if n >= 10]
        findings.append(Finding(
            INFO, f"{_minutes(stats['away_seconds'])} not counted as gameplay",
            "Greyed out on the chart and left out of the FPS numbers, so it doesn't look like "
            "frame drops: " + "; ".join(reasons) + "."))

    if len(game) >= MIN_GAME_SAMPLES:
        fps = [s["fps"] for s in game]
        rfps = [s.get("rfps") or s["fps"] for s in game]
        stats.update(avg_fps=sum(fps) / len(fps), avg_rfps=sum(rfps) / len(rfps),
                     low1=_pct(rfps, 1))
        multiplier = round(median(f / r for f, r in zip(fps, rfps) if r)) if rfps else 1
        frame_gen = median(f / r for f, r in zip(fps, rfps) if r) >= 1.5
        stats["frame_gen"] = multiplier if frame_gen else None

        dips = _find_dips(game, start, skip)
        stats["dips"] = len(dips)
        steady = [s for s in game if not any(abs(s["t"] - d["t"]) <= 2 for d in dips)]
        gpu_med = median(_values(steady, "gpu")) if _values(steady, "gpu") else None
        cap = _detect_cap([s["fps"] for s in steady], gpu_med)
        bottleneck, b = _bottleneck(steady or game, cap)
        stats["bottleneck"] = bottleneck
        findings += _bottleneck_findings(bottleneck, b, cap, stats, meta)
        findings += _frame_gen_findings(stats)
        findings += _dip_findings(dips, stats)
    else:
        findings.append(Finding(
            INFO, "No frame-rate data for this session",
            "PC Monitor saw a busy GPU but no game frame rate, so it can only check temperatures "
            "and memory.",
            "In HWiNFO → Configure Sensors → HWiNFO Gadget, tick the PresentMon Framerate values "
            "while a game is running."))

    cpu_maxed = _cpu_load_findings(samples)
    if cpu_maxed:  # don't also call it "well balanced" when every core was pinned
        findings = [f for f in findings if f.title != "Well balanced"]
    findings += cpu_maxed
    findings += _thermal_findings(samples, stats, meta)
    findings += _memory_findings(samples, meta)
    findings += _background_findings(game or samples, meta)
    findings += _network_findings(samples)

    if not any(RANK[f.severity] >= RANK[WARN] for f in findings):
        findings.append(Finding(GOOD, "No problems found",
                                "Temperatures, memory and background apps all looked fine."))
    findings.sort(key=lambda f: -RANK[f.severity])
    verdict, severity = _verdict(findings, bottleneck, stats)
    return Analysis(verdict, severity, findings, stats, [d["t"] for d in dips], bottleneck,
                    _limiter(findings, bottleneck), sorted(skip))


def _minutes(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m} min" if m >= 1 else f"{int(seconds)} s"


LIMITER_NOTES = {"CPU": "CPU couldn't keep up", "GPU": "GPU couldn't keep up",
                 "VRAM": "graphics memory full", "RAM": "memory nearly full",
                 "Internet": "unsteady connection", "GPU heat": "GPU too hot",
                 "CPU heat": "CPU too hot", "Heat": "overheating", "Low base FPS": "frame gen base too low",
                 "Loading / shaders": "loading stutters", "Stutters": "frequent frame drops"}


def _limiter(findings: list[Finding], bottleneck: str) -> tuple[str, str, str]:
    """The biggest thing holding performance back: the most serious finding that names one."""
    for f in findings:  # already sorted worst first
        if f.factor and RANK[f.severity] >= RANK[WARN]:
            return f.factor, LIMITER_NOTES.get(f.factor, "using your CPU"), f.severity
    if bottleneck == "cap":
        return "Nothing", "running at your FPS cap", GOOD
    if bottleneck == "gpu":
        return "Nothing", "GPU working flat out (normal)", GOOD
    return "Nothing", "no problems found", GOOD


def _bottleneck_findings(kind, b, cap, stats, meta) -> list[Finding]:
    avg = stats["avg_rfps"]
    gpu_busy = (f"busy about {b['gpu_ratio'] * 100:.0f}% of each frame" if b["gpu_ratio"]
                else f"at about {b['gpu_use']:.0f}% usage" if b["gpu_use"] is not None else "")
    smt_tip = (" Your CPU has SMT / Hyper-Threading turned off (for example by an X3D Gaming Mode "
               "BIOS option): a few games run better with it on, so it's worth a try for this one."
               if meta.get("smt_off") else "")
    if kind == "cap":
        return [Finding(GOOD, f"Running at an FPS cap (about {cap} FPS)",
                        f"Your frame rate sat flat at about {cap} with spare GPU power, so a limit "
                        "(in-game cap, V-Sync, Reflex or G-Sync) was holding it there. That's a "
                        "good thing: steady frames feel smoother than bouncing ones.",
                        "Nothing needed. If you want more FPS, raise or remove the cap in the game "
                        "or NVIDIA Control Panel.")]
    if kind == "gpu":
        if avg < 60:
            return [Finding(WARN, "Your GPU couldn't keep up at these settings",
                            f"The GPU was the limit ({gpu_busy}) and averaged only {avg:.0f} rendered FPS.",
                            "Turn on DLSS (Quality or Balanced) and lower the heaviest settings: ray "
                            "tracing, shadows, volumetric fog/clouds. Your CPU has room to spare.",
                            factor="GPU")]
        return [Finding(GOOD, "GPU-limited, as it should be",
                        f"Your graphics card was doing the most work ({gpu_busy}), which is what you "
                        "want in a game: nothing else was holding it back.",
                        "Nothing needed. For more FPS: DLSS Quality/Balanced, or lower ray tracing "
                        "and shadows.")]
    if kind == "cpu":
        return [Finding(WARN, "Held back by your CPU",
                        f"Your GPU was waiting on the CPU ({gpu_busy}) while the CPU was maxed out. "
                        f"You averaged {avg:.0f} rendered FPS; a stronger CPU, or easing its load, "
                        "would give more.",
                        "Raising resolution or graphics quality costs little FPS here, since the GPU "
                        "has room. Lower CPU-heavy settings (view distance, crowds/NPC density, "
                        "physics), close background apps, and cap FPS a little below your average "
                        "for steadier frames." + smt_tip, factor="CPU")]
    if kind == "engine":
        return [Finding(INFO, "Neither your CPU nor GPU was maxed out",
                        f"The GPU was only {gpu_busy} and no CPU core was pinned. Usually that means a "
                        "frame cap you didn't notice, the game engine's own limit, or one game thread "
                        "hopping between cores (which hides a CPU limit).",
                        "Check the game for an FPS limit or V-Sync. If FPS feels low, treat it like a "
                        "CPU limit: lower view distance and crowd settings." + smt_tip)]
    return [Finding(GOOD, "Well balanced", "Your CPU and GPU were both working without one clearly "
                                           "holding the other back.")]


def _frame_gen_findings(stats) -> list[Finding]:
    mult = stats.get("frame_gen")
    if not mult:
        return []
    base = stats["avg_rfps"]
    if base < 50:
        return [Finding(WARN, f"Frame generation ({mult}×) on top of a low base frame rate",
                        f"It looked like {stats['avg_fps']:.0f} FPS, but the game only rendered about "
                        f"{base:.0f}. Frame generation makes motion smoother but doesn't make the game "
                        "respond faster, so it can feel laggy.",
                        "Lower settings or use a faster DLSS mode until the rendered rate is 60+; "
                        "frame generation works best from there.", factor="Low base FPS")]
    return [Finding(INFO, f"Frame generation was on ({mult}×)",
                    f"The game rendered about {base:.0f} FPS and frame generation brought it to "
                    f"{stats['avg_fps']:.0f}. That base is high enough to feel responsive.")]


def _dip_findings(dips, stats) -> list[Finding]:
    if not dips:
        return [Finding(GOOD, "No big frame drops",
                        "Your frame rate never suddenly fell to less than two-thirds of normal.")]
    per_hour = len(dips) / max(stats["duration"], 60) * 3600
    causes: dict[str, int] = {}
    for d in dips:
        causes[d["cause"]] = causes.get(d["cause"], 0) + 1
    main, count = max(causes.items(), key=lambda kv: kv[1])
    if main.startswith("background:"):
        app = main.split(":", 1)[1]
        why, fix = (f"{app} was using a lot of CPU at the same moments",
                    f"Close {app} while gaming, or turn down what it's doing in the background.")
    else:
        why, fix = DIP_FIXES[main]
        if main in ("vram", "ram"):  # its own finding below already has the fix
            fix = "See the " + ("graphics memory (VRAM)" if main == "vram" else "system memory (RAM)") \
                  + " finding below."
    severity = WARN if len(dips) >= 5 or per_hour > 10 else INFO
    others = len(dips) - count
    factor = (main.split(":", 1)[1] if main.startswith("background:") else
              {"vram": "VRAM", "ram": "RAM", "thermal": "Heat", "loading": "Loading / shaders",
               "cpu": "CPU", "unknown": "Stutters"}[main])
    return [Finding(severity, f"{len(dips)} big frame drop{'s' if len(dips) != 1 else ''}",
                    f"Most likely cause ({count} of {len(dips)}): {why}."
                    + (f" {others} had other causes." if others else ""), fix, factor=factor)]


def _cpu_load_findings(samples) -> list[Finding]:
    """The whole CPU (every core) pinned near 100%: everything fights for CPU time."""
    maxed = _share(samples, lambda s: (s.get("cpu") or 0) >= 95)
    if maxed < 0.15:
        return []
    totals: dict[str, float] = {}
    for s in samples:
        if (s.get("cpu") or 0) >= 95:
            for app, pct in (s.get("bg") or {}).items():
                totals[app] = totals.get(app, 0) + pct
    n = sum(1 for s in samples if (s.get("cpu") or 0) >= 95)
    busiest = sorted(((app, t / n) for app, t in totals.items()), key=lambda x: -x[1])[:3]
    others = (" Besides the game, the busiest apps then were "
              + ", ".join(f"{app} ({pct:.0f}%)" for app, pct in busiest) + "." if busiest else "")
    return [Finding(WARN, f"Your whole CPU was maxed out for {_minutes(maxed * len(samples))}",
                    "Every core was near 100%, so the game, Windows and everything else were "
                    "fighting for CPU time. That causes stutters and input lag, and can even cause "
                    "network hiccups." + others,
                    "Close heavy background apps while you play (Reports → Apps shows what was busy "
                    "then). If it happens in the game on its own, lower CPU-heavy settings (view "
                    "distance, player/crowd density) or cap FPS a bit lower. Very high CPU at the "
                    "start of a session after an update is often shader compiling and passes.",
                    factor="CPU")]


def _thermal_findings(samples, stats, meta) -> list[Finding]:
    out = []
    hot_gpu = _share(samples, lambda s: (s.get("thr") or 0) & GPU_THERMAL)
    slow_gpu = _share(samples, lambda s: (s.get("thr") or 0) & GPU_HW_SLOWDOWN)
    cpu_throttle = _share(samples, lambda s: s.get("cthr"))
    gt, ct = stats["peak_gpu_temp"], stats["peak_cpu_temp"]
    limits = cpu_limits(meta.get("cpu_name", ""))
    cooling = ("Check that every case fan and your CPU cooler's fans (and pump, if it's liquid "
               "cooled) are spinning, that the cooler is plugged into the CPU_FAN header, and that "
               "dust isn't clogging the filters or heatsinks. A more aggressive fan curve (in the "
               "BIOS or your fan software) also helps.")
    if hot_gpu or slow_gpu:
        out.append(Finding(BAD, "Your GPU overheated and slowed itself down",
                           f"It throttled for about {(hot_gpu + slow_gpu) * stats['duration'] / 60:.0f} "
                           f"min, peaking at {gt:.0f} °C." if gt else "It throttled during the session.",
                           cooling + " For the GPU, make sure its fans aren't blocked.", factor="GPU heat"))
    elif gt and gt >= 83:
        out.append(Finding(WARN, f"GPU ran hot ({gt:.0f} °C peak)",
                           "Not throttling yet, but close to where it would start.", cooling,
                           factor="GPU heat"))
    if cpu_throttle:
        out.append(Finding(BAD, "Your CPU overheated and slowed itself down",
                           f"It hit its limit (peak {ct:.0f} °C)." if ct else "It hit its temperature limit.",
                           cooling, factor="CPU heat"))
    elif ct and ct >= limits["throttle_c"] - 7:
        out.append(Finding(WARN, f"CPU ran hot ({ct:.0f} °C peak)",
                           f"Your {limits['model']} slows itself down at {limits['throttle_c']} °C. "
                           "In games it should normally stay well below that.", cooling,
                           factor="CPU heat"))
    if not out and ct and gt:
        out.append(Finding(GOOD, "Temperatures were fine",
                           f"CPU peaked at {ct:.0f} °C and GPU at {gt:.0f} °C."))
    if _share(samples, lambda s: (s.get("thr") or 0) & GPU_POWER_CAP) >= 0.3:
        out.append(Finding(INFO, "Your GPU reached its power limit",
                           "Normal for a GPU working flat out: it's the card's built-in safety "
                           "limit, not a problem."))
    return out


def _memory_findings(samples, meta) -> list[Finding]:
    out = []
    vram = max(_values(samples, "vram"), default=0)
    ram = max(_values(samples, "ram"), default=0)
    if vram >= 95:
        out.append(Finding(WARN, "Graphics memory (VRAM) was full",
                           f"It reached {vram:.0f}% of your {meta.get('vram_total_gb', 16):.0f} GB. When "
                           "VRAM runs out the game has to shuffle textures around, which causes "
                           "stutters.", DIP_FIXES["vram"][1], factor="VRAM"))
    elif vram >= 85:
        out.append(Finding(INFO, f"VRAM got fairly full ({vram:.0f}%)",
                           "Still fine, but you're near the limit at these settings."))
    if ram >= 90:
        top = next((s.get("memtop") for s in reversed(samples) if s.get("memtop")), None)
        out.append(Finding(WARN, "System memory (RAM) was nearly full",
                           f"It reached {ram:.0f}%." + (f" The biggest user was {top}." if top else ""),
                           DIP_FIXES["ram"][1], factor="RAM"))
    return out


def _background_findings(samples, meta) -> list[Finding]:
    totals: dict[str, float] = {}
    for s in samples:
        for app, pct in (s.get("bg") or {}).items():
            totals[app] = totals.get(app, 0) + pct
    if not totals:
        return []
    app, total = max(totals.items(), key=lambda kv: kv[1])
    avg = total / len(samples)
    if avg >= 4:
        return [Finding(WARN, f"{app} used {avg:.0f}% of your CPU while you played",
                        "That's CPU time your game could have used.",
                        f"Close {app} while gaming, or turn down its background work (for example "
                        "recording quality or 'always on' features).", factor=app)]
    if avg >= 2:
        return [Finding(INFO, f"{app} was the busiest background app ({avg:.1f}% CPU)",
                        "Small, but worth knowing about if you're chasing every frame.")]
    return []


def _network_findings(samples) -> list[Finding]:
    measured = [s for s in samples if s.get("ping") is not None or s.get("lost")]
    if len(measured) < 30:
        return []
    pings = [s["ping"] for s in measured if s.get("ping") is not None]
    loss = _share(measured, lambda s: s.get("lost")) * 100
    jitter = (sum(abs(a - b) for a, b in zip(pings, pings[1:])) / (len(pings) - 1)) if len(pings) > 1 else 0
    spikes = sum(1 for p in pings if p >= 100)
    spike_share = spikes / len(pings) * 100 if pings else 0
    avg = sum(pings) / len(pings) if pings else 0
    if loss >= 2 or jitter >= 15 or spike_share >= 1:
        return [Finding(WARN, "Your internet connection was unsteady",
                        f"Average ping {avg:.0f} ms, jitter ±{jitter:.0f} ms, {loss:.1f}% of pings lost, "
                        f"{spikes} spikes over 100 ms. In online games this shows up as lag or "
                        "rubber-banding, not as low FPS.",
                        "On Wi-Fi, walls and distance cause most of this (the 6 GHz band is the fastest "
                        "but the most easily blocked). If it keeps happening, try your router's 5 GHz "
                        "network or plug in an Ethernet cable. If the PC was also maxed out at the time, "
                        "that can cause it too. See Reports → Network for when it happened.",
                        factor="Internet")]
    return [Finding(GOOD, "Your internet connection was steady",
                    f"Average ping {avg:.0f} ms with almost no jitter or loss"
                    + (f" (just {spikes} brief spike{'s' if spikes != 1 else ''} over 100 ms)."
                       if spikes else "."))]


def _verdict(findings, bottleneck, stats) -> tuple[str, str]:
    verdict, severity = _main_verdict(findings, bottleneck, stats)
    # A good verdict shouldn't hide warnings: say how many things are worth a look.
    warnings = sum(1 for f in findings if f.severity == WARN)
    if severity == GOOD and warnings:
        return f"{verdict}, with {warnings} thing{'s' if warnings > 1 else ''} to check", INFO
    return verdict, severity


def _main_verdict(findings, bottleneck, stats) -> tuple[str, str]:
    worst = findings[0]
    if worst.severity == BAD:
        return worst.title, BAD
    dips = stats.get("dips", 0)
    if dips >= 5:
        return f"Stuttery: {dips} big frame drops", WARN
    if bottleneck == "cpu":
        return "Held back by your CPU", WARN
    if bottleneck == "gpu":
        return (("Smooth: your GPU was the limit", GOOD) if stats.get("avg_rfps", 0) >= 60
                else ("Your GPU couldn't keep up at these settings", WARN))
    if bottleneck == "cap":
        return "Smooth: running at your FPS cap", GOOD
    if worst.severity == WARN:
        return worst.title, WARN
    return "Ran well", GOOD
