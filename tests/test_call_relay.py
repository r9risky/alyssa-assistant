from unittest.mock import patch

import brain.dialogue as dialogue
from call_ears import get_call_session
from plugins.call_mode import relay_to_call


def test_relay_tool_never_plays_audio_itself():
    session = get_call_session()
    session.start_call("Discord", "general")
    try:
        with patch("voice_playback.speak") as speak:
            out = relay_to_call({"message": "be there soon"})
        speak.assert_not_called()
        assert "be there soon" in out
    finally:
        session.end_call()


def test_tell_them_skips_the_model_during_a_call():
    session = get_call_session()
    session.start_call("Discord", "general")
    try:
        with patch.object(dialogue.ui_config, "USER_NICKNAME", "Roi"):
            reply = dialogue._handle_pre_model_intent("Alyssa, tell them I'm joining in ten minutes")
        assert reply == "Message from Roi: I'm joining in ten minutes."
    finally:
        session.end_call()
    assert dialogue._handle_pre_model_intent("tell them I'm late") is None
