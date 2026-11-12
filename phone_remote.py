"""Phone remote: talk to Alyssa from your phone's browser on the same Wi-Fi.

Off by default (Settings > Companion > Phone remote). When on, a tiny web page
is served on PHONE_REMOTE_PORT. It only answers devices on your local network,
and only with the secret link from Settings (scan the QR code). From the page
you can type to her, see her replies, and use quick media/quiet buttons.
"""
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

MAX_LOG = 50
MAX_TEXT = 500

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(_BASE_DIR, "phone_remote.json")

_lock = threading.Lock()
_log: list[dict] = []
_next_id = 1
_server: ThreadingHTTPServer | None = None
_bridge = None


def log(who: str, text: str, sources=None) -> None:
    """Add a line to the chat the phone and the chat history window show ('you' or 'alyssa')."""
    global _next_id
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        entry = {"id": _next_id, "who": who, "text": text[:2000], "time": time.strftime("%H:%M")}
        if sources:
            entry["sources"] = list(sources)
        _log.append(entry)
        _next_id += 1
        del _log[:-MAX_LOG]


def messages_after(after: int) -> list[dict]:
    with _lock:
        return [m for m in _log if m["id"] > after]


def token(regenerate: bool = False) -> str:
    """The secret that the phone link carries; made once and kept in phone_remote.json."""
    if not regenerate:
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                saved = str(json.load(f).get("token", ""))
            if len(saved) >= 20:
                return saved
        except (OSError, ValueError, AttributeError):
            pass
    fresh = secrets.token_urlsafe(18)
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"token": fresh}, f)
    os.replace(tmp, TOKEN_FILE)
    return fresh


def lan_ip() -> str:
    """This PC's address on the local network (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # TEST-NET address: only picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port() -> int:
    return int(getattr(config, "PHONE_REMOTE_PORT", 47900))


def phone_url() -> str:
    return f"http://{lan_ip()}:{port()}/?t={token()}"


def _is_local(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _quick_action(name: str) -> str:
    import actions

    if name == "quiet":
        config.QUIET_MODE = not bool(getattr(config, "QUIET_MODE", False))
        return "Quiet mode is on." if config.QUIET_MODE else "Quiet mode is off."
    funcs = {"play_pause": "media_play_pause", "next": "media_next_track", "previous": "media_previous_track"}
    func = actions.FUNCTIONS.get(funcs.get(name, ""))
    if func is None:
        return "Unknown button."
    return str(func())


class _Handler(BaseHTTPRequestHandler):
    server_version = "AlyssaRemote"

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode("utf-8"))

    def _allowed(self, supplied: str) -> bool:
        return _is_local(self.client_address[0]) and hmac.compare_digest(supplied or "", token())

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            if not self._allowed((query.get("t") or [""])[0]):
                return self._send(403, b"Open the link from Alyssa's Settings.", "text/plain; charset=utf-8")
            return self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if parsed.path == "/api/messages":
            if not self._allowed(self.headers.get("X-Token", "")):
                return self._json(403, {"error": "forbidden"})
            try:
                after = int((query.get("after") or ["0"])[0])
            except ValueError:
                after = 0
            return self._json(200, {"messages": messages_after(after)})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._allowed(self.headers.get("X-Token", "")):
            return self._json(403, {"error": "forbidden"})
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return self._json(400, {"error": "bad request"})
        if self.path == "/api/say":
            text = " ".join(str(body.get("text", "")).split())[:MAX_TEXT]
            if not text:
                return self._json(400, {"error": "empty"})
            if _bridge is None or getattr(_bridge, "text_queue", None) is None:
                return self._json(503, {"error": "Alyssa isn't ready"})
            _bridge.text_queue.put(text)  # same path as the typing bar
            return self._json(200, {"ok": True})
        if self.path == "/api/action":
            result = _quick_action(str(body.get("action", "")))
            log("alyssa", result)
            return self._json(200, {"ok": True, "result": result})
        self._json(404, {"error": "not found"})


def start(bridge=None) -> str | None:
    """Start serving if enabled. Returns an error message, or None when running/disabled."""
    global _server, _bridge
    _bridge = bridge if bridge is not None else _bridge
    if not getattr(config, "PHONE_REMOTE_ENABLED", False):
        stop()
        return None
    if _server is not None:
        return None
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port()), _Handler)
    except OSError as e:
        return f"Phone remote couldn't use port {port()}: {e}"
    server.daemon_threads = True
    _server = server
    threading.Thread(target=server.serve_forever, daemon=True, name="phone-remote").start()
    print(f"[phone remote] Listening on port {port()} (local network only).")
    return None


def stop() -> None:
    global _server
    if _server is not None:
        server, _server = _server, None
        server.shutdown()
        server.server_close()


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alyssa</title>
<style>
:root{--bg:#0f1220;--card:#1a1f33;--text:#eef0fa;--sub:#9aa1bd;--accent:#6c63ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.4 -apple-system,system-ui,"Segoe UI",sans-serif;display:flex;flex-direction:column;height:100dvh}
header{padding:14px 16px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
header small{color:var(--sub);font-weight:400}
#log{flex:1;overflow-y:auto;padding:0 16px 8px;display:flex;flex-direction:column;gap:8px}
.m{max-width:85%;padding:9px 12px;border-radius:14px;background:var(--card);white-space:pre-wrap;word-wrap:break-word}
.you{align-self:flex-end;background:var(--accent)}.t{display:block;font-size:11px;color:var(--sub)}
.you .t{color:#e5e3ff}
#quick{display:flex;gap:8px;padding:8px 16px;overflow-x:auto}
button{border:0;border-radius:12px;background:var(--card);color:var(--text);padding:10px 14px;font:inherit;font-size:14px}
form{display:flex;gap:8px;padding:8px 16px 16px}
input{flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--card);color:var(--text);font:inherit}
form button{background:var(--accent)}
</style></head><body>
<header>Alyssa <small id="st">connecting…</small></header>
<div id="log"></div>
<div id="quick">
<button data-a="play_pause">⏯ Play/Pause</button><button data-a="previous">⏮</button>
<button data-a="next">⏭</button><button data-a="quiet">🤫 Quiet</button>
</div>
<form id="f"><input id="i" placeholder="Message Alyssa" maxlength="500" autocomplete="off"><button>Send</button></form>
<script>
const T=new URLSearchParams(location.search).get("t")||"";let last=0;
const H={"X-Token":T,"Content-Type":"application/json"};
const log=document.getElementById("log"),st=document.getElementById("st");
function add(m){const d=document.createElement("div");d.className="m "+(m.who==="you"?"you":"");
const t=document.createElement("span");t.className="t";t.textContent=m.time;d.textContent=m.text;d.prepend(t);
log.appendChild(d);log.scrollTop=log.scrollHeight;}
async function poll(){try{const r=await fetch("/api/messages?after="+last,{headers:H});
if(!r.ok)throw 0;const j=await r.json();j.messages.forEach(m=>{add(m);last=m.id});st.textContent="connected";}
catch(e){st.textContent="offline"}setTimeout(poll,1500)}
document.getElementById("f").onsubmit=async e=>{e.preventDefault();const i=document.getElementById("i");
const text=i.value.trim();if(!text)return;i.value="";
try{await fetch("/api/say",{method:"POST",headers:H,body:JSON.stringify({text})})}catch(e){st.textContent="offline"}};
document.querySelectorAll("#quick button").forEach(b=>b.onclick=()=>fetch("/api/action",
{method:"POST",headers:H,body:JSON.stringify({action:b.dataset.a})}));
poll();
</script></body></html>
"""
