#!/usr/bin/env python3
"""Local knowledge, requirement, and case-maintenance support tools.

The tool keeps a downloaded copy of a configured GitHub Markdown repository and
stores requirements and curated cases as ordinary local JSON records.  It has
no model, company-system, authentication, or submission integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "knowledge-source.json"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
KNOWLEDGE_DIRNAME = "knowledge"
REQUIREMENTS_DIRNAME = "requirements"
CURATION_DIRNAME = "curation"
EVENTS_FILENAME = "events.jsonl"
MANIFEST_FILENAME = "manifest.json"
HTTP_TIMEOUT = 20.0

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
REPO_RE = re.compile(r"^[^/\\\s]+/[^/\\\s]+$")
CURATION_STATUSES = {"pending_review", "approved", "rejected", "withdrawn"}
REQUIRED_REQUIREMENT_FIELDS = ("request_id", "summary", "scenario", "desired_outcome")
REQUIRED_CURATION_FIELDS = (
    "candidate_id",
    "title",
    "symptom",
    "environment",
    "resolution",
    "resolution_evidence",
    "source_refs",
    "resolved",
)


class SupportError(Exception):
    """An expected user-facing error with a stable JSON error code."""

    def __init__(self, code: str, message: str, details: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line errors in the same JSON protocol as tool errors."""

    def error(self, message: str) -> None:  # pragma: no cover - parser edge
        raise SupportError("INVALID_ARGUMENT", message)


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _success(command: str, **values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "command": command}
    result.update(values)
    return result


def _failure(error: SupportError) -> dict[str, Any]:
    item: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.details is not None:
        item["details"] = error.details
    return {"ok": False, "error": item}


