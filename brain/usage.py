"""Running count of model tokens used today, shown in Settings and logged per call."""
import datetime
import threading

import telemetry

_lock = threading.Lock()
_day = None
_totals = {"input": 0, "output": 0, "calls": 0}


def record(provider: str, input_tokens, output_tokens) -> None:
    """Add one model call's token usage. Missing counts are treated as 0."""
    global _day
    input_tokens, output_tokens = int(input_tokens or 0), int(output_tokens or 0)
    today = datetime.date.today()
    with _lock:
        if _day != today:
            _day = today
            _totals.update(input=0, output=0, calls=0)
        _totals["input"] += input_tokens
        _totals["output"] += output_tokens
        _totals["calls"] += 1
    telemetry.log(f"[tokens] {provider}: {input_tokens} in / {output_tokens} out")


def today() -> dict:
    """{"input", "output", "calls"} for today (zeros if nothing ran yet today)."""
    with _lock:
        if _day != datetime.date.today():
            return {"input": 0, "output": 0, "calls": 0}
        return dict(_totals)
