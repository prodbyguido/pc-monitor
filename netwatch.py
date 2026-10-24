"""Network watcher: pings your router and the internet, records drops, and runs speed tests
only when you press the button in Reports.

Pinging two places shows *where* a problem is: if your router answers but the internet
doesn't, it's your internet provider; if the router doesn't answer either, it's your Wi-Fi
or router. Each ping is a few dozen bytes, so this uses a few KB per minute.
"""
import ctypes
import http.client
import re
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes

import psutil

from db import connect

PING_EVERY = 2.0            # seconds between rounds
TIMEOUT_MS = 1000
INTERNET = "1.1.1.1"        # Cloudflare
BACKUP = "8.8.8.8"          # Google, tried when Cloudflare doesn't answer
DOWN_AFTER = 3              # failed rounds in a row before it counts as a drop (~6 s)

iphlpapi = ctypes.windll.iphlpapi
iphlpapi.IcmpCreateFile.restype = wintypes.HANDLE
iphlpapi.IcmpSendEcho.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, wintypes.WORD,
                                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
iphlpapi.IcmpSendEcho.restype = wintypes.DWORD
iphlpapi.IcmpCloseHandle.argtypes = [wintypes.HANDLE]


class MIB_IPFORWARDROW(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "dest", "mask", "policy", "next_hop", "if_index", "type", "proto", "age",
        "next_hop_as", "metric1", "metric2", "metric3", "metric4", "metric5")]


iphlpapi.GetBestRoute.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(MIB_IPFORWARDROW)]


def ip_to_ulong(ip: str) -> int:
    return int.from_bytes(socket.inet_aton(ip), "little")


def default_gateway() -> str | None:
    """Your router's address, from Windows' routing table."""
    row = MIB_IPFORWARDROW()
    if iphlpapi.GetBestRoute(ip_to_ulong(INTERNET), 0, ctypes.byref(row)) != 0 or not row.next_hop:
        return None
    return socket.inet_ntoa(row.next_hop.to_bytes(4, "little"))


def wifi_status() -> dict | None:
    """Your Wi-Fi connection as Windows reports it, or None when not on Wi-Fi.

    Read locally from Windows (no network traffic). The network's name is never read out.
    """
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True,
                             text=True, timeout=5, creationflags=0x08000000).stdout  # no window
    except (OSError, subprocess.SubprocessError):
        return None

    def field(name: str) -> str | None:
        m = re.search(rf"^\s*{name}\s*:\s*(.+?)\s*$", out, re.MULTILINE)
        return m.group(1) if m else None

    def number(name: str) -> float | None:
        value = field(name)
        m = re.match(r"[\d.]+", value or "")
        return float(m.group(0)) if m else None

    if (field("State") or "").lower() != "connected":
        return None
    return {"signal": number("Signal"), "rx_mbps": number(r"Receive rate \(Mbps\)"),
            "tx_mbps": number(r"Transmit rate \(Mbps\)"), "band": field("Band"),
            "radio": field("Radio type")}


def wifi_link_mbps() -> float | None:
    """The Wi-Fi link speed, read the ordinary way (this doesn't trigger Windows' location icon)."""
    for name, stats in psutil.net_if_stats().items():
        lowered = name.lower()
        if stats.isup and stats.speed and ("wi-fi" in lowered or "wlan" in lowered or "wireless" in lowered):
            return float(stats.speed)
    return None


class WifiPoller(threading.Thread):
    """Keeps the latest Wi-Fi status for the dashboard's Wi-Fi tile.

    The link speed is read every few seconds. The signal % can only be read with Windows'
    "netsh" tool, which makes Windows show its location icon ("Network Command Shell is using
    your location") because Wi-Fi details could reveal where you are. PC Monitor never reads your
    location, but to keep the icon away the signal is only checked once a minute, and only while
    the network watcher is on (allow_signal).
    """

    SIGNAL_EVERY = 60.0

    def __init__(self, every: float = 5.0):
        super().__init__(daemon=True, name="wifi-poller")
        self.every = every
        self.status = None
        self.checked = False
        self.allow_signal = False
        self.signal = None
        self.signal_checked = float("-inf")
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            link = wifi_link_mbps()
            now = time.monotonic()
            if not self.allow_signal:
                self.signal = None
            elif link and now - self.signal_checked >= self.SIGNAL_EVERY:
                self.signal_checked = now
                full = wifi_status()
                self.signal = full["signal"] if full else None
            self.status = ({"signal": self.signal, "rx_mbps": link, "tx_mbps": link}
                           if link else None)
            self.checked = True
            self.stop_event.wait(self.every)


# --- Wi-Fi strength score ---------------------------------------------------------
# Full marks for each part, chosen for a busy household: 2-3 people gaming online plus
# 4K streaming and more need roughly 100-200 Mbit/s and a steady, low ping.
CAPACITY_FULL_MBPS = 400     # real-world capacity that comfortably covers all of that
WIFI_EFFICIENCY = 0.6        # Wi-Fi delivers roughly 60% of its link rate in practice
WIFI_WEIGHTS = {"signal": 0.25, "capacity": 0.25, "ping": 0.20, "jitter": 0.15, "loss": 0.15}


