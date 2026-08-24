"""
Security/webcam plugin - motion detection on your webcam (or an IP camera
RTSP/HTTP stream), with a spoken alert. Off by default - say "Alyssa, turn
on the security camera" to arm it, "turn it off" to disarm.

Requires: pip install opencv-python (add to requirements.txt if missing).

Privacy note: this only ever compares consecutive frames for MOTION - it
never records, uploads, or saves footage anywhere. When you ask for a
snapshot (capture_camera_snapshot) it saves ONE image locally, same as
take_screenshot() does for your screen, and tells you exactly where.

How it works: enabling the camera starts its own background thread (not
main.py's watcher loop - motion detection needs frame-rate polling, much
faster than the shared watcher cadence). That thread just sets a flag
when it sees motion; check_watch() (called by main.py's watcher loop,
same as every other plugin) is what actually turns that flag into a
spoken alert, with a cooldown so one motion event doesn't repeat forever.
"""
import os
import sys
import threading
import time

try:
    import cv2
except ImportError:
    cv2 = None

# 0 = default webcam. Or a string like "rtsp://192.168.1.50:554/stream" for
# an IP camera.
CAMERA_SOURCE = 0

MOTION_THRESHOLD = 25          # per-pixel frame-diff sensitivity (lower = more sensitive)
MOTION_MIN_AREA_FRACTION = 0.02  # fraction of frame that must change to count as "motion"
ALERT_COOLDOWN_SECONDS = 120    # don't re-alert more often than this

WATCH_INTERVAL_SECONDS = 15  # check_watch() just checks the flag - cheap, can run often

_CV2_MISSING_MSG = "I can't use the camera - opencv-python isn't installed. Run: pip install opencv-python"

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_lock = threading.RLock()
_armed = False
_thread = None
_stop_event = None
_motion_detected_at = None   # timestamp of the most recent motion event, cleared once alerted
_last_alert_at = 0
_camera_error = None


def _capture_loop(stop_event: threading.Event):
    global _motion_detected_at, _camera_error
    cap = cv2.VideoCapture(CAMERA_SOURCE)
    if not cap.isOpened():
        _camera_error = f"Couldn't open camera source {CAMERA_SOURCE!r}."
        return
    try:
        prev_gray = None
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.5)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, gray)
                _, thresh = cv2.threshold(diff, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
                changed_fraction = cv2.countNonZero(thresh) / thresh.size
                if changed_fraction >= MOTION_MIN_AREA_FRACTION:
                    with _lock:
                        _motion_detected_at = time.time()

            prev_gray = gray
            time.sleep(0.3)  # ~3fps is plenty for motion detection, keeps CPU low
    finally:
        cap.release()


def enable_security_camera() -> str:
    global _armed, _thread, _stop_event, _camera_error, _motion_detected_at
    if cv2 is None:
        return _CV2_MISSING_MSG
    with _lock:
        if _armed:
            return "The security camera is already on."
        _camera_error = None
        _motion_detected_at = None
        _stop_event = threading.Event()
        _thread = threading.Thread(target=_capture_loop, args=(_stop_event,), daemon=True)
        _thread.start()
        _armed = True
    time.sleep(1.0)  # give it a moment to report an open failure
    if _camera_error:
        with _lock:
            _armed = False
        return _camera_error
    return "Security camera is on - I'll let you know if I see motion."


def disable_security_camera() -> str:
    global _armed
    with _lock:
        if not _armed:
            return "The security camera is already off."
        if _stop_event:
            _stop_event.set()
        _armed = False
    return "Security camera is off."


def camera_status() -> str:
    with _lock:
        return "Security camera is armed." if _armed else "Security camera is off."


def capture_camera_snapshot() -> str:
    """Saves a single still frame to Pictures, same convention as
    take_screenshot(). Works whether or not the camera is currently armed
    for motion detection - opens it just long enough for one frame."""
    if cv2 is None:
        return _CV2_MISSING_MSG
    cap = cv2.VideoCapture(CAMERA_SOURCE)
    try:
        if not cap.isOpened():
            return f"Couldn't open camera source {CAMERA_SOURCE!r}."
        ok, frame = cap.read()
        if not ok:
            return "Couldn't capture a frame from the camera."
        pictures_dir = os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(pictures_dir, exist_ok=True)
        filename = f"alyssa_camera_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(pictures_dir, filename)
        cv2.imwrite(path, frame)
        return f"Saved a snapshot to your Pictures folder as {filename}."
    finally:
        cap.release()


def check_watch():
    """Called by main.py's watcher loop. Turns a motion flag set by the
    background capture thread into a spoken alert, cooldown-limited."""
    global _last_alert_at, _motion_detected_at
    with _lock:
        if not _armed or _motion_detected_at is None:
            return None
        if time.time() - _last_alert_at < ALERT_COOLDOWN_SECONDS:
            return None
        _last_alert_at = time.time()
        _motion_detected_at = None
    return "Motion detected on the security camera."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "enable_security_camera",
            "description": "Arms motion detection on the webcam/security camera - e.g. 'turn on the security camera', 'watch the room'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disable_security_camera",
            "description": "Disarms motion detection on the webcam/security camera - e.g. 'turn off the security camera'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "camera_status",
            "description": "Reports whether the security camera is currently armed - e.g. 'is the camera on?'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_camera_snapshot",
            "description": "Saves a single still photo from the webcam to the Pictures folder - e.g. 'take a picture', 'snap a photo of the room'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "enable_security_camera": enable_security_camera,
    "disable_security_camera": disable_security_camera,
    "camera_status": camera_status,
    "capture_camera_snapshot": capture_camera_snapshot,
}
