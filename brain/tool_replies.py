"""How tool results become her spoken reply, and which tools need what treatment."""

import random


def _summarize_for_speech(output: str, max_chars: int = 140) -> str:
    """Condense multi-line output for speech only; full text still goes to console and the model."""
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return output
    first = lines[0].strip()
    if len(first) > max_chars:
        first = first[:max_chars].rstrip() + "..."
    if len(lines) == 1:
        return first
    extra = len(lines) - 1
    return f"{first} (+{extra} more line{'s' if extra != 1 else ''})"


def _natural_fast_reply(name: str, arguments: dict, output: str, user_text: str) -> str:
    """Spoken fast-path reply after a tool runs; no extra model call. Random phrasing per action."""
    output = str(output).strip()
    lowered_request = user_text.casefold()
    if any(word in output.casefold() for word in ("couldn't", "error", "cancelled", "failed", "blocked")):
        return output

    if name == "media_play_pause":
        if any(word in lowered_request for word in ("pause", "stop")):
            return random.choice(["Paused.", "That's paused.", "Music's paused."])
        if any(word in lowered_request for word in ("play", "resume", "continue")):
            return random.choice(["Resumed.", "That's playing again.", "Back on."])
        return random.choice(["Done.", "Updated the playback.", "That's toggled."])
    if name == "media_next_track":
        return random.choice(["Skipped.", "Next track's up.", "Moved on to the next one."])
    if name == "media_previous_track":
        return random.choice(["Went back a track.", "That's the previous one.", "Back a track."])
    if name == "volume_up":
        return random.choice(["Turned it up.", "Volume's up.", "Louder now."])
    if name == "volume_down":
        return random.choice(["Turned it down.", "Volume's down.", "Quieter now."])
    if name == "toggle_mute":
        return random.choice(["Done.", "Sound's toggled.", "That's handled."])
    if name == "open_app":
        app = arguments.get("app_name", "that")
        return random.choice([
            f"{app.capitalize()}'s open.",
            f"Done - {app}'s up.",
            f"There you go, {app}'s open.",
        ])
    if name == "type_text":
        return random.choice(["Typed it in.", "Done - that's entered.", "Got it typed."])
    if name == "press_keys":
        return random.choice(["Done.", "Pressed it.", "That's handled."])
    if name == "run_command":
        if output == "Command ran with no output.":
            return random.choice(["Done - that finished.", "That ran fine.", "Finished, no issues."])
        summary = _summarize_for_speech(output)
        return random.choice([f"Done - {summary}", f"That's finished. {summary}", f"All set - {summary}"])
    if name == "delete_file" and output.startswith("Moved "):
        return random.choice(["Moved to the Recycle Bin.", "Done - that's in the Recycle Bin.", "Sent it to the Recycle Bin."])
    if name == "remember_fact":
        # Do not speak memory.remember()'s "I'll remember that: {fact}" verbatim.
        fact = str(arguments.get("fact", "")).strip().rstrip(".")
        if not fact:
            return output
        if len(fact) <= 50:
            return random.choice([
                f"Got it - {fact}.",
                f"Noted, I'll remember that: {fact}.",
                f"Saved - {fact}.",
            ])
        return random.choice(["Got it, I'll remember that.", "Noted, that's saved.", "Done - I've saved that."])
    if name == "forget_fact":
        # forget() may remove multiple comma-joined facts; do not read them verbatim.
        if output.startswith("Forgot: "):
            forgotten = [f.strip() for f in output[len("Forgot: "):].split(",") if f.strip()]
            if len(forgotten) == 1 and len(forgotten[0].rstrip(".")) <= 50:
                item = forgotten[0].rstrip(".")
                return random.choice([f"Forgot that - {item}.", f"Done, that's cleared: {item}.", f"That's forgotten now: {item}."])
            if len(forgotten) > 1:
                return random.choice([
                    f"Forgot {len(forgotten)} things that matched that.",
                    f"Cleared {len(forgotten)} memories matching that.",
                ])
            return random.choice(["Forgot that.", "Done, that's cleared.", "That's forgotten now."])
    for past_tense, first_person in (
        ("Opened ", "Opened "),
        ("Closed ", "Closed "),
        ("Minimized ", "Minimized "),
        ("Maximized ", "Maximized "),
        ("Pressed ", "Pressed "),
        ("Turned ", "Turned "),
        ("Locked ", "Locked "),
    ):
        if output.startswith(past_tense):
            rest = output[len(past_tense):]
            return random.choice([
                f"{first_person}{rest}",
                f"Done - {first_person.lower()}{rest}",
            ])
    if output:
        return output
    return random.choice(["Done.", "That's handled.", "All set."])


