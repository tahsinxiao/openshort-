"""Free deterministic editorial heuristics for transcript candidate selection."""
import re

_FILLER_RE = re.compile(r"\b(um|uh|like|you know|so anyway|basically|kind of|sort of|right)\b", re.IGNORECASE)
_WEAK_OPEN_RE = re.compile(r"^\s*(and|but|so|because|that|this|it|they|he|she|which|therefore)\b", re.IGNORECASE)
_SURPRISE_RE = re.compile(r"\b(never|always|most|only|first|last|secret|actually|surpris|danger|million|billion|percent|times|impossible|largest|smallest|deadly|why)\w*\b", re.IGNORECASE)


def _words(text):
    return re.findall(r"\b[\w'-]+\b", str(text or ""), flags=re.UNICODE)


def score_window(window):
    text = str(window.get("text") or "").strip()
    words = _words(text)
    if not words:
        return 0, {"reason": "empty", "word_count": 0}
    word_count = len(words)
    duration = max(0.1, float(window.get("end", 0) or 0) - float(window.get("start", 0) or 0))
    density = min(1.0, word_count / max(1.0, duration * 2.2))
    sentence_complete = 1.0 if re.search(r"[.!?]\s*$", text) else 0.35
    weak_open = 1.0 if _WEAK_OPEN_RE.search(text) else 0.0
    surprise = min(1.0, len(_SURPRISE_RE.findall(text)) / 3.0)
    filler_ratio = min(1.0, len(_FILLER_RE.findall(text)) / max(1, word_count / 8))
    question = 1.0 if "?" in text else 0.0
    score = 100 * (density * 0.25 + sentence_complete * 0.25 + surprise * 0.20 + question * 0.10 + (1.0 - weak_open) * 0.10 + (1.0 - filler_ratio) * 0.10)
    return round(max(0.0, min(100.0, score)), 2), {
        "reason": "deterministic editorial pre-score",
        "word_count": word_count,
        "speech_density": round(density, 3),
        "sentence_complete": bool(sentence_complete >= 1.0),
        "weak_open": bool(weak_open),
        "surprise_signal": round(surprise, 3),
        "filler_ratio": round(filler_ratio, 3),
    }


def rank_windows(windows):
    ranked = []
    for window in windows:
        item = dict(window)
        score, details = score_window(item)
        item["editorial_pre_score"] = score
        item["editorial_signals"] = details
        ranked.append(item)
    return sorted(ranked, key=lambda item: item["editorial_pre_score"], reverse=True)


def critic_check(start_text, end_text):
    opening = str(start_text or "").strip()
    ending = str(end_text or "").strip()
    issues = []
    if not opening:
        issues.append("empty opening")
    if _WEAK_OPEN_RE.search(opening):
        issues.append("opening begins with a weak context-dependent word")
    if ending and not re.search(r"[.!?]\s*$", ending):
        issues.append("ending may cut off before a complete sentence")
    return {"approved": not issues, "issues": issues}


def critic_clip(transcript_result, start, end):
    segments = []
    for segment in transcript_result.get("segments", []):
        if float(segment.get("end", 0) or 0) > float(start) and float(segment.get("start", 0) or 0) < float(end):
            text = str(segment.get("text") or "").strip()
            if text:
                segments.append(text)
    if not segments:
        return {"approved": False, "issues": ["no transcript context"]}
    return critic_check(segments[0], segments[-1])


__all__ = ["score_window", "rank_windows", "critic_check", "critic_clip"]
