"""telegram_uploader.py — Free cardless clip backup via a private Telegram channel.

Why: Render free wipes its local disk on restart, and setting up S3/R2 needs a
card (or a Cloudflare account). Telegram gives you free, no-card storage: you
create a private channel, add a bot to it, and this module uploads every
finished clip to that channel via the Bot API.

This is a BACKUP, not a cloud server/CDN:
  * Bot API file limit is 50 MB per file — clips (usually < 30 MB) fit fine;
    larger files are skipped with a warning (use S3/R2 for those).
  * Files are restored via the Telegram API by file_id — no public URLs.
  * Telegram's terms are for messaging; treat it as a convenience backup and
    never as a replacement for a real object store.

Env vars:
  TELEGRAM_BOT_TOKEN   bot token from @BotFather (free, no card)
  TELEGRAM_CHANNEL_ID  channel id (e.g. -1001234567890) or @publicusername
                       (the bot must be an admin/member with post permission)
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple

import httpx

API_BASE = "https://api.telegram.org/bot{token}"
MAX_FILE_BYTES = 50 * 1024 * 1024  # Bot API hard limit

INDEX_NAME = "telegram_index.json"


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                and os.environ.get("TELEGRAM_CHANNEL_ID", "").strip())


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _channel() -> str:
    return os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()


def _index_path() -> str:
    data_dir = os.environ.get("DATA_DIR", "").strip() or "output"
    return os.path.join(data_dir, INDEX_NAME)


def _load_index() -> Dict[str, dict]:
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(index: Dict[str, dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_index_path()), exist_ok=True)
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
    except Exception as e:
        print(f"⚠️ [telegram] Could not persist index: {e}")


def _send_document(file_path: str, caption: str,
                   timeout: float = 300.0) -> Tuple[Optional[int], Optional[str]]:
    """Upload one file to the channel. Returns (message_id, file_id)."""
    url = f"{API_BASE.format(token=_token())}/sendDocument"
    data = {"chat_id": _channel(), "caption": caption[:1024]}
    resp = None
    for attempt in range(1, 4):
        try:
            with open(file_path, "rb") as f:
                files = {"document": (os.path.basename(file_path), f, "application/octet-stream")}
                resp = httpx.post(url, data=data, files=files, timeout=timeout)
        except Exception as e:
            print(f"⚠️ [telegram] Upload failed for {os.path.basename(file_path)}: {e}")
            return None, None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            delay = 3 * attempt
            print(f"⚠️ [telegram] HTTP {resp.status_code}, retrying in {delay}s "
                  f"({attempt}/3)...")
            time.sleep(delay)
            continue
        break

    if resp is None or resp.status_code != 200:
        print(f"⚠️ [telegram] Upload error: {resp.status_code if resp else 'no response'} "
              f"{resp.text[:300] if resp else ''}")
        return None, None
    try:
        result = resp.json()["result"]
        doc = result.get("document") or {}
        return result.get("message_id"), doc.get("file_id")
    except (KeyError, ValueError) as e:
        print(f"⚠️ [telegram] Unexpected upload response: {e}")
        return None, None


def upload_file(file_path: str, key: str, caption: str = "") -> bool:
    """Upload one file under a stable key (e.g. '<job_id>/clip_1.mp4')."""
    if not is_configured():
        return False
    if not os.path.exists(file_path):
        return False
    size = os.path.getsize(file_path)
    if size > MAX_FILE_BYTES:
        print(f"⚠️ [telegram] {os.path.basename(file_path)} is {size / 1e6:.0f} MB "
              f"(> 50 MB bot limit) — skipped. Use S3/R2 for large files.")
        return False
    message_id, file_id = _send_document(file_path, caption or key)
    if message_id is None or file_id is None:
        return False
    index = _load_index()
    index[key] = {
        "message_id": message_id,
        "file_id": file_id,
        "chat_id": _channel(),
        "file_name": os.path.basename(file_path),
        "size": size,
        "uploaded_at": time.time(),
    }
    _save_index(index)
    print(f"📤 [telegram] Backed up {os.path.basename(file_path)} "
          f"({size / 1e6:.1f} MB) as {key}")
    return True


def upload_job_artifacts(directory: str, job_id: str) -> int:
    """Upload all clips + metadata of a job to the Telegram channel."""
    if not is_configured() or not os.path.exists(directory):
        return 0
    count = 0
    for filename in sorted(os.listdir(directory)):
        if (filename.endswith(".mp4") or filename.endswith(".json")) and not filename.startswith("temp_"):
            file_path = os.path.join(directory, filename)
            key = f"{job_id}/{filename}"
            if upload_file(file_path, key, caption=f"openshorts job {job_id[:8]} — {filename}"):
                count += 1
    return count


def list_backups() -> List[dict]:
    index = _load_index()
    out = []
    for key, meta in index.items():
        out.append({
            "key": key,
            "file_name": meta.get("file_name"),
            "size": meta.get("size"),
            "uploaded_at": meta.get("uploaded_at"),
        })
    return sorted(out, key=lambda x: str(x.get("uploaded_at", "")), reverse=True)


def download_file(key: str, dest_path: str) -> bool:
    """Fetch a backed-up file back from Telegram by its index entry."""
    index = _load_index()
    entry = index.get(key)
    if not entry:
        print(f"⚠️ [telegram] No index entry for {key}")
        return False
    file_id = entry.get("file_id")
    if not file_id:
        print(f"⚠️ [telegram] No file_id stored for {key}")
        return False
    token = _token()
    try:
        resp = httpx.get(
            f"{API_BASE.format(token=token)}/getFile",
            params={"file_id": file_id}, timeout=60.0)
        resp.raise_for_status()
        file_path = resp.json().get("result", {}).get("file_path")
        if not file_path:
            print(f"⚠️ [telegram] getFile returned no path for {key}")
            return False
        dl = httpx.get(f"https://api.telegram.org/file/bot{token}/{file_path}",
                       timeout=300.0)
        dl.raise_for_status()
    except Exception as e:
        print(f"⚠️ [telegram] Restore failed for {key}: {e}")
        return False
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(dl.content)
    print(f"📥 [telegram] Restored {key} → {dest_path}")
    return True
