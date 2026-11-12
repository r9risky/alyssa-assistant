import transcribe


def test_preload_reports_failed_model_download_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(transcribe, "start_streaming", lambda: None)

    def offline():
        raise OSError("couldn't reach huggingface.co")

    monkeypatch.setattr(transcribe, "_get_model", offline)

    message = transcribe.preload()

    assert message and "internet" in message


def test_preload_returns_none_when_model_loads(monkeypatch):
    monkeypatch.setattr(transcribe, "start_streaming", lambda: None)
    monkeypatch.setattr(transcribe, "_get_model", lambda: object())

    assert transcribe.preload() is None
