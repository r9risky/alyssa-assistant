"""Runs add-ons that didn't ship with Alyssa in a separate process, held to the
permissions you approved.

The add-on can't touch Alyssa's memory, settings or API keys (secrets are
stripped from its environment), and an audit hook in its process refuses what
it didn't ask for: the internet without "network", programs without "shell",
native code without a device permission, and files outside its own data folder
without "files". It talks to Alyssa only through JSON lines on stdin/stdout.

shortcut: the audit hook stops honest mistakes and casual misuse, not a determined
attacker who finds a gap in CPython; OS-level isolation (AppContainer / job objects)
is the upgrade if add-ons from strangers become common.
"""
import json
import os
import queue
import subprocess
import sys
import threading

CALL_TIMEOUT_SECONDS = 60
START_TIMEOUT_SECONDS = 30
HOST_FLAG = "--plugin-host"
_SECRET_ENV_VARS = (
    "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CUSTOM_LLM_API_KEY",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "YOUTUBE_API_KEY", "DISCORD_BOT_TOKEN",
)
_DEVICE_PERMISSIONS = {"keyboard_mouse", "screen", "microphone", "camera"}  # these need native code


def data_dir(plugin_path: str) -> str:
    """The one folder an add-on without "files" may write to: plugins/data/<name>/."""
    stem = os.path.basename(plugin_path).removesuffix(".py").lstrip("_")
    return os.path.join(os.path.dirname(os.path.abspath(plugin_path)), "data", stem)


class SandboxedPlugin:
    """Alyssa's side: starts the add-on's process and forwards calls to it."""

    def __init__(self, path: str, permissions):
        self.path = os.path.abspath(path)
        self.name = os.path.basename(path)
        self.permissions = sorted(set(permissions or []))
        self._lock = threading.Lock()
        self._proc = None
        self._replies = None
        self._next_id = 0
        self.manifest = self._start()

    def _command(self) -> list:
        perms = ",".join(self.permissions)
        if getattr(sys, "frozen", False):  # main.py hands this flag straight to host_main()
            return [sys.executable, HOST_FLAG, self.path, perms]
        return [sys.executable, os.path.abspath(__file__), self.path, perms]

    def _start(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV_VARS}
        env["PYTHONIOENCODING"] = "utf-8"
        os.makedirs(data_dir(self.path), exist_ok=True)
        self._proc = subprocess.Popen(
            self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=env, cwd=data_dir(self.path),
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
        )
        self._replies = queue.Queue()
        threading.Thread(target=self._read_replies, args=(self._proc, self._replies), daemon=True).start()
        threading.Thread(target=self._forward_log, args=(self._proc,), daemon=True).start()
        reply = self._wait(START_TIMEOUT_SECONDS)
        if reply is None or "manifest" not in reply:
            self.close()
            raise RuntimeError((reply or {}).get("error") or "its sandbox didn't start")
        return reply["manifest"]

    @staticmethod
    def _read_replies(proc, replies):
        for line in proc.stdout:
            try:
                replies.put(json.loads(line))
            except ValueError:
                continue
        replies.put(None)  # process ended

    def _forward_log(self, proc):
        for line in proc.stderr:
            print(f"[plugin {self.name}] {line.rstrip()}")

    def _wait(self, timeout):
        try:
            return self._replies.get(timeout=timeout)
        except queue.Empty:
            return None

    def call(self, target: str, *args, timeout: float = CALL_TIMEOUT_SECONDS, **kwargs):
        """Run one of its functions. Hooks ('__intent__', '__watch__') get None back on any failure,
        so a broken add-on can't answer for everything you say."""
        hook = target.startswith("__")
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self.manifest = self._start()  # crashed or timed out earlier: fresh process
            self._next_id += 1
            request = {"id": self._next_id, "call": target, "args": list(args), "kwargs": kwargs}
            try:
                self._proc.stdin.write(json.dumps(request, default=str) + "\n")
                self._proc.stdin.flush()
            except OSError as e:
                self.close()
                return None if hook else f"The {self.name} add-on stopped working ({e})."
            reply = self._wait(timeout)
            while reply is not None and reply.get("id") != self._next_id:  # stale answer to a timed-out call
                reply = self._wait(timeout)
            if reply is None:
                print(f"[plugin {self.name}] {target} took over {timeout:g}s; stopped it.")
                self.close()
                return None if hook else f"The {self.name} add-on took too long, so I stopped it."
            if "error" in reply:
                print(f"[plugin {self.name}] {target} failed: {reply['error']}")
                return None if hook else f"The {self.name} add-on hit a problem: {reply['error']}"
            return reply.get("result")

    def close(self):
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()


