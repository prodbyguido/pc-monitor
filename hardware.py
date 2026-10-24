"""Limits of the CPU in this PC, so colours and warnings fit any processor.

throttle_c  the temperature at which the CPU slows itself down to protect itself
power_w     its normal full-load power limit (AMD "PPT", Intel "PL2")
"""
import re

# AMD Ryzen package power limits (PPT, watts) for common models; others fall back by suffix.
RYZEN_PPT = {
    "3600": 88, "3600X": 128, "3700X": 88, "3800X": 142, "3900X": 142, "3950X": 142,
    "5600": 88, "5600X": 76, "5700X": 88, "5800X": 142, "5900X": 142, "5950X": 142,
    "5700X3D": 142, "5800X3D": 142,
    "7600": 88, "7600X": 142, "7700": 88, "7700X": 142, "7900X": 230, "7950X": 230,
    "7800X3D": 162, "7900X3D": 162, "7950X3D": 162,
    "9600X": 88, "9700X": 88, "9900X": 162, "9950X": 230,
    "9800X3D": 162, "9900X3D": 162, "9950X3D": 230,
}
# X3D chips with a lower temperature limit than the usual 95 °C
X3D_THROTTLE = {"5": 90, "7": 89}


def cpu_limits(cpu_name: str) -> dict:
    name = cpu_name.upper()
    if "INTEL" in name:
        unlocked = re.search(r"\d{4,5}K", name)
        return {"throttle_c": 100, "power_w": 253 if unlocked else 150, "model": cpu_name}
    m = re.search(r"RYZEN\s+\d+\s+(?:PRO\s+)?(\d{4})(X3D|XT|X|GT|G|F)?", name)
    if not m:
        return {"throttle_c": 95, "power_w": 142, "model": cpu_name}
    number, suffix = m.group(1), m.group(2) or ""
    model = number + suffix
    ppt = RYZEN_PPT.get(model) or RYZEN_PPT.get(number + ("X" if suffix == "XT" else "")) \
        or (142 if suffix in ("X", "XT", "X3D") else 88)
    throttle = X3D_THROTTLE.get(number[0], 95) if suffix == "X3D" else 95
    return {"throttle_c": throttle, "power_w": ppt, "model": f"Ryzen {model}"}
