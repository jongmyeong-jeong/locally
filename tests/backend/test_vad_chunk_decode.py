"""Tests for server._decode_chunk_pcm — MediaRecorder header-donor decode.

MediaRecorder.start(timeslice) emits a webm init segment only in the first
blob; later blobs are bare clusters that ffmpeg rejects standalone. The server
must decode `header_blob + chunk` and slice off the header's samples.

audio_io.load_pcm_16k_mono is patched — no ffmpeg involved.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app import server as server_mod


@pytest.fixture(autouse=True)
def _clean_headers():
    server_mod._VAD_HEADERS.clear()
    yield
    for header_path, _ in server_mod._VAD_HEADERS.values():
        Path(header_path).unlink(missing_ok=True)
    server_mod._VAD_HEADERS.clear()


def test_first_chunk_decodes_standalone_and_registers_header(tmp_path):
    chunk0 = tmp_path / "c0.webm"
    chunk0.write_bytes(b"HDR+AUDIO0")
    fake0 = np.zeros(16000, dtype=np.float32)

    with patch.object(
        server_mod.audio_io, "load_pcm_16k_mono", return_value=fake0
    ):
        pcm = server_mod._decode_chunk_pcm("sess-h1", 0, chunk0)

    assert pcm.size == 16000
    header_path, header_samples = server_mod._VAD_HEADERS["sess-h1"]
    assert header_samples == 16000
    assert Path(header_path).read_bytes() == b"HDR+AUDIO0"


def test_later_chunk_decoded_with_header_prefix_and_sliced(tmp_path):
    chunk0 = tmp_path / "c0.webm"
    chunk0.write_bytes(b"HDR+AUDIO0")
    chunk1 = tmp_path / "c1.webm"
    chunk1.write_bytes(b"CLUSTER1")

    fake0 = np.zeros(16000, dtype=np.float32)
    fake_full = np.concatenate(
        [np.zeros(16000, dtype=np.float32), np.ones(8000, dtype=np.float32)]
    )
    decoded_inputs: list[bytes] = []

    def fake_load(path):
        data = Path(path).read_bytes()
        decoded_inputs.append(data)
        return fake0 if data == b"HDR+AUDIO0" else fake_full

    with patch.object(
        server_mod.audio_io, "load_pcm_16k_mono", side_effect=fake_load
    ):
        server_mod._decode_chunk_pcm("sess-h2", 0, chunk0)
        pcm1 = server_mod._decode_chunk_pcm("sess-h2", 1, chunk1)

    # Second decode input must be header + cluster, byte-exact.
    assert decoded_inputs[-1] == b"HDR+AUDIO0CLUSTER1"
    # Returned PCM is only the new chunk's samples.
    assert pcm1.size == 8000
    assert bool(np.all(pcm1 == 1.0))
    # Combo temp file is cleaned up.
    assert not list(Path(server_mod.tempfile.gettempdir()).glob("sess-h2_combo_*"))


def test_cleanup_removes_header_state_and_file(tmp_path):
    chunk0 = tmp_path / "c0.webm"
    chunk0.write_bytes(b"HDR")

    with patch.object(
        server_mod.audio_io,
        "load_pcm_16k_mono",
        return_value=np.zeros(10, dtype=np.float32),
    ):
        server_mod._decode_chunk_pcm("sess-h3", 0, chunk0)

    header_path, _ = server_mod._VAD_HEADERS["sess-h3"]
    assert Path(header_path).exists()

    server_mod._cleanup_session_live_state("sess-h3")

    assert "sess-h3" not in server_mod._VAD_HEADERS
    assert not Path(header_path).exists()
