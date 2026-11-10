"""Mid-job interruptible asides and quick query handler.

Inspired by Capri Desktop's `Asides.cs`:
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


# Fast regex patterns matching user interrupts
_STOP_PATTERN = re.compile(
    r"^((alyssa|capri|no|nah|nope|ok|okay|hey|wait|actually|just|alright|right)[\s,.!-]+)*"
    r"(stop( stop)*|stop (it|that|this|there|now|everything|working|playing|doing (it|that|this|everything)|what you'?re doing|right now|right there|the (job|task))"
    r"|cancel|cancel (it|that|this|everything|the (job|task))"
    r"|abort|abort (?:it|that|(?:the\s+)?(?:job|task|mission))"
    r"|forget (it|that|about (it|that))|never ?mind|nvm|drop (it|that)|leave it|leave it there|scrap (it|that)|that'?s enough|enough"
    r"|don'?t (do (it|that)|bother)|quit it|cut it out|knock it off|hands off|let go|call it off|give up"
    r"|para|p[aá]rate|det[eé]nte|cancela|stopp|h[öo]r auf|arr[eê]te|стоп|хватит)$",
    re.IGNORECASE,
)

_STATUS_PATTERN = re.compile(
    r"\bwhat(?:'?re| are| r|cha)? (?:you|u|ya)? ?(?:doing|doin|up to|working on)\b|\bwhatcha\b"
    r"|\bwhat'?s (?:happening|going on|the (?:status|hold ?up|progress|plan))\b"
    r"|\bhow(?:'?s| is)? (?:it|that|this|things|the \w+|your \w+) (?:going|coming(?: along)?|looking|getting on|doing)\b"
    r"|\bhow are (?:you|u|things) (?:going|getting on|coming along)\b|\bhow'?s (?:it|that)$|\bhow far\b"
    r"|\bwhere are (?:you|u) (?:up to|at)\b|\bwhat step\b|\bany (?:progress|luck|news)\b|^(?:status|progress|update)$"
    r"|\b(?:are|r) (?:you|u) (?:done|finished|nearly|almost|close|stuck|still|there yet)\b|\bis it (?:done|finished|ready)\b"
    r"|\bstill (?:going|working|on it)\b|\bwhat'?s taking\b",
    re.IGNORECASE,
)

_SCREEN_PATTERN = re.compile(
    r"\bwhat (?:do|can) (?:you|u|ya) see\b|\bwhat (?:are|r) (?:you|u|ya) (?:looking at|seeing)\b"
    r"|\bwhat'?s on (?:the|my|your|that) screen\b|\bwhat does (?:it|that|this) say\b|\bread (?:it|that|this)(?: out)?$"
    r"|\bcan (?:you|u) (?:see|read) (?:it|that|this)\b",
    re.IGNORECASE,
)

_CHANGE_PATTERN = re.compile(
    r"\b(instead|rather|actually|not that|not this|no not|wrong|don'?t use|change (?:it|that|this)|make it|make that|switch to|go with|use .+ not)\b",
    re.IGNORECASE,
)


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
    """Classify user's mid-job remark using fast regex."""
    clean = (text or "").strip()
    if _STOP_PATTERN.match(clean):
        return AsideKind.STOP
    if _STATUS_PATTERN.search(clean):
        return AsideKind.STATUS
    if _SCREEN_PATTERN.search(clean):
        return AsideKind.SCREEN
    if _CHANGE_PATTERN.search(clean):
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
