"""Optional CSV logging (off unless you turn it on in Settings). One file per day in logs/."""
import csv
import time

from config import LOG_DIR

COLUMNS = ["time", "cpu_usage_pct", "cpu_temp_c", "cpu_power_w", "gpu_usage_pct", "gpu_temp_c",
           "gpu_power_w", "vram_used_gb", "ram_used_gb", "disk_read_mb_s", "disk_write_mb_s",
           "net_down_mbit_s", "net_up_mbit_s", "fps_displayed", "fps_rendered", "game"]


class DataLogger:
    def __init__(self):
        self.file = None
        self.writer = None
        self.day = None
        self.rows = 0

    def write(self, row: dict):
        day = time.strftime("%Y-%m-%d")
        if day != self.day:
            self.close()
            LOG_DIR.mkdir(exist_ok=True)
            path = LOG_DIR / f"{day}.csv"
            new_file = not path.exists()
            self.file = open(path, "a", newline="", encoding="utf-8")
            self.writer = csv.DictWriter(self.file, COLUMNS)
            if new_file:
                self.writer.writeheader()
            self.day = day
        row = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
        row["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.writer.writerow(row)
        self.rows += 1
        if self.rows % 10 == 0:
            self.file.flush()

    def close(self):
        if self.file:
            self.file.close()
        self.file = self.writer = self.day = None
