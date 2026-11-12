import json
import queue
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

import config
import phone_remote as pr


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "TOKEN_FILE", str(tmp_path / "phone_remote.json"))
    monkeypatch.setattr(config, "PHONE_REMOTE_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "PHONE_REMOTE_PORT", _free_port(), raising=False)
    monkeypatch.setattr(config, "QUIET_MODE", False, raising=False)
    bridge = SimpleNamespace(text_queue=queue.Queue())
    assert pr.start(bridge) is None
    yield bridge, f"http://127.0.0.1:{config.PHONE_REMOTE_PORT}", pr.token()
    pr.stop()


def _call(url, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_and_api_need_the_secret(remote):
    _bridge, base, token = remote
    assert _call(base + "/")[0] == 403
    assert _call(base + "/?t=wrong")[0] == 403
    status, page = _call(f"{base}/?t={token}")
    assert status == 200 and b"Message Alyssa" in page
    assert _call(base + "/api/messages")[0] == 403
    assert _call(base + "/api/say", token="nope", body={"text": "hi"})[0] == 403


def test_say_goes_to_her_queue_and_replies_come_back(remote):
    bridge, base, token = remote
    assert _call(base + "/api/say", token, {"text": "  what's   the time "})[0] == 200
    assert bridge.text_queue.get_nowait() == "what's the time"
    pr.log("alyssa", "It's noon.")
    status, body = _call(base + "/api/messages?after=0", token)
    texts = [m["text"] for m in json.loads(body)["messages"]]
    assert status == 200 and "It's noon." in texts


def test_quiet_button_toggles(remote):
    _bridge, base, token = remote
    status, body = _call(base + "/api/action", token, {"action": "quiet"})
    assert status == 200 and config.QUIET_MODE is True


def test_only_local_network_addresses_count():
    assert pr._is_local("192.168.1.20") and pr._is_local("10.0.0.5") and pr._is_local("127.0.0.1")
    assert not pr._is_local("8.8.8.8") and not pr._is_local("garbage")


def test_token_is_kept_and_can_be_rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "TOKEN_FILE", str(tmp_path / "t.json"))
    first = pr.token()
    assert pr.token() == first and len(first) >= 20
    assert pr.token(regenerate=True) != first


def test_disabled_means_no_server(monkeypatch):
    monkeypatch.setattr(config, "PHONE_REMOTE_ENABLED", False, raising=False)
    assert pr.start() is None and pr._server is None