def _scale(value: float, worst: float, best: float) -> float:
    """0 at worst, 1 at best (works whichever way round they are), clamped."""
    return max(0.0, min(1.0, (value - worst) / (best - worst)))


def ping_stats(recent: list) -> dict | None:
    """Average ping, jitter and packet loss from recent internet pings (None = no answer)."""
    if not recent:
        return None
    ok = [ms for ms in recent if ms is not None]
    jitter = (sum(abs(a - b) for a, b in zip(ok, ok[1:])) / (len(ok) - 1)) if len(ok) > 1 else 0.0
    return {"ping": sum(ok) / len(ok) if ok else None, "jitter": jitter,
            "loss": (len(recent) - len(ok)) / len(recent) * 100}


def wifi_strength(status: dict | None, pings: dict | None, internet_up: bool | None,
                  plan_mbps: int = 0) -> dict:
    """The 0-100% Wi-Fi strength score and the parts it's made of.

    internet_up is None when the network watcher is off (then only signal and link speed count).
    plan_mbps is your internet plan's speed (Settings → About your PC); Wi-Fi faster than the plan
    brings no extra benefit. 0 = not set.
    """
    if internet_up is False or (pings and pings["ping"] is None):
        return {"score": 0.0, "reason": "No internet connection"}
    if status is None:
        return {"score": None, "reason": "Not connected to Wi-Fi"}
    parts = {}
    if status["signal"] is not None:
        parts["signal"] = _scale(status["signal"], 20, 80)
    rates = [r for r in (status["rx_mbps"], status["tx_mbps"]) if r]
    if rates:
        capacity = min(rates) * WIFI_EFFICIENCY
        if plan_mbps:
            capacity = min(capacity, plan_mbps)
        parts["capacity"] = _scale(capacity, 0, CAPACITY_FULL_MBPS)
    if pings:
        parts["ping"] = _scale(pings["ping"], 150, 20)
        parts["jitter"] = _scale(pings["jitter"], 50, 5)
        parts["loss"] = _scale(pings["loss"], 5, 0)
    if not parts:
        return {"score": None, "reason": "No Wi-Fi details from Windows"}
    weight = sum(WIFI_WEIGHTS[k] for k in parts)
    score = sum(WIFI_WEIGHTS[k] * v for k, v in parts.items()) / weight * 100
    # One really bad part (like heavy packet loss) caps the whole score.
    score = min(score, 40 + 120 * min(parts.values()))
    return {"score": score, "parts": parts, "reason": None}


class Pinger:
    def __init__(self):
        self.handle = iphlpapi.IcmpCreateFile()
        self.reply = ctypes.create_string_buffer(256)
        self.data = bytes(10)  # blank filler: the ping carries nothing that identifies you or the app

    def ping(self, ip: str) -> float | None:
        """Round-trip time in ms, or None if there was no answer."""
        n = iphlpapi.IcmpSendEcho(self.handle, ip_to_ulong(ip), self.data, len(self.data), None,
                                  self.reply, len(self.reply), TIMEOUT_MS)
        if not n:
            return None
        status, rtt = struct.unpack_from("<II", self.reply, 4)
        return float(rtt) if status == 0 else None

    def close(self):
        iphlpapi.IcmpCloseHandle(self.handle)


class NetWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="net-watcher")
        self.stop_event = threading.Event()
        self.latest = {"router": None, "internet": None}  # ms, for the live Network card
        self.internet_up = True
        self.gateway = None
        self.signal = None
        # The minute still being collected (not saved yet), for live Reports:
        # (minute, {target: [sent, lost, ms_sum, max_ms]}) or None
        self.live = None
        self.recent = deque(maxlen=30)  # last minute of internet pings, for the Wi-Fi tile

    def stop(self):
        self.stop_event.set()

    def run(self):
        con = connect()
        con.execute("UPDATE net_event SET end = start WHERE end IS NULL")  # left open by a crash
        con.commit()
        pinger = Pinger()
        minute = None
        stats: dict[str, list[float]] = {}   # target -> [sent, lost, ms_sum, max_ms]
        fails = {"router": 0, "internet": 0}
        open_event = {"router": None, "internet": None}
        router_answers = False  # some routers never answer pings; then we don't judge them
        last_gateway = 0.0
        while not self.stop_event.is_set():
            t0 = time.time()
            if t0 - last_gateway > 60 or not self.gateway:
                self.gateway, last_gateway = default_gateway(), t0
            # self.signal is filled in from the Wi-Fi tile's reading (WifiPoller), so Windows'
            # location-flagged Wi-Fi check runs only once a minute in total.
            router = pinger.ping(self.gateway) if self.gateway else None
            internet = pinger.ping(INTERNET)
            if internet is None:
                internet = pinger.ping(BACKUP)
            router_answers = router_answers or router is not None
            results = {"router": router, "internet": internet}
            self.latest = results
            self.recent.append(internet)

            m = int(t0 // 60 * 60)
            if minute is not None and m != minute:
                self.live = None  # cleared first so Reports never counts a minute twice
                self.flush(con, minute, stats)
                stats = {}
            minute = m
            for target, ms in results.items():
                if target == "router" and not router_answers:
                    continue
                s = stats.setdefault(target, [0, 0, 0.0, 0.0])
                s[0] += 1
                if ms is None:
                    s[1] += 1
                else:
                    s[2] += ms
                    s[3] = max(s[3], ms)
            self.live = (minute, {target: list(s) for target, s in stats.items()})

            for target, ms in results.items():
                if target == "router" and not router_answers:
                    continue
                fails[target] = fails[target] + 1 if ms is None else 0
                down = fails[target] >= DOWN_AFTER
                if down and open_event[target] is None:
                    start = int(t0 - (DOWN_AFTER - 1) * PING_EVERY)
                    cur = con.execute("INSERT INTO net_event VALUES (?, NULL, ?)", (start, target))
                    open_event[target] = cur.lastrowid
                    con.commit()
                elif ms is not None and open_event[target] is not None:
                    con.execute("UPDATE net_event SET end = ? WHERE rowid = ?",
                                (int(t0), open_event[target]))
                    open_event[target] = None
                    con.commit()
            self.internet_up = fails["internet"] < DOWN_AFTER
            self.stop_event.wait(max(0.0, PING_EVERY - (time.time() - t0)))

        self.live = None
        if minute is not None:
            self.flush(con, minute, stats)
        for rowid in open_event.values():
            if rowid is not None:
                con.execute("UPDATE net_event SET end = ? WHERE rowid = ?", (int(time.time()), rowid))
        con.commit()
        pinger.close()
        con.close()

    def flush(self, con, minute: int, stats: dict[str, list[float]]):
        for target, (sent, lost, ms_sum, max_ms) in stats.items():
            ok = sent - lost
            con.execute("INSERT OR REPLACE INTO net_minute VALUES (?, ?, ?, ?, ?, ?)",
                        (minute, target, sent, lost, ms_sum / ok if ok else None,
                         max_ms if ok else None))
        if self.signal is not None:
            con.execute("INSERT OR REPLACE INTO wifi_minute VALUES (?, ?)", (minute, self.signal))
        con.commit()


# --- Speed test (only when you press the button) --------------------------------
SPEED_HOST = "speed.cloudflare.com"
DOWN_BYTES = 25_000_000     # per stream
UP_BYTES = 10_000_000       # per stream
STREAMS = 4                 # parallel connections, so fast connections get measured properly
HEADERS = {"User-Agent": "PC-Monitor/1.0"}
DATA_USED_MB = (DOWN_BYTES + UP_BYTES) * STREAMS // 1_000_000


def _connection():
    return http.client.HTTPSConnection(SPEED_HOST, timeout=30)


def _download(results: list, i: int):
    conn = _connection()
    conn.request("GET", f"/__down?bytes={DOWN_BYTES}", headers=HEADERS)
    resp = conn.getresponse()
    total = 0
    while chunk := resp.read(256 * 1024):
        total += len(chunk)
    results[i] = total
    conn.close()


def _upload(results: list, i: int):
    conn = _connection()
    body = b"0" * UP_BYTES
    conn.request("POST", "/__up", body=body,
                 headers={**HEADERS, "Content-Type": "application/octet-stream"})
    conn.getresponse().read()
    results[i] = UP_BYTES
    conn.close()


def _parallel(worker) -> float:
    """Run STREAMS copies of worker at once; returns megabits per second."""
    results = [0] * STREAMS
    threads = [threading.Thread(target=worker, args=(results, i), daemon=True) for i in range(STREAMS)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seconds = time.perf_counter() - start
    return sum(results) * 8 / seconds / 1e6


def speed_test(progress) -> dict:
    """Measures ping, download and upload against Cloudflare. progress(text) reports each step."""
    progress("Measuring ping…")
    conn = _connection()
    conn.request("GET", "/__down?bytes=0", headers=HEADERS)
    conn.getresponse().read()  # first request sets up the connection; not timed
    times = []
    for _ in range(5):
        t = time.perf_counter()
        conn.request("GET", "/__down?bytes=0", headers=HEADERS)
        conn.getresponse().read()
        times.append((time.perf_counter() - t) * 1000)
    conn.close()
    ping = min(times)  # the fastest round trip is closest to the real network delay
    progress("Measuring download…")
    down = _parallel(_download)
    progress("Measuring upload…")
    up = _parallel(_upload)
    result = {"ts": int(time.time()), "down": down, "up": up, "ping": ping}
    con = connect()
    con.execute("INSERT INTO speed_test VALUES (?, ?, ?, ?)", (result["ts"], down, up, ping))
    con.commit()
    con.close()
    return result
