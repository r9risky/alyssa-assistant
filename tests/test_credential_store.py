import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import credential_store


class CredentialStoreTests(unittest.TestCase):
    def test_file_store_is_outside_project_when_data_dir_is_overridden(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"ALYSSA_DATA_DIR": temp_dir}, clear=False
        ):
            path = credential_store.credentials_path()
            self.assertEqual(path.parent, Path(temp_dir))

    def test_saved_llm_key_round_trips(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"ALYSSA_DATA_DIR": temp_dir}, clear=False
        ):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                credential_store.save_llm_credentials({"GEMINI_API_KEY": "stored-key"})
                self.assertEqual(
                    credential_store.get_llm_credential("GEMINI_API_KEY"), "stored-key"
                )

    def test_environment_variable_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"ALYSSA_DATA_DIR": temp_dir, "OPENAI_API_KEY": "environment-key"},
            clear=False,
        ):
            credential_store.save_llm_credentials({"OPENAI_API_KEY": "stored-key"})
            self.assertEqual(
                credential_store.get_llm_credential("OPENAI_API_KEY"),
                "environment-key",
            )

    def test_unknown_credentials_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"ALYSSA_DATA_DIR": temp_dir}, clear=False
        ):
            credential_store.save_llm_credentials({"NOT_ALLOWED": "secret"})
            self.assertNotIn("NOT_ALLOWED", credential_store.load_llm_credentials())


if __name__ == "__main__":
    unittest.main()
