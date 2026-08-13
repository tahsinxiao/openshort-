"""github_actions.py — Run clipping jobs on GitHub Actions from the app.

The app's backend (Render) is too weak for long videos, but GitHub Actions
gives free 7 GB / 2-core machines on public repos. This module lets the app:

  1. dispatch a "Clip a video" workflow with the user's link,
  2. poll the run until it finishes,
  3. download the finished clips artifact and unpack it into the job dir.

So the user never leaves the app: paste link → backend dispatches → clips
appear in the normal results view.

Env vars:
  GITHUB_TOKEN        a classic PAT with `repo` + `workflow` scopes (free,
                      created at github.com/settings/tokens — no credit card)
  GITHUB_REPO         "owner/repo" e.g. "tahsinxiao/openshort-"
  GITHUB_WORKFLOW_FILE  workflow file name in .github/workflows
                      (default "clip-video.yml")
  GITHUB_WHISPER_MODEL  "tiny" (default) | "base" | "small"

The AI key is NOT passed through inputs (they're visible in run logs) — the
workflow reads it from the repo's Actions secrets instead.
"""

import json
import os
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import httpx

API = "https://api.github.com"


def is_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip()
                and os.environ.get("GITHUB_REPO", "").strip())


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _repo() -> str:
    return os.environ.get("GITHUB_REPO", "").strip().strip("/")


def _workflow_file() -> str:
    return os.environ.get("GITHUB_WORKFLOW_FILE", "clip-video.yml").strip()


def whisper_model() -> str:
    m = os.environ.get("GITHUB_WHISPER_MODEL", "tiny").strip().lower()
    return m if m in ("tiny", "base", "small") else "tiny"


def _headers(accept: Optional[str] = None) -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def dispatch(url: str, output_format: str = "vertical") -> Optional[int]:
    """Trigger the clip workflow. Returns the run id (or None on failure)."""
    repo = _repo()
    dispatch_url = f"{API}/repos/{repo}/actions/workflows/{_workflow_file()}/dispatches"
    body = {
        "ref": "main",
        "inputs": {
            "video_url": url,
            "whisper_model": whisper_model(),
            "output_format": output_format if output_format in ("vertical", "square", "horizontal") else "vertical",
        },
    }
    before = time.time()
    resp = httpx.post(dispatch_url, headers=_headers(), json=body, timeout=30.0)
    if resp.status_code not in (204, 201, 200):
        print(f"⚠️ [gh-actions] Dispatch failed ({resp.status_code}): {resp.text[:300]}")
        return None

    for _ in range(10):
        runs = _list_recent_runs(5)
        for run in runs:
            created = run.get("created_at") or ""
            try:
                created_ts = time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))
            except Exception:
                continue
            if created_ts >= before - 5 and run.get("event") == "workflow_dispatch":
                return run.get("id")
        time.sleep(3)
    return None


def _list_recent_runs(per_page: int = 5) -> List[dict]:
    repo = _repo()
    try:
        resp = httpx.get(
            f"{API}/repos/{repo}/actions/runs",
            params={"per_page": per_page, "event": "workflow_dispatch"},
            headers=_headers(), timeout=30.0)
        resp.raise_for_status()
        return resp.json().get("workflow_runs") or []
    except Exception as e:
        print(f"⚠️ [gh-actions] list runs failed: {e}")
        return []


def get_run(run_id: int) -> Optional[dict]:
    repo = _repo()
    try:
        resp = httpx.get(
            f"{API}/repos/{repo}/actions/runs/{run_id}",
            headers=_headers(), timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"⚠️ [gh-actions] get run failed: {e}")
        return None


def _artifacts(run_id: int) -> List[dict]:
    repo = _repo()
    try:
        resp = httpx.get(
            f"{API}/repos/{repo}/actions/runs/{run_id}/artifacts",
            params={"per_page": 20},
            headers=_headers(), timeout=30.0)
        resp.raise_for_status()
        return resp.json().get("artifacts") or []
    except Exception as e:
        print(f"⚠️ [gh-actions] list artifacts failed: {e}")
        return []


def download_artifacts(run_id: int, dest_dir: str) -> int:
    """Download the clips artifact of a run into dest_dir. Returns file count."""
    os.makedirs(dest_dir, exist_ok=True)
    total = 0
    for artifact in _artifacts(run_id):
        if not str(artifact.get("name") or "").startswith("clips-"):
            continue
        if artifact.get("expired"):
            print("⚠️ [gh-actions] clips artifact expired — re-run the workflow.")
            continue
        aid = artifact.get("id")
        repo = _repo()
        try:
            resp = httpx.get(
                f"{API}/repos/{repo}/actions/artifacts/{aid}/zip",
                headers=_headers(), timeout=600.0, follow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            print(f"⚠️ [gh-actions] artifact download failed: {e}")
            continue
        zip_path = os.path.join(dest_dir, "_artifact.zip")
        with open(zip_path, "wb") as f:
            f.write(resp.content)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
        except Exception as e:
            print(f"⚠️ [gh-actions] artifact extract failed: {e}")
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        total += _flatten_extracted(dest_dir)
    return total


def _flatten_extracted(dest_dir: str) -> int:
    """Move any .mp4 / .json found in subfolders up to dest_dir root."""
    moved = 0
    for root, _dirs, files in os.walk(dest_dir):
        if os.path.abspath(root) == os.path.abspath(dest_dir):
            continue
        for name in files:
            if not (name.endswith(".mp4") or name.endswith(".json")):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(dest_dir, name)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                try:
                    os.remove(src)
                except OSError:
                    pass
                continue
            try:
                os.replace(src, dst)
                moved += 1
            except OSError:
                pass
    return moved


def status_text(run: dict) -> str:
    status = run.get("status") or ""
    conclusion = run.get("conclusion") or ""
    if status == "completed":
        return f"completed ({conclusion})"
    return status