def _read_json(path: Path, missing_code: str, description: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SupportError(missing_code, f"{description}不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SupportError("INVALID_JSON", f"{description}不是有效 JSON: {path}") from exc
    except UnicodeDecodeError as exc:
        raise SupportError("INVALID_JSON", f"{description}不是 UTF-8 JSON: {path}") from exc
    except OSError as exc:
        raise SupportError("STATE_READ_FAILED", f"读取{description}失败: {path}") from exc


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise SupportError("STATE_WRITE_FAILED", f"写入本地状态失败: {path}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise SupportError("STATE_WRITE_FAILED", f"写入本地文件失败: {path}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _data_dir(raw: str | None) -> Path:
    value = Path(raw).expanduser() if raw is not None else DEFAULT_DATA_DIR
    if not value.is_absolute():
        value = REPO_ROOT / value
    value = value.resolve()
    try:
        value.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SupportError("DATA_UNAVAILABLE", f"无法创建数据目录: {value}") from exc
    if not value.is_dir():
        raise SupportError("DATA_UNAVAILABLE", f"数据路径不是目录: {value}")
    return value


def _safe_id(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not SAFE_ID_RE.fullmatch(raw):
        raise SupportError("INVALID_INPUT", f"{field} 只能使用 1-80 位安全文件名字符")
    return raw


def _safe_relative_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise SupportError("INVALID_PATH", "path 必须是非空相对路径")
    if raw.startswith(("/", "\\")) or "\\" in raw or "//" in raw:
        raise SupportError("INVALID_PATH", "path 必须是安全的相对路径")
    candidate = Path(raw)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise SupportError("INVALID_PATH", "path 必须是安全的相对路径")
    return candidate


def _safe_repo_path(raw: Any) -> str:
    candidate = _safe_relative_path(raw)
    value = candidate.as_posix()
    if not value.lower().endswith(".md"):
        raise SupportError("SOURCE_INVALID", f"知识源包含非 Markdown 路径: {value}")
    return value


def _required_text(payload: dict[str, Any], field: str, kind: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SupportError("INVALID_INPUT", f"{kind}字段 {field} 必须是非空文本")
    return value.strip()


def _normalize_source_refs(value: Any, *, required: bool) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise SupportError("INVALID_INPUT", "source_refs 必须是字符串数组" + ("且不能为空" if required else ""))
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise SupportError("INVALID_INPUT", "source_refs 必须只包含非空文本")
    return [item.strip() for item in value]


def _normalize_requirement(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SupportError("INVALID_INPUT", "需求输入 JSON 顶层必须是对象")
    result: dict[str, Any] = {
        "request_id": _safe_id(payload.get("request_id"), "request_id"),
        "summary": _required_text(payload, "summary", "需求"),
        "scenario": _required_text(payload, "scenario", "需求"),
        "desired_outcome": _required_text(payload, "desired_outcome", "需求"),
    }
    for field in ("impact", "workaround"):
        if field in payload:
            result[field] = _required_text(payload, field, "需求")
    if "source_refs" in payload:
        result["source_refs"] = _normalize_source_refs(payload["source_refs"], required=False)
    return result


def _normalize_curation(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SupportError("INVALID_INPUT", "候选案例 JSON 顶层必须是对象")
    result: dict[str, Any] = {"candidate_id": _safe_id(payload.get("candidate_id"), "candidate_id")}
    for field in ("title", "symptom", "environment", "resolution", "resolution_evidence"):
        result[field] = _required_text(payload, field, "候选案例")
    result["source_refs"] = _normalize_source_refs(payload.get("source_refs"), required=True)
    if payload.get("resolved") is not True:
        raise SupportError("UNRESOLVED_CANDIDATE", "候选案例只有 resolved=true 才能保存")
    result["resolved"] = True
    return result


def _load_root_config() -> dict[str, str]:
    raw = _read_json(CONFIG_PATH, "CONFIG_NOT_FOUND", "知识源配置")
    if not isinstance(raw, dict):
        raise SupportError("CONFIG_INVALID", "知识源配置顶层必须是对象")
    repo = raw.get("repo")
    ref = raw.get("ref")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise SupportError("CONFIG_INVALID", "知识源配置 repo 必须是 owner/name")
    if not isinstance(ref, str) or not ref.strip() or any(char.isspace() for char in ref):
        raise SupportError("CONFIG_INVALID", "知识源配置 ref 必须是非空引用名")
    return {"repo": repo, "ref": ref}


def _request(url: str, *, accept: str | None = None) -> bytes:
    headers = {"User-Agent": "knowledge-support/1.0"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SupportError("SOURCE_UNAVAILABLE", f"无法读取知识源: {url}") from exc
    if not isinstance(body, bytes):
        body = bytes(body)
    return body


def _request_json(url: str) -> Any:
    body = _request(url, accept="application/vnd.github+json")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupportError("SOURCE_INVALID", f"知识源 API 返回无效 JSON: {url}") from exc


def _github_urls(repo: str, ref: str) -> tuple[str, str]:
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in repo.split("/"))
    encoded_ref = urllib.parse.quote(ref, safe="")
    base = f"https://api.github.com/repos/{encoded_repo}"
    return f"{base}/commits/{encoded_ref}", f"{base}/git/trees/{{commit}}?recursive=1"


def _raw_url(repo: str, commit: str, path: str) -> str:
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in repo.split("/"))
    encoded_commit = urllib.parse.quote(commit, safe="")
    encoded_path = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{encoded_repo}/{encoded_commit}/{encoded_path}"


def _title_for(path: str, body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return Path(path).stem
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if title:
                return title
    return Path(path).stem


def _git_blob_sha(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode("ascii") + body).hexdigest()


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _entry_type(path: str) -> str:
    if path == "README.md":
        return "index"
    if path.startswith("cases/"):
        return "case"
    return "product"


def _sync(data: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _load_root_config()
    repo, ref = config["repo"], config["ref"]
    commit_url, tree_url_template = _github_urls(repo, ref)
    commit_raw = _request_json(commit_url)
    if not isinstance(commit_raw, dict) or not isinstance(commit_raw.get("sha"), str) or not commit_raw["sha"].strip():
        raise SupportError("SOURCE_INVALID", f"无法从 GitHub 响应解析实际 commit: {commit_url}")
    commit = commit_raw["sha"].strip()
    tree_raw = _request_json(tree_url_template.format(commit=urllib.parse.quote(commit, safe="")))
    if not isinstance(tree_raw, dict):
        raise SupportError("SOURCE_INVALID", "GitHub tree 响应顶层必须是对象")
    if tree_raw.get("truncated") is True:
        raise SupportError("SOURCE_TREE_TRUNCATED", "GitHub tree 响应被截断，未同步不完整知识")
    tree = tree_raw.get("tree")
    if not isinstance(tree, list):
        raise SupportError("SOURCE_INVALID", "GitHub tree 响应缺少 tree 数组")

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.lower().endswith(".md"):
            continue
        path = _safe_repo_path(raw_path)
        if path in seen:
            raise SupportError("SOURCE_INVALID", f"GitHub tree 包含重复路径: {path}")
        seen.add(path)
        entries.append({"path": path, "tree_sha": item.get("sha")})
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise SupportError("SOURCE_INVALID", "知识源没有 Markdown 文件")

    stage: Path | None = None
    source_entries: list[dict[str, Any]] = []
    try:
        stage = Path(tempfile.mkdtemp(prefix=".knowledge-sync-", dir=str(data)))
        for item in entries:
            path = item["path"]
            source_url = _raw_url(repo, commit, path)
            body = _request(source_url, accept="text/plain")
            try:
                body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SupportError("KNOWLEDGE_FILE_INVALID", f"知识文件不是 UTF-8: {path}") from exc
            tree_sha = item.get("tree_sha")
            if isinstance(tree_sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", tree_sha):
                actual_blob = _git_blob_sha(body)
                if actual_blob.lower() != tree_sha.lower():
                    raise SupportError(
                        "SOURCE_HASH_MISMATCH",
                        f"知识文件 Git blob 校验失败: {path}",
                        {"path": path, "expected": tree_sha, "actual": actual_blob},
                    )
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            source_entries.append(
                {
                    "path": path,
                    "title": _title_for(path, body),
                    "type": _entry_type(path),
                    "source_url": source_url,
                    "commit": commit,
                    "tree_sha": tree_sha,
                    "sha256": _sha256(body),
                    "searchable": path != "README.md",
                }
            )
        manifest = {
            "repo": repo,
            "ref": ref,
            "commit": commit,
            "entries": source_entries,
        }
        _write_json(stage / MANIFEST_FILENAME, manifest)
        knowledge = data / KNOWLEDGE_DIRNAME
        backup: Path | None = None
        if knowledge.exists() or knowledge.is_symlink():
            backup = data / f".{KNOWLEDGE_DIRNAME}.backup-{os.getpid()}"
            if backup.exists() or backup.is_symlink():
                _remove_path(backup)
            os.replace(knowledge, backup)
        try:
            os.replace(stage, knowledge)
            stage = None
        except OSError as exc:
            if backup is not None and (backup.exists() or backup.is_symlink()) and not knowledge.exists():
                os.replace(backup, knowledge)
            raise SupportError("STATE_WRITE_FAILED", "无法提交完整知识缓存") from exc
        if backup is not None and (backup.exists() or backup.is_symlink()):
            _remove_path(backup)
    except SupportError:
        raise
    except OSError as exc:
        raise SupportError("STATE_WRITE_FAILED", "同步知识缓存失败") from exc
    finally:
        if stage is not None and (stage.exists() or stage.is_symlink()):
            _remove_path(stage)

    source = {"repo": repo, "ref": ref, "commit": commit}
    result = _success(
        "sync",
        source=source,
        files=[item["path"] for item in source_entries],
        knowledge="knowledge",
    )
    return result, {"source": source, "body_read": True}


def _load_manifest(data: Path) -> dict[str, Any]:
    knowledge = data / KNOWLEDGE_DIRNAME
    if not knowledge.is_dir():
        raise SupportError("NOT_SYNCED", "本地知识缓存不存在，请先运行 sync")
    try:
        raw = _read_json(knowledge / MANIFEST_FILENAME, "CACHE_CORRUPT", "知识缓存 manifest")
    except SupportError as exc:
        if exc.code == "INVALID_JSON":
            raise SupportError("CACHE_CORRUPT", f"知识缓存 manifest 不是有效 JSON: {knowledge / MANIFEST_FILENAME}") from exc
        raise
    if not isinstance(raw, dict):
        raise SupportError("CACHE_CORRUPT", "知识缓存 manifest 顶层必须是对象")
    if not all(isinstance(raw.get(field), str) and raw[field] for field in ("repo", "ref", "commit")):
        raise SupportError("CACHE_CORRUPT", "知识缓存 manifest 来源信息无效")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise SupportError("CACHE_CORRUPT", "知识缓存 manifest 缺少 entries")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise SupportError("CACHE_CORRUPT", "知识缓存 manifest 条目无效")
        path = entry.get("path")
        try:
            safe_path = _safe_repo_path(path)
        except SupportError as exc:
            raise SupportError("CACHE_CORRUPT", f"知识缓存 manifest 路径无效: {path!r}") from exc
        if safe_path in seen or not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise SupportError("CACHE_CORRUPT", f"知识缓存 manifest 条目无效: {safe_path}")
        if entry.get("type") not in {"index", "product", "case"}:
            raise SupportError("CACHE_CORRUPT", f"知识缓存条目类型无效: {safe_path}")
        if not isinstance(entry.get("source_url"), str) or not entry["source_url"]:
            raise SupportError("CACHE_CORRUPT", f"知识缓存来源 URL 无效: {safe_path}")
        expected_url = _raw_url(raw["repo"], raw["commit"], safe_path)
        if entry["source_url"] != expected_url or entry.get("commit") != raw["commit"]:
            raise SupportError("CACHE_CORRUPT", f"知识缓存来源版本不一致: {safe_path}")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise SupportError("CACHE_CORRUPT", f"知识缓存 sha256 无效: {safe_path}")
        normalized.append(dict(entry, path=safe_path))
        seen.add(safe_path)
    result = dict(raw)
    result["entries"] = normalized
    return result


def _source_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {field: manifest[field] for field in ("repo", "ref", "commit")}


def _read_cached_body(data: Path, path: str, expected_sha256: str | None = None) -> str:
    knowledge_root = (data / KNOWLEDGE_DIRNAME).resolve()
    target = data / KNOWLEDGE_DIRNAME / path
    try:
        resolved = target.resolve()
        resolved.relative_to(knowledge_root)
        raw_body = target.read_bytes()
        body = raw_body.decode("utf-8")
    except FileNotFoundError as exc:
        raise SupportError("KNOWLEDGE_FILE_MISSING", f"知识文件缺失: {path}") from exc
    except UnicodeDecodeError as exc:
        raise SupportError("KNOWLEDGE_FILE_INVALID", f"知识文件不是 UTF-8: {path}") from exc
    except (OSError, ValueError) as exc:
        raise SupportError("KNOWLEDGE_FILE_INVALID", f"无法读取知识文件: {path}") from exc
    if expected_sha256 is not None and _sha256(raw_body) != expected_sha256:
        raise SupportError("CACHE_CORRUPT", f"知识文件 hash 与 manifest 不一致: {path}")
    return body


def _search_terms(query: str) -> list[str]:
    return [term for term in re.split(r"\s+", query.strip().casefold()) if term]


def _matches(query: str, text: str) -> bool:
    terms = _search_terms(query)
    if not terms:
        return False
    haystack = text.casefold()
    return all(term in haystack for term in terms)


def _excerpt(body: str, query: str, limit: int = 180) -> str:
    plain = " ".join(line.strip().lstrip("#*- ").strip() for line in body.splitlines() if line.strip())
    terms = _search_terms(query)
    lowered = plain.casefold()
    position = min((lowered.find(term) for term in terms if lowered.find(term) >= 0), default=0)
    start = max(0, position - 50)
    excerpt = plain[start : start + limit].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if start + limit < len(plain):
        excerpt += "…"
    return excerpt


def _knowledge_entries(data: Path, manifest: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for entry in manifest["entries"]:
        if entry["type"] == "index" or not entry.get("searchable", True):
            continue
        body = _read_cached_body(data, entry["path"], entry.get("sha256"))
        yield dict(entry, body=body)


def _validate_curation_record(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SupportError("RECORD_CORRUPT", f"候选记录顶层必须是对象: {label}")
    status = raw.get("status")
    if status not in CURATION_STATUSES:
        raise SupportError("RECORD_CORRUPT", f"候选记录状态无效: {label}")
    try:
        normalized = _normalize_curation(raw)
    except SupportError as exc:
        raise SupportError("RECORD_CORRUPT", f"候选记录字段无效: {label}") from exc
    if raw.get("candidate_id") != normalized["candidate_id"]:
        raise SupportError("RECORD_CORRUPT", f"候选记录 ID 无效: {label}")
    if "id" in raw and raw["id"] != normalized["candidate_id"]:
        raise SupportError("RECORD_CORRUPT", f"候选记录 id 无效: {label}")
    if "type" in raw and raw["type"] != "case":
        raise SupportError("RECORD_CORRUPT", f"候选记录类型无效: {label}")
    for field in ("review_note", "withdrawal_note"):
        value = raw.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise SupportError("RECORD_CORRUPT", f"候选记录 {field} 无效: {label}")
    if status == "withdrawn" and not isinstance(raw.get("withdrawal_note"), str):
        raise SupportError("RECORD_CORRUPT", f"已撤回候选记录缺少 withdrawal_note: {label}")
    return raw


def _validate_requirement_record(raw: Any, identifier: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SupportError("RECORD_CORRUPT", f"需求记录顶层必须是对象: {identifier}")
    try:
        normalized = _normalize_requirement(raw)
    except SupportError as exc:
        raise SupportError("RECORD_CORRUPT", f"需求记录字段无效: {identifier}") from exc
    if normalized["request_id"] != identifier or raw.get("id") != identifier:
        raise SupportError("RECORD_CORRUPT", f"需求记录 ID 无效: {identifier}")
    if raw.get("status") != "saved" or raw.get("storage") != "local":
        raise SupportError("RECORD_CORRUPT", f"需求记录状态或存储标记无效: {identifier}")
    return raw


def _record_path(data: Path, kind: str, identifier: str) -> Path:
    return data / kind / f"{identifier}.json"


def _load_record(data: Path, kind: str, identifier: str, missing_code: str) -> dict[str, Any]:
    path = _record_path(data, kind, identifier)
    try:
        return _read_json(path, missing_code, f"{kind}记录")
    except SupportError as exc:
        if exc.code == "INVALID_JSON":
            raise SupportError("RECORD_CORRUPT", f"{kind}记录不是有效 JSON: {path}") from exc
        raise


def _record_payload(raw: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: raw[field] for field in fields if field in raw}


def _curation_records(data: Path) -> list[dict[str, Any]]:
    directory = data / CURATION_DIRNAME
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise SupportError("RECORD_CORRUPT", "候选记录目录不是目录")
    results: list[dict[str, Any]] = []
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        raise SupportError("STATE_READ_FAILED", "无法列出候选记录") from exc
    for path in paths:
        raw = _load_record(data, CURATION_DIRNAME, path.stem, "RECORD_CORRUPT")
        results.append(_validate_curation_record(raw, path.name))
    return results


def _curated_entry(data: Path, record: dict[str, Any]) -> dict[str, Any]:
    identifier = record["candidate_id"]
    body = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
    return {
        "path": f"curated/{identifier}.json",
        "title": record["title"],
        "type": "case",
        "body": body,
        "record": record,
        "source_refs": record["source_refs"],
        "source_path": str(_record_path(data, CURATION_DIRNAME, identifier)),
    }


def _search(data: Path, query: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(query, str) or not query.strip():
        raise SupportError("INVALID_QUERY", "query 必须是非空文本")
    manifest = _load_manifest(data)
    results: list[dict[str, Any]] = []
    for entry in _knowledge_entries(data, manifest):
        if _matches(query, f"{entry['path']} {entry['title']} {entry['body']}"):
            results.append(
                {
                    "path": entry["path"],
                    "title": entry["title"],
                    "type": entry["type"],
                    "excerpt": _excerpt(entry["body"], query),
                    "source_url": entry["source_url"],
                    "source_path": entry["path"],
                    "commit": entry["commit"],
                    "sha256": entry["sha256"],
                    "body_read": False,
                }
            )
    for record in _curation_records(data):
        if record["status"] != "approved":
            continue
        entry = _curated_entry(data, record)
        if _matches(query, f"{entry['path']} {entry['title']} {entry['body']}"):
            results.append(
                {
                    "path": entry["path"],
                    "title": entry["title"],
                    "type": "case",
                    "excerpt": _excerpt(entry["body"], query),
                    "source_refs": record["source_refs"],
                    "source_path": str(_record_path(data, CURATION_DIRNAME, record["candidate_id"])),
                    "body_read": False,
                }
            )
    result = _success("search", query=query, results=results, total=len(results), body_read=False)
    return result, {"source": _source_from_manifest(manifest), "body_read": False, "query": query}


def _read(data: Path, raw_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    relative = _safe_relative_path(raw_path)
    if relative.parts and relative.parts[0] == "curated":
        parts = relative.parts
        if len(parts) != 2 or not parts[1].endswith(".json"):
            raise SupportError("PATH_NOT_FOUND", f"知识路径不存在: {relative.as_posix()}")
        identifier = _safe_id(parts[1][:-5], "candidate_id")
        record = _validate_curation_record(
            _load_record(data, CURATION_DIRNAME, identifier, "RECORD_NOT_FOUND"), f"{identifier}.json"
        )
        if record["status"] != "approved":
            raise SupportError("CURATED_NOT_APPROVED", f"候选案例当前状态为 {record['status']}，不能作为知识正文读取")
        body = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        result = _success(
            "read",
            path=relative.as_posix(),
            title=record["title"],
            body=body,
            source_path=str(_record_path(data, CURATION_DIRNAME, identifier)),
            source_refs=record["source_refs"],
            type="case",
            status=record["status"],
            body_read=True,
        )
        return result, {
            "source": {"storage": "local", "path": str(_record_path(data, CURATION_DIRNAME, identifier))},
            "body_read": True,
            "path": relative.as_posix(),
            "identifier": identifier,
        }

    manifest = _load_manifest(data)
    path = relative.as_posix()
    entry = next((item for item in manifest["entries"] if item["path"] == path), None)
    if entry is None:
        raise SupportError("PATH_NOT_FOUND", f"知识路径不存在: {path}")
    body = _read_cached_body(data, path, entry.get("sha256"))
    result = _success(
        "read",
        path=path,
        title=entry["title"],
        body=body,
        source_url=entry["source_url"],
        source_path=path,
        repo=manifest["repo"],
        ref=manifest["ref"],
        commit=manifest["commit"],
        sha256=entry["sha256"],
        type=entry["type"],
        body_read=True,
    )
    return result, {"source": _source_from_manifest(manifest), "body_read": True, "path": path}


def _requirement_save(data: Path, input_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _normalize_requirement(_read_json(Path(input_path).expanduser(), "INPUT_NOT_FOUND", "需求输入文件"))
    identifier = payload["request_id"]
    path = _record_path(data, REQUIREMENTS_DIRNAME, identifier)
    if path.exists():
        existing = _validate_requirement_record(_load_record(data, REQUIREMENTS_DIRNAME, identifier, "RECORD_NOT_FOUND"), identifier)
        if _normalize_requirement(existing) != payload:
            raise SupportError("REQUEST_CONFLICT", f"request_id 已存在且内容不同: {identifier}")
        record = existing
        idempotent, written = True, False
    else:
        record = {**payload, "id": identifier, "status": "saved", "storage": "local"}
        _write_json(path, record)
        idempotent, written = False, True
    result = _success(
        "requirement-save",
        id=identifier,
        request_id=identifier,
        status="saved",
        storage="local",
        idempotent=idempotent,
        written=written,
        record=record,
    )
    return result, {
        "source": {"storage": "local", "path": str(path)},
        "body_read": False,
        "identifier": identifier,
        "idempotent": idempotent,
        "written": written,
    }


def _requirement_read(data: Path, raw_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identifier = _safe_id(raw_id, "request_id")
    record = _validate_requirement_record(
        _load_record(data, REQUIREMENTS_DIRNAME, identifier, "RECORD_NOT_FOUND"), identifier
    )
    result = _success("requirement-read", id=identifier, request_id=identifier, record=record)
    result.update({field: record[field] for field in REQUIRED_REQUIREMENT_FIELDS if field in record})
    result.update({"status": record["status"], "storage": record["storage"]})
    return result, {
        "source": {"storage": "local", "path": str(_record_path(data, REQUIREMENTS_DIRNAME, identifier))},
        "body_read": False,
        "identifier": identifier,
    }


def _curation_save(data: Path, input_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _normalize_curation(_read_json(Path(input_path).expanduser(), "INPUT_NOT_FOUND", "候选案例输入文件"))
    identifier = payload["candidate_id"]
    path = _record_path(data, CURATION_DIRNAME, identifier)
    if path.exists():
        existing = _validate_curation_record(_load_record(data, CURATION_DIRNAME, identifier, "RECORD_NOT_FOUND"), f"{identifier}.json")
        if _record_payload(existing, payload.keys()) != payload:
            raise SupportError("CURATION_CONFLICT", f"candidate_id 已存在且内容不同: {identifier}")
        record = existing
        idempotent, written = True, False
    else:
        record = {**payload, "id": identifier, "status": "pending_review", "type": "case", "review_note": None}
        _write_json(path, record)
        idempotent, written = False, True
    result = _success(
        "curation-save",
        id=identifier,
        candidate_id=identifier,
        status=record["status"],
        idempotent=idempotent,
        written=written,
        record=record,
    )
    return result, {
        "source": {"storage": "local", "path": str(path)},
        "body_read": False,
        "identifier": identifier,
        "idempotent": idempotent,
        "written": written,
    }


def _curation_read(data: Path, raw_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identifier = _safe_id(raw_id, "candidate_id")
    path = _record_path(data, CURATION_DIRNAME, identifier)
    record = _validate_curation_record(_load_record(data, CURATION_DIRNAME, identifier, "RECORD_NOT_FOUND"), f"{identifier}.json")
    return _success("curation-read", id=identifier, status=record["status"], record=record), {
        "source": {"storage": "local", "path": str(path)},
        "body_read": False,
        "identifier": identifier,
    }


def _curation_review(data: Path, raw_id: str, decision: str, note: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identifier = _safe_id(raw_id, "candidate_id")
    if decision not in {"approve", "reject"}:
        raise SupportError("INVALID_DECISION", "decision 只能是 approve 或 reject")
    if not isinstance(note, str) or not note.strip():
        raise SupportError("INVALID_INPUT", "审核 note 必须是非空文本")
    path = _record_path(data, CURATION_DIRNAME, identifier)
    record = _validate_curation_record(_load_record(data, CURATION_DIRNAME, identifier, "RECORD_NOT_FOUND"), f"{identifier}.json")
    target = "approved" if decision == "approve" else "rejected"
    current = record["status"]
    if current in {"approved", "rejected"}:
        if current != target:
            raise SupportError("CURATION_TERMINAL", f"候选记录已是终态 {current}，不能改为 {target}")
        result = _success("curation-review", id=identifier, decision=decision, status=current, note=record.get("review_note"), record=record, idempotent=True, written=False)
        return result, {"source": {"storage": "local", "path": str(path)}, "body_read": False, "identifier": identifier, "idempotent": True, "written": False}
    if current != "pending_review":
        raise SupportError("CURATION_STATE_INVALID", f"候选记录状态不可审核: {current}")
    updated = dict(record)
    updated["status"] = target
    updated["review_note"] = note.strip()
    _write_json(path, updated)
    result = _success("curation-review", id=identifier, decision=decision, status=target, note=updated["review_note"], record=updated, idempotent=False, written=True)
    return result, {"source": {"storage": "local", "path": str(path)}, "body_read": False, "identifier": identifier, "idempotent": False, "written": True}


def _curation_list(data: Path, query: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if query is not None and not isinstance(query, str):
        raise SupportError("INVALID_QUERY", "query 必须是文本")
    query_text = query.strip() if query else ""
    results: list[dict[str, Any]] = []
    for record in _curation_records(data):
        if query_text and not _matches(query_text, json.dumps(record, ensure_ascii=False, sort_keys=True)):
            continue
        summary = "；".join((record["title"], record["symptom"], record["environment"], record["resolution"]))
        results.append(
            {
                "id": record["candidate_id"],
                "status": record["status"],
                "title": record["title"],
                "summary": _excerpt(summary, query_text or record["title"], 240),
                "source_refs": record["source_refs"],
                "body_read": False,
            }
        )
    return _success("curation-list", query=query_text or None, results=results, total=len(results), body_read=False), {
        "source": {"storage": "local", "path": str(data / CURATION_DIRNAME)},
        "body_read": False,
        "query": query_text or None,
    }


def _curation_withdraw(data: Path, raw_id: str, note: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identifier = _safe_id(raw_id, "candidate_id")
    if not isinstance(note, str) or not note.strip():
        raise SupportError("INVALID_INPUT", "withdraw note 必须是非空文本")
    path = _record_path(data, CURATION_DIRNAME, identifier)
    record = _validate_curation_record(_load_record(data, CURATION_DIRNAME, identifier, "RECORD_NOT_FOUND"), f"{identifier}.json")
    current = record["status"]
    if current == "withdrawn":
        return _success("curation-withdraw", id=identifier, status=current, withdrawal_note=record["withdrawal_note"], record=record, idempotent=True, written=False), {
            "source": {"storage": "local", "path": str(path)}, "body_read": False, "identifier": identifier, "idempotent": True, "written": False
        }
    if current != "approved":
        raise SupportError("CURATION_WITHDRAW_INVALID_STATE", f"只有 approved 候选可以撤回，当前状态为 {current}: {identifier}")
    updated = dict(record)
    updated["status"] = "withdrawn"
    updated["withdrawal_note"] = note.strip()
    _write_json(path, updated)
    return _success("curation-withdraw", id=identifier, status="withdrawn", withdrawal_note=updated["withdrawal_note"], record=updated, idempotent=False, written=True), {
        "source": {"storage": "local", "path": str(path)}, "body_read": False, "identifier": identifier, "idempotent": False, "written": True
    }


def _capabilities(data: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _success(
        "capabilities",
        capabilities=["knowledge-sync", "knowledge-search", "knowledge-read", "requirement-local-save", "curation-local-review"],
        network="GitHub source sync only",
        storage="local filesystem",
    )
    return result, {"source": {"storage": "local"}, "body_read": False}


def _append_event(data: Path, *, command: str, success: bool, event: dict[str, Any] | None = None, error: SupportError | None = None) -> None:
    event = event or {}
    item: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "success": success,
        "query": event.get("query"),
        "path": event.get("path"),
        "id": event.get("identifier"),
        "body_read": bool(event.get("body_read", False)),
        "source": event.get("source", {"storage": "local"}),
    }
    for field in ("idempotent", "written"):
        if field in event:
            item[field] = event[field]
    if error is not None:
        item["error"] = {"code": error.code, "message": error.message}
    try:
        with (data / EVENTS_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write(_json_value(item) + "\n")
    except OSError as exc:
        print(f"warning: 无法写入事件记录 {data / EVENTS_FILENAME}: {exc}", file=sys.stderr)


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--data-dir", dest="data_dir")
    parser.add_argument("command", nargs="?")
    parser.add_argument("--query")
    parser.add_argument("--path")
    parser.add_argument("--input")
    parser.add_argument("--id")
    parser.add_argument("--decision")
    parser.add_argument("--note")
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def _dispatch(args: argparse.Namespace, data: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    command = args.command
    if command == "sync":
        return _sync(data)
    if command == "capabilities":
        return _capabilities(data)
    if command == "search":
        return _search(data, args.query)
    if command == "read":
        if not args.path:
            raise SupportError("INVALID_ARGUMENT", "read 需要 --path RELATIVE")
        return _read(data, args.path)
    if command == "requirement-save":
        if not args.input:
            raise SupportError("INVALID_ARGUMENT", "requirement-save 需要 --input FILE")
        return _requirement_save(data, args.input)
    if command == "requirement-read":
        if not args.id:
            raise SupportError("INVALID_ARGUMENT", "requirement-read 需要 --id ID")
        return _requirement_read(data, args.id)
    if command == "curation-save":
        if not args.input:
            raise SupportError("INVALID_ARGUMENT", "curation-save 需要 --input FILE")
        return _curation_save(data, args.input)
    if command == "curation-read":
        if not args.id:
            raise SupportError("INVALID_ARGUMENT", "curation-read 需要 --id ID")
        return _curation_read(data, args.id)
    if command == "curation-list":
        return _curation_list(data, args.query)
    if command == "curation-review":
        if not args.id or not args.decision or args.note is None:
            raise SupportError("INVALID_ARGUMENT", "curation-review 需要 --id ID --decision approve|reject --note TEXT")
        return _curation_review(data, args.id, args.decision, args.note)
    if command == "curation-withdraw":
        if not args.id or args.note is None:
            raise SupportError("INVALID_ARGUMENT", "curation-withdraw 需要 --id ID --note TEXT")
        return _curation_withdraw(data, args.id, args.note)
    if command is None:
        raise SupportError("INVALID_ARGUMENT", "缺少 command")
    raise SupportError("UNKNOWN_COMMAND", f"不支持的 command: {command}")


def _usage() -> str:
    return "python3 tools/support.py [--data-dir DIR] <command>"


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    data: Path | None = None
    command = ""
    try:
        args = _build_parser().parse_args(argv)
        command = args.command or ""
        if args.help:
            print(_json_value(_success("help", usage=_usage())))
            return 0
        data = _data_dir(args.data_dir)
        result, event = _dispatch(args, data)
        _append_event(data, command=command, success=True, event=event)
        print(_json_value(result))
        return 0
    except SupportError as error:
        if data is None:
            try:
                data = _data_dir(getattr(args, "data_dir", None) if args is not None else None)
            except SupportError:
                data = None
        if data is not None:
            _append_event(data, command=command, success=False, event={"query": getattr(args, "query", None) if args else None, "path": getattr(args, "path", None) if args else None, "identifier": getattr(args, "id", None) if args else None}, error=error)
        print(_json_value(_failure(error)))
        return 1
    except Exception as exc:  # Defensive protocol boundary: never report false success.
        error = SupportError("INTERNAL_ERROR", "支持工具发生未预期错误")
        if data is not None:
            _append_event(data, command=command, success=False, error=error)
        print(_json_value(_failure(error)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
