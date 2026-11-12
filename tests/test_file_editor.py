import os

import pytest

from actions.confirmation import VoiceConfirmationRequired
from plugins import file_editor as fe


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(fe, "_home", lambda: os.path.realpath(home))
    monkeypatch.setattr(fe, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(fe, "_confirm", lambda description, force=False: True)
    return home


def test_edit_replaces_one_exact_match_and_undo_restores(home):
    notes = home / "notes.txt"
    notes.write_bytes(b"buy mlik\r\ncall mom\r\n")
    assert fe.edit_text_file(str(notes), "mlik", "milk") == "Updated notes.txt."
    assert notes.read_bytes() == b"buy milk\r\ncall mom\r\n"  # CRLF kept
    assert "Undid" in fe.undo_last_file_edit()
    assert notes.read_bytes() == b"buy mlik\r\ncall mom\r\n"


def test_edit_refuses_missing_or_ambiguous_text(home):
    notes = home / "notes.txt"
    notes.write_text("a a", encoding="utf-8")
    assert "couldn't find" in fe.edit_text_file(str(notes), "zzz", "b")
    assert "2 times" in fe.edit_text_file(str(notes), "a", "b")
    assert notes.read_text(encoding="utf-8") == "a a"


def test_append_and_create_then_undo_removes_created_file(home):
    todo = home / "todo.md"
    todo.write_text("- one", encoding="utf-8")
    assert fe.append_to_text_file(str(todo), "- two") == "Added that to todo.md."
    assert todo.read_text(encoding="utf-8") == "- one\n- two\n"

    new = home / "new.txt"
    assert fe.create_text_file(str(new), "hi") == "Created new.txt."
    assert "already exists" in fe.create_text_file(str(new), "again")
    fe.undo_last_file_edit()
    assert not new.exists()


def test_refuses_outside_home_scripts_and_her_own_folder(home, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert "user folder" in fe.read_text_file(str(outside))
    script = home / "run.bat"
    script.write_text("echo", encoding="utf-8")
    assert "plain text" in fe.edit_text_file(str(script), "echo", "del")
    assert script.read_text(encoding="utf-8") == "echo"


def test_her_own_folder_is_off_limits(monkeypatch):
    monkeypatch.setattr(fe, "_home", lambda: os.path.realpath(fe._BASE_DIR))
    assert "my own files" in fe.read_text_file(os.path.join(fe._BASE_DIR, "README.md"))


def test_declined_or_pending_approval_changes_nothing(home, monkeypatch):
    notes = home / "notes.txt"
    notes.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fe, "_confirm", lambda description, force=False: False)
    assert fe.edit_text_file(str(notes), "old", "new") == "Cancelled by user."

    def pending(description, force=False):
        raise VoiceConfirmationRequired

    monkeypatch.setattr(fe, "_confirm", pending)
    with pytest.raises(VoiceConfirmationRequired):
        fe.edit_text_file(str(notes), "old", "new")
    assert notes.read_text(encoding="utf-8") == "old"
    # the spoken "yes" path calls back with confirmed=True
    assert fe.edit_text_file(str(notes), "old", "new", confirmed=True) == "Updated notes.txt."
