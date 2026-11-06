import numpy as np
import pytest
import soundfile as sf
from scipy.signal import welch

from parchi import config
from parchi.eval import channel

SR = 16000


def tones(*freqs, seconds=1.0):
    t = np.arange(int(SR * seconds)) / SR
    return sum(0.2 * np.sin(2 * np.pi * f * t) for f in freqs).astype(np.float64)


def band_power(x, lo, hi):
    f, p = welch(x, SR, nperseg=2048)
    return p[(f >= lo) & (f <= hi)].sum()


def test_phone_band_limits_audio():
    x = tones(1000, 6000)  # 6 kHz cannot survive an 8 kHz phone line
    y = channel.phone(x)
    assert len(y) == len(x)
    assert band_power(y, 5500, 6500) < 1e-3 * band_power(y, 900, 1100)


def test_phone_removes_low_hum():
    y = channel.phone(tones(1000, 100))
    assert band_power(y, 80, 120) < 0.05 * band_power(y, 900, 1100)


def test_mulaw_round_trip_is_close_but_quantised():
    x = np.linspace(-0.9, 0.9, 1001)
    y = channel.mulaw(x)
    assert np.max(np.abs(y - x)) < 0.02 and not np.allclose(y, x)


@pytest.mark.parametrize("snr", [10.0, 5.0])
def test_add_noise_hits_target_snr(snr):
    x = tones(440)
    noise = np.random.default_rng(0).normal(size=SR // 3)  # shorter than x: must loop
    y = channel.add_noise(x, noise, snr, np.random.default_rng(1))
    measured = 20 * np.log10(channel.rms(x) / channel.rms(y - x))
    assert abs(measured - snr) < 0.1


def test_noise_conditions_skipped_without_noise_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    run, skipped = channel.available_conditions(list(channel.CONDITIONS))
    assert run == ["clean", "phone"] and skipped == ["phone+noise@10dB", "phone+noise@5dB"]
    (tmp_path / "noise").mkdir()
    sf.write(str(tmp_path / "noise" / "fan.wav"), np.zeros(SR, dtype=np.float32), SR)
    assert channel.available_conditions(list(channel.CONDITIONS))[0] == list(channel.CONDITIONS)


def test_degrade_writes_16k_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "noise").mkdir()
    sf.write(str(tmp_path / "noise" / "crowd.wav"), np.random.default_rng(0).normal(size=SR).astype(np.float32) * 0.1, SR)
    src = tmp_path / "clean.wav"
    sf.write(str(src), tones(500).astype(np.float32), SR, subtype="PCM_16")
    assert channel.degrade(src, "clean", "n1") == src
    out = channel.degrade(src, "phone+noise@5dB", "n1")
    info = sf.info(str(out))
    assert info.samplerate == SR and info.channels == 1
    first = out.read_bytes()
    out.unlink()
    assert channel.degrade(src, "phone+noise@5dB", "n1").read_bytes() == first
