import json

from output_diagnostics import probe_output


def test_probe_output_reports_size_and_bitrate(monkeypatch, tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * 1000)
    payload = {
        "format": {"duration": "10", "size": "1000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080,
             "height": 1920, "bit_rate": "700000"},
            {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000"},
        ],
    }
    monkeypatch.setattr(
        "subprocess.check_output",
        lambda *args, **kwargs: json.dumps(payload).encode("utf-8"),
    )
    result = probe_output(str(path))
    assert result["duration_seconds"] == 10.0
    assert result["video_bitrate_kbps"] == 700.0
    assert result["overall_bitrate_kbps"] == 0.8
    assert result["width"] == 1080
    assert result["height"] == 1920


def test_probe_output_is_fail_open(tmp_path):
    path = tmp_path / "missing.mp4"
    result = probe_output(str(path))
    assert result["file_size_bytes"] == 0
    assert "probe_error" in result