_CONFIRMATION_GATED_TOOLS = {
    "delete_file", "run_command", "system_power_action", "click_screen_element",
    "kill_process", "clean_temp_files", "empty_recycle_bin",
    "edit_text_file", "append_to_text_file", "create_text_file", "undo_last_file_edit",
}


_UNTRUSTED_WEB_TOOLS = {"search_web", "summarize_webpage"}


_SAFE_AFTER_UNTRUSTED_WEB_TOOLS = _UNTRUSTED_WEB_TOOLS


# Their output is already what she should say (the hello when she joins a call, the line to
# pass on): it's the whole reply, with no second model call. Rephrasing it, the model told
# you what to say ("Just say 'Hey everyone!'") instead of saying it to the call itself.
_SAY_AS_IS_TOOLS = {"join_call", "leave_call", "relay_to_call", "confirm_friend_request"}


# Tools whose result _natural_fast_reply already phrases as her full answer: once a round of
# only these succeeds, she says it herself instead of paying for a second model call.
_SELF_PHRASED_TOOLS = {
    "open_app", "type_text", "press_keys", "open_url", "open_file", "delete_file",
    "remember_fact", "forget_fact", "play_music", "media_play_pause", "media_next_track",
    "media_previous_track", "volume_up", "volume_down", "toggle_mute", "set_volume_level",
    "minimize_window", "maximize_window", "close_window", "switch_window", "snap_window",
    "show_desktop", "set_clipboard", "set_quiet_mode", "reset_conversation",
}


_SKIP_ANNOUNCE_TOOLS = {"get_datetime", "read_clipboard", "run_diagnostics", "reset_conversation"}


def _sanitize_tool_arguments(arguments: dict) -> dict:
    """Drop `confirmed` from model-supplied tool args.

    - Internal-only; `_handle_pending_power_confirmation` sets it from trusted literals, never model JSON.
    - Strip every first-round call (not just gated tools) or the model can skip confirmation.
    """
    if "confirmed" in arguments:
        arguments = dict(arguments)
        del arguments["confirmed"]
    return arguments


def _natural_announce_reply(name: str, arguments: dict):
    """Present-tense pre-run phrase, or None to skip announce and run immediately.

    None for `_CONFIRMATION_GATED_TOOLS` and `_SKIP_ANNOUNCE_TOOLS`.
    """
    if name == "proactive_alert":
        return str(arguments.get("message", "")).strip() or None

    if name in _CONFIRMATION_GATED_TOOLS or name in _SKIP_ANNOUNCE_TOOLS:
        return None

    if name == "open_app":
        app = str(arguments.get("app_name", "that")).strip() or "that"
        return random.choice([f"Opening {app}...", f"On it - opening {app}...", f"Pulling up {app}..."])
    if name == "open_url":
        return random.choice(["Opening that up...", "Heading there now...", "Pulling that up..."])
    if name == "open_file":
        return random.choice(["Opening that file...", "Pulling that up...", "One sec, opening it..."])
    if name == "type_text":
        return random.choice(["Typing that in...", "Got it, typing now...", "Typing now..."])
    if name == "press_keys":
        keys = str(arguments.get("keys", "")).strip()
        return random.choice([f"Pressing {keys}...", "On it..."]) if keys else "On it..."
    if name == "search_files":
        return random.choice(["Searching for that...", "Looking for it now...", "Digging that up..."])
    if name == "play_music":
        return random.choice(["Putting that on...", "One sec, queuing that up...", "On it..."])
    if name in ("media_play_pause", "media_next_track", "media_previous_track"):
        return random.choice(["One sec...", "Got it..."])
    if name in ("volume_up", "volume_down", "toggle_mute"):
        return random.choice(["On it...", "One sec..."])
    if name == "minimize_window":
        return random.choice(["Minimizing that...", "One sec..."])
    if name == "maximize_window":
        return random.choice(["Maximizing that...", "One sec..."])
    if name == "close_window":
        return random.choice(["Closing that...", "Closing it now...", "On it..."])
    if name == "switch_window":
        return random.choice(["Switching over...", "One sec..."])
    if name == "snap_window":
        return random.choice(["Snapping that over...", "One sec..."])
    if name == "show_desktop":
        return random.choice(["Showing the desktop...", "One sec..."])
    if name == "set_clipboard":
        return random.choice(["Copying that...", "One sec..."])
    if name == "take_screenshot":
        return random.choice(["Taking a screenshot...", "One sec..."])
    if name == "describe_screen":
        return random.choice(["Let me take a look...", "One sec, looking...", "Checking your screen..."])
    if name == "remember_fact":
        return random.choice(["Noting that down...", "One sec..."])
    if name == "forget_fact":
        return random.choice(["Clearing that...", "One sec..."])
    return None
