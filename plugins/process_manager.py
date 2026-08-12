"""
Process Manager & System Tune-Up plugin for Alyssa.

Gives Alyssa abilities to:
- Terminate specific running applications/processes ('kill_process')
- Identify top CPU or RAM consuming processes ('get_heavy_processes')
- Clean temporary system files ('clean_temp_files')
- Empty the Windows Recycle Bin ('empty_recycle_bin')
"""
import ctypes
import os

try:
    import psutil
except ImportError:
    psutil = None

import actions

_shell32 = ctypes.windll.shell32 if os.name == "nt" else None
SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


def kill_process(app_name: str, confirmed: bool = False) -> str:
    """Closes/terminates running processes matching app_name."""
    if not app_name or not app_name.strip():
        return "I need the name of the process or application to close."

    if psutil is None:
        return "I can't manage processes because psutil is not installed."

    target = app_name.strip().lower()
    if target.endswith(".exe"):
        target = target[:-4]

    matching = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info['name'] or '').lower()
            stem = name[:-4] if name.endswith(".exe") else name
            if target == stem or target in stem or stem in target:
                matching.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not matching:
        return f"I couldn't find any running process matching '{app_name}'."

    proc_summary = f"process '{matching[0].info['name']}' (PID {matching[0].pid})" if len(matching) == 1 else f"{len(matching)} process instances of '{target}'"

    if not confirmed and actions._critical_confirmation_callback:
        return actions._critical_confirmation_callback(
            "kill_process",
            f"terminate {proc_summary}",
            {"app_name": app_name},
        )

    killed_count = 0
    errors = 0
    for proc in matching:
        try:
            proc.terminate()
            killed_count += 1
        except Exception:
            try:
                proc.kill()
                killed_count += 1
            except Exception:
                errors += 1

    if killed_count > 0:
        msg = f"Closed {killed_count} process(es) matching '{app_name}'."
        if errors > 0:
            msg += f" ({errors} couldn't be terminated)."
        return msg
    return f"Failed to terminate processes matching '{app_name}' due to permissions."


def get_heavy_processes(sort_by: str = "memory", top_n: int = 5) -> str:
    """Returns the top processes consuming the most Memory or CPU."""
    if psutil is None:
        return "I can't check heavy processes because psutil is not installed."

    sort_by_lower = (sort_by or "memory").strip().lower()
    is_cpu = "cpu" in sort_by_lower

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent']):
        try:
            mem_mb = (proc.info['memory_info'].rss / (1024 * 1024)) if proc.info['memory_info'] else 0
            cpu_pct = proc.info['cpu_percent'] or 0.0
            name = proc.info['name'] or "Unknown"
            processes.append({
                "pid": proc.pid,
                "name": name,
                "mem_mb": mem_mb,
                "cpu_pct": cpu_pct,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if is_cpu:
        processes.sort(key=lambda x: x['cpu_pct'], reverse=True)
        unit_label = "CPU"
    else:
        processes.sort(key=lambda x: x['mem_mb'], reverse=True)
        unit_label = "Memory"

    top = processes[:max(1, min(20, top_n))]
    if not top:
        return "Couldn't retrieve process resource usage."

    lines = [f"Top {len(top)} processes by {unit_label} usage:"]
    for i, p in enumerate(top, 1):
        mem_fmt = f"{p['mem_mb']:.1f} MB" if p['mem_mb'] < 1024 else f"{p['mem_mb']/1024:.2f} GB"
        lines.append(f"{i}. {p['name']} (PID {p['pid']}) - RAM: {mem_fmt}, CPU: {p['cpu_pct']:.1f}%")

    return "\n".join(lines)


def clean_temp_files() -> str:
    """Cleans temporary files from system TEMP directories."""
    temp_dirs = []
    for var in ["TEMP", "TMP"]:
        val = os.environ.get(var)
        if val and os.path.isdir(val) and val not in temp_dirs:
            temp_dirs.append(val)

    bytes_freed = 0
    files_deleted = 0
    errors = 0

    for temp_dir in temp_dirs:
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    size = os.path.getsize(file_path)
                    os.remove(file_path)
                    bytes_freed += size
                    files_deleted += 1
                except Exception:
                    errors += 1

    freed_mb = bytes_freed / (1024 * 1024)
    if bytes_freed >= 1024 * 1024 * 1024:
        freed_str = f"{bytes_freed / (1024 * 1024 * 1024):.2f} GB"
    else:
        freed_str = f"{freed_mb:.1f} MB"

    return f"Cleaned {files_deleted} temporary file(s), freeing {freed_str} of disk space."


def empty_recycle_bin(confirmed: bool = False) -> str:
    """Empties the Windows Recycle Bin."""
    if _shell32 is None or not hasattr(_shell32, "SHEmptyRecycleBinW"):
        return "Emptying the Recycle Bin is only supported on Windows."

    if not confirmed and actions._critical_confirmation_callback:
        return actions._critical_confirmation_callback(
            "empty_recycle_bin",
            "empty the Windows Recycle Bin",
            {},
        )

    try:
        flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        res = _shell32.SHEmptyRecycleBinW(None, None, flags)
        if res == 0:
            return "Recycle Bin emptied successfully."
        elif res == 0x80004005:  # E_FAIL - often returned when already empty
            return "The Recycle Bin is already empty."
        else:
            return "Recycle Bin cleared."
    except Exception as e:
        return f"Couldn't empty the Recycle Bin: {e}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": "Closes or terminates a running application/process by name, e.g. 'close Chrome', 'kill Discord', 'end task notepad'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "The name of the app or process to close (e.g. 'chrome', 'discord', 'notepad')."}
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_heavy_processes",
            "description": "Lists the top system processes consuming the most CPU or Memory (RAM), e.g. 'what process is using the most RAM', 'show top CPU users'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {"type": "string", "enum": ["memory", "cpu"], "description": "Sort by 'memory' (RAM) or 'cpu'."},
                    "top_n": {"type": "integer", "description": "Number of processes to list (default 5)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_temp_files",
            "description": "Cleans temporary Windows files and cache in %TEMP% to free up disk space.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "empty_recycle_bin",
            "description": "Empties the Windows Recycle Bin.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "kill_process": kill_process,
    "get_heavy_processes": get_heavy_processes,
    "clean_temp_files": clean_temp_files,
    "empty_recycle_bin": empty_recycle_bin,
}
