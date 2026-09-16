"""Optional free visual review for transcript-selected short candidates.

The main selector remains transcript-driven because speech gives better timing and
context. This pass adds visual evidence per candidate and records a score/reason
for debugging and future ranking, using a small number of still frames.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Dict, List

import ai_gateway


def _candidate_frames(video_path: str, start: float, end: float, count: int = 4) -> List[bytes]:
    count = max(2, min(int(count), 6))
    duration = max(0.1, float(end) - float(start))
    with tempfile.TemporaryDirectory() as tmp:
        pattern = os.path.join(tmp, "frame_%02d.jpg")
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-ss", str(max(0.0, start)),
            "-i", video_path, "-t", str(duration),
            "-vf", f"fps={count}/{duration:.6f},scale=768:-2",
            "-frames:v", str(count), "-q:v", "6", pattern,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=180)
        return [
            open(os.path.join(tmp, f"frame_{i:02d}.jpg"), "rb").read()
            for i in range(1, count + 1)
            if os.path.exists(os.path.join(tmp, f"frame_{i:02d}.jpg"))
        ]


def review(video_path: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return candidate visual scores; never makes the workflow fail."""
    if not candidates or not ai_gateway.is_configured():
        return []
    images: List[bytes] = []
    groups = []
    for i, candidate in enumerate(candidates[:4]):
        try:
            frames = _candidate_frames(video_path, candidate["start"], candidate["end"])
        except Exception:
            frames = []
        groups.append({
            "candidate_id": i,
            "start": candidate.get("start"),
            "end": candidate.get("end"),
            "frame_count": len(frames),
        })
        images.extend(frames)
    if not images:
        return []

    prompt = f"""
You are a short-form video editor performing a visual quality check.
The images are grouped by candidate in order; each candidate's frames are consecutive.
Candidate groups: {json.dumps(groups)}
For each candidate, score visual_quality from 0 to 100. Consider subject visibility,
readability of important on-screen graphics, visual change, crop safety, and whether
the moment would hold attention without relying only on narration. Do not penalize
ordinary talking-head shots. Return strict JSON only:
{{"candidates":[{{"candidate_id":0,"visual_quality":0,"reason":"max 12 words"}}]}}
"""
    try:
        parsed, _ = ai_gateway.complete_json(
            system="Return strict JSON only. Be conservative and factual.",
            user=prompt, temperature=0.0, images=images, kind="vision",
            max_tokens=600,
        )
        rows = parsed.get("candidates", []) if isinstance(parsed, dict) else []
        return [r for r in rows if isinstance(r, dict)]
    except Exception as exc:
        print(f"   ⚠️ Visual scene review skipped ({exc}).")
        return []


def enabled() -> bool:
    return os.environ.get("AI_SCENE_REVIEW", "0").strip().lower() in ("1", "true", "yes")


__all__ = ["enabled", "review"]
