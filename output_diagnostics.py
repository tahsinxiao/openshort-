"""Deterministic output-size and delivery-quality diagnostics."""
import json
import os
import subprocess


def probe_output(path):
    """Return delivery metrics for an encoded video, or a safe error record."""
    result = {
        "path": os.path.basename(path),
        "file_size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
    }
    try:
        raw = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_name,codec_type,width,height,bit_rate",
            "-of", "json", path,
        ], stderr=subprocess.DEVNULL, timeout=60)
        data = json.loads(raw.decode("utf-8"))
        fmt = data.get("format", {})
        result["duration_seconds"] = round(float(fmt.get("duration") or 0), 3)
        result["container_size_bytes"] = int(float(fmt.get("size") or result["file_size_bytes"]))
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        result.update({
            "video_codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "video_bitrate_kbps": round(float(video.get("bit_rate") or 0) / 1000, 1),
            "audio_codec": audio.get("codec_name"),
            "audio_bitrate_kbps": round(float(audio.get("bit_rate") or 0) / 1000, 1),
        })
        duration = result["duration_seconds"]
        result["overall_bitrate_kbps"] = round(result["container_size_bytes"] * 8 / max(duration, 0.001) / 1000, 1)
    except Exception as exc:
        result["probe_error"] = str(exc)
    return result


def write_output_diagnostics(path, output_dir=None):
    metrics = probe_output(path)
    output_dir = output_dir or os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    sidecar = os.path.join(output_dir, f"{stem}_quality.json")
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2)
    print(f"   Output: {metrics.get('duration_seconds', 0):.1f}s, {metrics.get('overall_bitrate_kbps', 0):.0f} kbps, {metrics.get('file_size_bytes', 0) / 1048576:.1f} MiB, {metrics.get('video_codec') or 'unknown'}")
    return metrics


__all__ = ["probe_output", "write_output_diagnostics"]
