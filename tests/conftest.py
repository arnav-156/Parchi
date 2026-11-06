import pytest

from parchi import config, gnani_client


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch, tmp_path):
    """Every test gets an empty cache dir, and any real Gnani call fails loudly."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")

    def boom(*a, **k):
        raise AssertionError("unit tests must not call the Gnani API")

    monkeypatch.setattr(gnani_client, "_call_stt", boom)
    monkeypatch.setattr(gnani_client, "_call_tts", boom)
    monkeypatch.setattr(gnani_client, "_sleep", lambda s: None)
