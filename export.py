"""Export a game session: a text report to paste into a chat, or every second as a CSV file."""
import csv
import time
from statistics import median

from analysis import Analysis
from config import EXPORT_DIR
from session import GameSession, fmt_duration
CSV_COLUMNS = [("t", "time"), ("fps", "fps_displayed"), ("rfps", "fps_rendered"),
               ("ft", "frame_time_ms"), ("gb", "gpu_busy_ms"), ("cb", "cpu_busy_ms"),
               ("gpu", "gpu_usage_pct"), ("gt", "gpu_temp_c"), ("gp", "gpu_power_w"),
               ("thr", "gpu_slowdown_bits"), ("vram", "vram_used_pct"), ("cpu", "cpu_usage_pct"),
               ("core", "busiest_core_pct"), ("ct", "cpu_temp_c"), ("cp", "cpu_power_w"),
               ("cthr", "cpu_throttling"), ("ram", "ram_used_pct"), ("disk", "disk_mb_s"),
               ("ping", "ping_ms"), ("lost", "ping_lost"), ("away", "tabbed_out"),
               ("bg", "busy_background_apps")]
SEVERITY_WORDS = {"good": "GOOD", "info": "NOTE", "warn": "WARNING", "bad": "PROBLEM"}


def _num(v, digits=0):
    return "" if v is None else f"{v:.{digits}f}"


