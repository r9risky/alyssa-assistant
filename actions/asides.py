"""Mid-job interruptible asides and quick query handler.

Idea credit: Capri Desktop's asides feature (https://github.com/lachydotmcg/capri-desktop).
Allows users to speak or type to Alyssa while a task, script, or game run is active:
- 'Stop': "stop", "cancel it", "abort", "never mind", "that's enough" -> immediately halts task.
- 'Status': "how's it going?", "what are you doing?", "what step?", "progress?" -> instant spoken status without slow LLM roundtrip.
- 'Screen': "what's on my screen?", "what do you see?" -> reports current vision glance.
- 'Change': "instead do X", "change it to Y" -> updates pending task parameters.
- 'Chat': general conversational banter beside the job.
"""

from __future__ import annotations

import enum
import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("actions.asides")


class AsideKind(enum.Enum):
    STOP = "stop"
    STATUS = "status"
    SCREEN = "screen"
    CHANGE = "change"
    ADD = "add"
    CHAT = "chat"


# Words people put in front of a remark ("ok alyssa, stop") that don't change its meaning.
_LEAD_INS = {
    "alyssa", "hey", "ok", "okay", "oh", "um", "uh", "no", "nah", "nope", "wait", "actually",
    "just", "please", "alright", "right", "so", "yo",
}

# A whole remark (after lead-ins) that means "stop the job".
_STOP_PHRASES = {
    "stop", "stop stop", "stop it", "stop that", "stop now", "stop right now", "stop everything",
    "stop the job", "stop the task", "stop doing that", "stop what you're doing", "stop working",
    "stop playing", "halt", "hold it", "cancel", "cancel it", "cancel that", "cancel the job",
    "cancel the task", "cancel everything", "abort", "abort it", "abort that", "abort task",
    "abort the task", "abort job", "abort the job", "abort mission", "never mind", "nevermind",
    "nvm", "forget it", "forget that", "forget about it", "drop it", "leave it", "scrap that",
    "scrap it", "enough", "that's enough", "thats enough", "don't do that", "dont do that",
    "don't bother", "quit it", "knock it off", "cut it out", "call it off", "give up",
    "pause", "freeze",
    # A few other languages
    "para", "párate", "detente", "cancela", "stopp", "hör auf", "arrête", "стоп", "хватит",
}

_STATUS_RE = re.compile(
    r"\b(?:how(?:'s| is) (?:it|that|this|everything|the \w+) (?:going|coming along|looking)"
    r"|how(?:'s| is) it going|how far along|how much longer|any (?:progress|luck|news|update)"
    r"|what (?:are|r) (?:you|u) (?:doing|up to|working on)|whatcha (?:doing|up to)"
    r"|what step|which step|where are you (?:at|up to)"
    r"|(?:are|r) (?:you|u) (?:done|finished|almost done|nearly done|stuck|still going)"
    r"|is it (?:done|finished|ready)|still (?:working|going|on it)|what's taking so long"
    r"|what's (?:the )?(?:status|progress|holdup|hold up))\b"
    r"|^(?:status|progress|update)\??$",
    re.IGNORECASE,
)

_SCREEN_RE = re.compile(
    r"\bwhat (?:do|can) (?:you|u) see\b|\bwhat are you (?:looking at|seeing)\b"
    r"|\bwhat's on (?:my|the|your) screen\b|\bwhat does (?:it|that|this) say\b"
    r"|\bcan you (?:see|read) (?:it|that|this)\b|^read (?:it|that|this)(?: out)?$",
    re.IGNORECASE,
)

_CHANGE_RE = re.compile(
    r"\b(?:instead|rather|not that one|no not|wrong one|switch to|change (?:it|that) to"
    r"|make (?:it|that)|go with|use .+ not|don't use)\b",
    re.IGNORECASE,
)


def _core(text: str) -> str:
    """Lower-case, drop end punctuation and leading filler words: 'OK Alyssa, stop!' -> 'stop'."""
    words = re.sub(r"[.!?,]+", " ", (text or "").lower().replace("’", "'")).split()
    while words and words[0] in _LEAD_INS:
        words.pop(0)
    return " ".join(words)


class ActiveJobState:
    """Holds active background job status for instant zero-latency aside replies."""

    def __init__(self):
        self.job_id: Optional[str] = None
        self.description: str = ""
        self.current_step: str = ""
        self.step_index: int = 0
        self.total_steps: int = 0
        self.start_time: float = 0.0
        self.is_active: bool = False
        self._cancel_requested: bool = False

    def start_job(self, description: str, total_steps: int = 1, job_id: Optional[str] = None) -> None:
        self.job_id = job_id or f"job_{int(time.time())}"
        self.description = description
        self.total_steps = total_steps
        self.step_index = 1
        self.current_step = "Starting task"
        self.start_time = time.time()
        self.is_active = True
        self._cancel_requested = False

    def update_step(self, step_text: str, step_index: Optional[int] = None) -> None:
        self.current_step = step_text
        if step_index is not None:
            self.step_index = step_index
        else:
            self.step_index += 1

    def finish_job(self) -> None:
        self.is_active = False
        self.current_step = "Finished"

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self.is_active = False

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested


# App-wide singleton active job tracker
ACTIVE_JOB = ActiveJobState()


def classify_aside(text: str) -> AsideKind:
    """Sort a remark made while a job runs, without a model call."""
    core = _core(text)
    if core in _STOP_PHRASES:
        return AsideKind.STOP
    if _STATUS_RE.search(core):
        return AsideKind.STATUS
    if _SCREEN_RE.search(core):
        return AsideKind.SCREEN
    if _CHANGE_RE.search(core):
        return AsideKind.CHANGE
    return AsideKind.CHAT


def format_status_reply(job: Optional[ActiveJobState] = None) -> str:
    """Generate instantaneous, natural conversational status reply."""
    state = job or ACTIVE_JOB
    if not state.is_active:
        return "Not running any background job right now. Standing by!"

    elapsed = int(time.time() - state.start_time)
    elapsed_str = f"{elapsed}s" if elapsed < 60 else f"{elapsed // 60}m {elapsed % 60}s"

    if state.total_steps > 1:
        return (
            f"On step {state.step_index} of {state.total_steps} ({state.current_step}). "
            f"Been at it for {elapsed_str}. Everything's moving along!"
        )
    return f"Right now: {state.current_step}. Running for {elapsed_str}."