# ---- the add-on's side (runs in the child process) ----

def _is_write(mode, flags) -> bool:
    if isinstance(mode, str):
        return any(c in mode for c in "wax+")
    if isinstance(flags, int):
        return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
    return False


def _inside(path: str, roots) -> bool:
    try:
        real = os.path.realpath(path)
    except (TypeError, ValueError):
        return False
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def install_guard(permissions, plugin_path: str) -> None:
    """Refuse what the add-on didn't ask for. Audit hooks can't be removed once added."""
    perms = set(permissions)
    own_data = os.path.realpath(data_dir(plugin_path))
    code_roots = {os.path.realpath(p) for p in (
        sys.prefix, sys.base_prefix, sys.exec_prefix, getattr(sys, "_MEIPASS", ""),
        *[p for p in sys.path if "site-packages" in p or "dist-packages" in p],
    ) if p}
    readable = code_roots | {os.path.realpath(os.path.dirname(os.path.abspath(plugin_path))), own_data}

    def refuse(what):
        raise PermissionError(f"this add-on didn't ask for permission to {what}")

    def hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto") and "network" not in perms:
            refuse("use the internet")
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
                     "os.startfile", "os.fork") and "shell" not in perms:
            refuse("run programs")
        if event.startswith("ctypes.") and not perms & _DEVICE_PERMISSIONS:
            refuse("run native code")
        if "files" in perms:
            return
        if event == "open" and args and not isinstance(args[0], int):
            path = os.fsdecode(args[0])
            writing = _is_write(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
            if not _inside(path, {own_data} if writing else readable):
                refuse(("change " if writing else "read ") + path)
        if event in ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "shutil.rmtree", "shutil.move"):
            if not all(_inside(os.fsdecode(a), {own_data}) for a in args[:2] if isinstance(a, (str, bytes, os.PathLike))):
                refuse("change files")

    sys.addaudithook(hook)


def host_main(plugin_path: str, permissions: str) -> int:
    """Child process: load the add-on under the guard, then answer calls until stdin closes."""
    import importlib.util

    proto, sys.stdout = sys.stdout, sys.stderr  # its print()s go to the log, not the protocol
    sys.dont_write_bytecode = True

    def send(message):
        proto.write(json.dumps(message, default=str) + "\n")
        proto.flush()

    perms = [p for p in permissions.split(",") if p]
    os.makedirs(data_dir(plugin_path), exist_ok=True)
    install_guard(perms, plugin_path)
    stem = os.path.basename(plugin_path).removesuffix(".py")
    try:
        spec = importlib.util.spec_from_file_location(f"alyssa_sandboxed_{stem}", plugin_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except BaseException as e:  # SystemExit or a refused permission at import time too
        send({"error": f"{type(e).__name__}: {e}"})
        return 1
    functions = getattr(module, "FUNCTIONS", {}) or {}
    functions = {n: f for n, f in functions.items() if isinstance(n, str) and callable(f)} if isinstance(functions, dict) else {}
    tools = getattr(module, "TOOLS", []) or []
    send({"manifest": {
        "functions": sorted(functions),
        "tools": [t for t in tools if isinstance(t, dict)] if isinstance(tools, (list, tuple)) else [],
        "instant": sorted(set(getattr(module, "INSTANT_REPLY_TOOLS", ()) or ())),
        "untrusted": sorted(set(getattr(module, "UNTRUSTED_OUTPUTS", ()) or ())),
        "intent": callable(getattr(module, "handle_intent", None)),
        "watch": callable(getattr(module, "check_watch", None)),
        "interval": getattr(module, "WATCH_INTERVAL_SECONDS", 120),
        "always": bool(getattr(module, "ANNOUNCE_IN_QUIET_MODE", False)),
        "panel": bool(getattr(module, "GUI_PANEL_TITLE", None)),
    }})
    targets = dict(functions, __intent__=getattr(module, "handle_intent", None),
                   __watch__=getattr(module, "check_watch", None))
    for line in sys.stdin:
        try:
            request = json.loads(line)
            target = targets.get(request.get("call"))
            if not callable(target):
                raise LookupError(f"no function {request.get('call')!r}")
            send({"id": request.get("id"), "result": target(*request.get("args", []), **request.get("kwargs", {}))})
        except Exception as e:
            send({"id": locals().get("request", {}).get("id"), "error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(host_main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
