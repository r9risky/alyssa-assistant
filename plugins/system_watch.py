"""On-demand system diagnostics plus one optional low-disk-space alert."""
import os
import shutil
import time

import config

try:
    import psutil
except ImportError:
    psutil = None

DISK_THRESHOLD = 90
WATCH_INTERVAL_SECONDS = config.SYSTEM_WATCH_INTERVAL_SECONDS

_PSUTIL_MISSING_MSG = (
    "I can't check system stats - psutil isn't installed. "
    "Run: pip install psutil"
)


def _bytes_to_gb(n: int) -> float:
    return n / (1024 ** 3)


def get_system_status() -> str:
    """Reports current CPU, RAM, disk, battery, and network status."""
    if psutil is None:
        return _PSUTIL_MISSING_MSG

    parts = []

    cpu = psutil.cpu_percent(interval=0.5)
    parts.append(f"CPU is at {cpu:.0f}%")

    mem = psutil.virtual_memory()
    parts.append(f"RAM is at {mem.percent:.0f}% ({_bytes_to_gb(mem.used):.1f} of {_bytes_to_gb(mem.total):.1f} GB)")

    disk_bits = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        device_label = part.device.rstrip("\\")
        disk_bits.append(f"{device_label} {usage.percent:.0f}% full ({_bytes_to_gb(usage.free):.0f} GB free)")
    if disk_bits:
        parts.append("Disks: " + ", ".join(disk_bits))

    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery is not None:
        state = "plugged in" if battery.power_plugged else "on battery"
        parts.append(f"Battery is at {battery.percent:.0f}% ({state})")

    top = _top_cpu_process()
    if top:
        parts.append(f"Top process: {top}")

    return ". ".join(parts) + "."


def _top_cpu_process():
    """Returns 'name (X%)' for the single busiest process, or None. A
    second psutil.cpu_percent pass is needed for per-process numbers to be
    meaningful (the first call after psutil import is always 0.0)."""
    if psutil is None:
        return None
    try:
        procs = list(psutil.process_iter(["name", "cpu_percent"]))
        for p in procs:
            try:
                p.cpu_percent(None)  # prime it
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.3)
        best = None
        for p in procs:
            try:
                cpu = p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if best is None:
                best = (p.info.get("name", "?"), cpu)
            elif cpu > best[1]:
                best = (p.info.get("name", "?"), cpu)
        if best and best[1] > 1:
            return f"{best[0]} ({best[1]:.0f}%)"
    except Exception:
        pass
    return None


_disk_was_low = False


def check_watch():
    """Return one informational alert when the system drive crosses 90% used."""
    global _disk_was_low

    if not config.SYSTEM_WATCH_ENABLED:
        _disk_was_low = False
        return None

    usage = shutil.disk_usage(os.path.abspath(os.sep))
    percent_used = usage.used / usage.total * 100 if usage.total else 0
    disk_is_low = percent_used >= DISK_THRESHOLD
    if not disk_is_low:
        _disk_was_low = False
        return None
    if _disk_was_low:
        return None

    _disk_was_low = True
    return (
        f"Your system drive is almost full - {percent_used:.0f}% used, "
        f"with only {_bytes_to_gb(usage.free):.1f} GB free."
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": (
                "Reports current CPU usage, RAM usage, disk space, "
                "battery level, and the busiest running process - e.g. "
                "'how's my system doing', 'check my CPU', 'how much disk "
                "space do I have left'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "get_system_status": get_system_status,
}
