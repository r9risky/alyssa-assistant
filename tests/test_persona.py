import unittest
from unittest.mock import patch

import config
from brain import dialogue, persona


class TidyReplyTests(unittest.TestCase):
    def test_strips_markdown_and_keeps_list_items_as_sentences(self):
        self.assertEqual(
            persona.tidy_reply("Here's the **plan**:\n- Open Chrome\n- Search it"),
            "Here's the plan: Open Chrome. Search it.",
        )

    def test_strips_assistant_speak_and_emoji(self):
        self.assertEqual(
            persona.tidy_reply("Certainly! here's the time 🙂. Let me know if you need anything else!"),
            "Here's the time.",
        )
        self.assertEqual(persona.tidy_reply("As an AI, I can't taste pizza 🍕."), "I can't taste pizza.")

    def test_keeps_meaningful_words(self):
        for text in ("Absolutely not, that's a bad idea.", "3 * 4 * 5 is 60.", "Of course it does."):
            with self.subTest(text=text):
                self.assertEqual(persona.tidy_reply(text), text)

    def test_link_keeps_its_label(self):
        self.assertEqual(persona.tidy_reply("See [the docs](https://x.y/z)."), "See the docs.")


class PromptTests(unittest.TestCase):
    def test_every_mode_uses_the_same_voice(self):
        with patch.object(config, "USER_NICKNAME", "Roi"):
            prompt = dialogue._compact_system_prompt()
        self.assertIn(persona.voice(config.ASSISTANT_NAME, "Roi"), prompt)
        self.assertNotIn("terse", prompt)

    def test_final_reply_is_tidied(self):
        self.assertEqual(
            dialogue._finalize_model_reply("hi", "Certainly! **Hey** there.", False), "Hey there."
        )
        dialogue.clear_conversation_history()


if __name__ == "__main__":
    unittest.main()


def test_personality_preset_changes_only_the_vibe():
    with patch.object(config, "PERSONALITY_PRESET", "sassy"):
        sassy = persona.voice("Alyssa", "Roi")
    assert "sassy" in sassy and "no markdown" in sassy and "Roi's desktop companion" in sassy
    with patch.object(config, "PERSONALITY_PRESET", "nonsense"):
        assert persona.current_preset() == "playful"
    assert persona.voice("Alyssa", preset="focused") != persona.voice("Alyssa", preset="chill")
