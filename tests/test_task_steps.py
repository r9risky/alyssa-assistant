from unittest.mock import patch

import actions
import brain
from brain import dialogue


def test_step_label_is_short_and_readable():
    assert dialogue._step_label("search_web", {"query": "manila   weather", "num_results": 5}) == "Search web: manila weather"
    assert dialogue._step_label("show_desktop", {}) == "Show desktop"
    assert dialogue._step_label("open_url", {"url": "x" * 50}).endswith("…")


def test_handle_command_reports_each_tool_step():
    first = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "function": {"name": "read_value", "arguments": {"key": "rent"}}},
        {"id": "b", "function": {"name": "break_value", "arguments": {}}},
    ]}}
    final = {"message": {"role": "assistant", "content": "Done.", "tool_calls": []}}
    events = []

    def boom():
        raise RuntimeError("nope")

    try:
        with (
            patch.dict(actions.FUNCTIONS, {"read_value": lambda key: "12", "break_value": boom}),
            patch.object(dialogue, "_call_model_with_error_handling", side_effect=[(first, None), (final, None)]),
        ):
            brain.handle_command("Do both.", on_step=lambda label, state: events.append((label, state)))
    finally:
        brain.clear_conversation_history()

    assert events == [
        ("Read value: rent", "running"), ("Read value: rent", "done"),
        ("Break value", "running"), ("Break value", "failed"),
    ]


def test_bubble_lists_steps_while_working_and_hides_them_after():
    from overlay.widgets import SpeechBubble

    bubble = SpeechBubble(None, {"bubble_seconds": 5})
    with patch("voice_dsp.play_typing_blip"):
        bubble.show_thinking()
        bubble.show_steps([("Open app: <Spotify>", "done"), ("Play music: lofi", "running")])
        text = bubble.label.text()
        assert text.startswith("Thinking")
        assert "✓ Open app: &lt;Spotify&gt;" in text and "▸ Play music: lofi" in text

        bubble.show_message("Putting that on...")
        bubble.finish_typing()
        assert "▸ Play music" in bubble.label.text()

        bubble.show_steps([])
        assert bubble.label.text() == "Putting that on..."
