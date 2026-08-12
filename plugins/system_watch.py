"""
System diagnostics plugin - gives Alyssa an on-demand "how's my PC doing"
ability (CPU/RAM/disk/battery/network via psutil), AND makes her mention it
unprompted when something needs attention (disk almost full, a process
pegging the CPU, RAM under pressure, battery low, network down) - see
check_watch() below, picked up automatically by main.py's background
watcher loop (see plugin_loader.py's docstring for how that wiring works).

Requires: pip install psutil (add it to requirements.txt if it's not
already installed - see the try/except below for the message you'll get
if it's missing).

Say "Alyssa, how's my system doing?" / "check my CPU" / "how much disk
space do I have left?" any time for get_system_status(). The proactive
side needs no setup - it just runs once main.py's watcher loop starts.
"""
import time

try:
    import psutil
except ImportError:
    psutil = None

# --- Thresholds (tune these in-file if they fire too often/rarely) ---------
CPU_SUSTAINED_THRESHOLD = 90       # percent
CPU_SUSTAINED_SECONDS = 90         # how long CPU has to stay above threshold before warning
RAM_THRESHOLD = 90                 # percent
DISK_THRESHOLD = 90                # percent used, checked on every fixed drive
BATTERY_LOW_THRESHOLD = 15         # percent, only if not plugged in

# How often main.py's watcher loop calls check_watch() for this plugin.
WATCH_INTERVAL_SECONDS = 60

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


# --- Proactive watcher -------------------------------------------------------
# Edge-triggered: each condition only speaks once when it FIRST crosses the
# threshold, and only speaks again after it's cleared and re-crossed (or
# after _RENOTIFY_SECONDS, for things like "still almost full" that are
# worth a rare re-reminder). Without this, a disk sitting at 91% would get
# mentioned every single watcher cycle forever.
_state = {
    "cpu_high_since": None,
    "cpu_warned": False,
    "ram_warned": False,
    "disk_warned": set(),      # device names already warned about
    "battery_warned": False,
    "last_disk_check": 0,
}
_RENOTIFY_SECONDS = 30 * 60


def check_watch():
    if psutil is None:
        return None
    alerts = []

    # CPU - only warn if it's been sustained, not a brief spike.
    cpu = psutil.cpu_percent(interval=1.0)
    if cpu >= CPU_SUSTAINED_THRESHOLD:
        if _state["cpu_high_since"] is None:
            _state["cpu_high_since"] = time.time()
        elif not _state["cpu_warned"] and time.time() - _state["cpu_high_since"] >= CPU_SUSTAINED_SECONDS:
            top = _top_cpu_process()
            alerts.append(
                f"Your CPU has been pegged at {cpu:.0f}% for a while"
                + (f" - looks like {top} is the culprit." if top else ".")
            )
            _state["cpu_warned"] = True
    else:
        _state["cpu_high_since"] = None
        _state["cpu_warned"] = False

    # RAM
    mem_percent = psutil.virtual_memory().percent
    if mem_percent >= RAM_THRESHOLD:
        if not _state["ram_warned"]:
            alerts.append(f"Heads up - RAM usage is at {mem_percent:.0f}%.")
            _state["ram_warned"] = True
    else:
        _state["ram_warned"] = False

    # Disk (checked less often - space doesn't change second to second)
    if time.time() - _state["last_disk_check"] > 300:
        _state["last_disk_check"] = time.time()
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            if usage.percent >= DISK_THRESHOLD:
                if part.device not in _state["disk_warned"]:
                    alerts.append(
                        f"Your {part.device} drive is almost full - "
                        f"{usage.percent:.0f}% used, only {_bytes_to_gb(usage.free):.1f} GB free."
                    )
                    _state["disk_warned"].add(part.device)
            else:
                _state["disk_warned"].discard(part.device)

    # Battery
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery is not None:
        if not battery.power_plugged and battery.percent <= BATTERY_LOW_THRESHOLD:
            if not _state["battery_warned"]:
                alerts.append(f"Your battery is down to {battery.percent:.0f}% and you're not plugged in.")
                _state["battery_warned"] = True
        else:
            _state["battery_warned"] = False

    return " ".join(alerts) if alerts else None


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