def _stamp(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def report_text(session: GameSession, a: Analysis, pc_notes: str = "", plan_mbps: int = 0) -> str:
    """A complete, readable report about the session, made to paste into a chat.

    pc_notes and plan_mbps come from Settings → About your PC."""
    m, st, s = session.meta, a.stats, session.samples
    extra = [f"- Other specs (from the owner): {pc_notes.strip()}"] if pc_notes.strip() else []
    if plan_mbps:
        extra.append(f"- Internet plan: {plan_mbps} Mbps")
    lines = [
        f"PC MONITOR GAME REPORT: {session.name}",
        f"When: {time.strftime('%a %d %b %Y, %H:%M', time.localtime(session.start))} – "
        f"{time.strftime('%H:%M', time.localtime(session.last_active))} "
        f"({fmt_duration(session.duration)}; {fmt_duration(st.get('away_seconds', 0))} tabbed out / "
        "menus / loading, not counted as gameplay)",
        "",
        "PC:",
        f"- CPU: {m.get('cpu_name', 'unknown')}, {m.get('cores', '?')} cores / "
        f"{m.get('threads', '?')} threads" + (" (SMT / Hyper-Threading off)" if m.get("smt_off") else ""),
        f"- GPU: {m.get('gpu_name', 'unknown')}, {m.get('vram_total_gb', 0):.0f} GB VRAM, "
        f"power limit {m.get('power_limit_w', 0):.0f} W",
        f"- RAM: {m.get('ram_total_gb', 0):.1f} GB usable",
        *extra,
        "",
        f"VERDICT: {a.verdict}",
        f"LIMITED BY: {a.limiter[0]} ({a.limiter[1]})",
        "",
        "KEY NUMBERS (gameplay only):",
    ]
    if st.get("avg_fps"):
        fg = st.get("frame_gen")
        lines.append(f"- FPS: average {st['avg_fps']:.0f} displayed"
                     + (f", {st['avg_rfps']:.0f} rendered ({fg}× frame generation)" if fg else "")
                     + f"; 1% low (worst 1% of seconds) {st['low1']:.0f}; big drops: {st.get('dips', 0)}")
    game = [x for x in s if x.get("fps") and x["t"] not in set(a.away)]
    def med(key):
        vals = [x[key] for x in game if x.get(key) is not None]
        return median(vals) if vals else None
    lines += [
        f"- Typical GPU usage {_num(med('gpu'))}%, busiest CPU core {_num(med('core'))}%, "
        f"overall CPU {_num(med('cpu'))}%",
        f"- Peak temps: CPU {_num(st.get('peak_cpu_temp'))} °C, GPU {_num(st.get('peak_gpu_temp'))} °C; "
        f"peak power: CPU {_num(st.get('peak_cpu_power'))} W, GPU {_num(st.get('peak_gpu_power'))} W",
        f"- Peak VRAM {_num(max((x['vram'] for x in s if x.get('vram') is not None), default=None))}%, "
        f"peak RAM {_num(max((x['ram'] for x in s if x.get('ram') is not None), default=None))}%",
    ]
    if med("ft") and med("gb"):
        lines.append(f"- Typical frame time {med('ft'):.2f} ms, GPU busy {med('gb'):.2f} ms"
                     + (f", CPU busy {med('cb'):.2f} ms" if med("cb") else ""))
    pings = [x["ping"] for x in s if x.get("ping") is not None]
    if pings:
        lost = sum(1 for x in s if x.get("lost"))
        lines.append(f"- Internet: average ping {sum(pings) / len(pings):.0f} ms, worst {max(pings):.0f} ms, "
                     f"{lost} of {len(pings) + lost} pings lost")

    lines += ["", "FINDINGS:"]
    for f in a.findings:
        lines.append(f"- [{SEVERITY_WORDS[f.severity]}] {f.title}. {f.detail}"
                     + (f" Suggested fix: {f.fix}" if f.fix else ""))

    if a.dips:
        lines += ["", "BIG FRAME DROPS (what else was happening that second):"]
        by_t = {x["t"]: x for x in s}
        for t in a.dips:
            x = by_t.get(t, {})
            busiest = sorted((x.get("bg") or {}).items(), key=lambda kv: kv[1], reverse=True)[:3]
            bg = ", ".join(f"{k} {v}%" for k, v in busiest)
            lines.append(f"- {_stamp(t)}: {_num(x.get('rfps') or x.get('fps'))} FPS | GPU {_num(x.get('gpu'))}% "
                         f"| busiest core {_num(x.get('core'))}% | VRAM {_num(x.get('vram'))}% | "
                         f"RAM {_num(x.get('ram'))}% | disk {_num(x.get('disk'))} MB/s"
                         + (f" | busy apps: {bg}" if bg else ""))

    lines += ["", "MINUTE BY MINUTE (fps avg/min, rendered avg, GPU %, busiest core %, CPU %, "
                  "CPU °C max, GPU °C max, GPU W, VRAM % max, RAM % max, disk MB/s max, ping ms avg, "
                  "pings lost, seconds not gameplay):"]
    away = set(a.away)
    by_minute: dict[int, list] = {}
    for x in s:
        by_minute.setdefault(int(x["t"] // 60), []).append(x)
    for minute, xs in sorted(by_minute.items()):
        play = [x for x in xs if x.get("fps") and x["t"] not in away]

        def avg(key, rows=play):
            vals = [x[key] for x in rows if x.get(key) is not None]
            return sum(vals) / len(vals) if vals else None

        def top(key, rows=xs):
            vals = [x[key] for x in rows if x.get(key) is not None]
            return max(vals) if vals else None
        lows = [x.get("rfps") or x["fps"] for x in play]
        lines.append(", ".join([
            time.strftime("%H:%M", time.localtime(minute * 60)), _num(avg("fps")),
            _num(min(lows) if lows else None), _num(avg("rfps")), _num(avg("gpu")), _num(avg("core")),
            _num(avg("cpu")), _num(top("ct")), _num(top("gt")), _num(avg("gp", xs)), _num(top("vram")),
            _num(top("ram")), _num(top("disk")), _num(avg("ping", xs)),
            str(sum(1 for x in xs if x.get("lost"))), str(sum(1 for x in xs if x["t"] in away))]))
    lines += ["", "(Readings are once per second, so very short single-frame hitches can be averaged away.)"]
    return "\n".join(lines)


def save_csv(session: GameSession) -> str:
    """Every second of the session as a spreadsheet file. Returns the file path."""
    EXPORT_DIR.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in session.name)
    path = EXPORT_DIR / f"{safe}_{time.strftime('%Y-%m-%d_%H-%M', time.localtime(session.start))}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # -sig: Excel reads it correctly
        w = csv.writer(f)
        w.writerow([name for _, name in CSV_COLUMNS])
        for x in session.samples:
            row = []
            for key, _ in CSV_COLUMNS:
                v = x.get(key)
                if key == "t":
                    v = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v))
                elif key == "bg":
                    v = "; ".join(f"{k} {p}%" for k, p in (v or {}).items())
                row.append("" if v is None else v)
            w.writerow(row)
    return str(path)
