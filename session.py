"""Game sessions: notice when you're playing, record how the PC handled it, and save it.

A session starts when the frame counter sees an actual game and ends after 30 seconds of no
game. Sessions of at least a minute are saved to history.db with one sample per second, so
the recap (see recap.py and analysis.py) can be reopened later from Reports → Games.
"""
import json
import time
import zlib
from dataclasses import dataclass, field

from db import connect, day_bounds

END_AFTER = 30
MIN_LENGTH = 60
GPU_BUSY_PCT = 60
UNKNOWN = "Game session"


@dataclass
class GameSession:
    name: str
    start: float          # time.time()
    last_active: float
    samples: list = field(default_factory=list)   # one dict per second (see analysis.py)
    meta: dict = field(default_factory=dict)      # facts about the PC, e.g. VRAM size
    id: int | None = None                         # row in history.db once saved

    @property
    def duration(self) -> float:
        return self.last_active - self.start


class SessionTracker:
    def __init__(self):
        self.current: GameSession | None = None
        self.last: GameSession | None = None

    @staticmethod
    def is_active(fps: float | None, gpu_util: float | None) -> bool:
        return bool(fps and fps > 0) or (gpu_util or 0) >= GPU_BUSY_PCT

    def update(self, active: bool, sample: dict, app_name: str | None) -> GameSession | None:
        """Feed one second; returns a finished session when one just ended."""
        now = time.time()
        if active:
            if self.current is None:
                self.current = GameSession(app_name or UNKNOWN, now, now)
            s = self.current
            s.last_active = now
            if s.name == UNKNOWN and app_name:
                s.name = app_name
            s.samples.append(sample)
            return None
        if self.current and now - self.current.last_active >= END_AFTER:
            finished, self.current = self.current, None
            if finished.duration >= MIN_LENGTH:
                self.last = finished
                return finished
        return None

    def reset(self):
        self.current = None


# --- saving and loading -------------------------------------------------------------
def save(session: GameSession) -> int:
    data = zlib.compress(json.dumps({"samples": session.samples, "meta": session.meta},
                                    separators=(",", ":")).encode())
    con = connect()
    cur = con.execute("INSERT INTO game_session (name, start, end, data) VALUES (?, ?, ?, ?)",
                      (session.name, int(session.start), int(session.last_active), data))
    con.commit()
    con.close()
    session.id = cur.lastrowid
    return session.id


def load(con, session_id: int) -> GameSession | None:
    row = con.execute("SELECT id, name, start, end, data FROM game_session WHERE id = ?",
                      (session_id,)).fetchone()
    if not row:
        return None
    payload = json.loads(zlib.decompress(row[4]))
    return GameSession(row[1], row[2], row[3], payload["samples"], payload["meta"], row[0])


def latest_id(con) -> int | None:
    row = con.execute("SELECT id FROM game_session ORDER BY start DESC LIMIT 1").fetchone()
    return row[0] if row else None


def sessions_on(con, day: str) -> list[tuple]:
    """(id, name, start, end) for each game session that started on this day, newest first."""
    start, end = day_bounds(day)
    return con.execute("SELECT id, name, start, end FROM game_session WHERE start >= ? AND start < ? "
                       "ORDER BY start DESC", (start, end)).fetchall()


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
