from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import support  # noqa: E402


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class SupportToolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.config = self.root / "knowledge-source.json"
        self.config.write_text(json.dumps({"repo": "example/knowledge", "ref": "main"}), encoding="utf-8")
        self.commit = "commit-one"
        self.bodies = {
            "README.md": "# 目录索引\n通知和群聊产品资料目录。\n",
            "cases/notification.md": "# 通知缺失\n群聊通知未收到时检查通知设置。\n",
            "product/messaging.md": "# 消息发送\n消息发送支持文本和图片。\n",
        }
        self.fail_paths: set[str] = set()
        self.truncated = False
        self._install_source()
        self.config_patch = mock.patch.object(support, "CONFIG_PATH", self.config)
        self.urlopen_patch = mock.patch.object(support.urllib.request, "urlopen", side_effect=self._urlopen)
        self.config_patch.start()
        self.urlopen_patch.start()

    def tearDown(self):
        self.urlopen_patch.stop()
        self.config_patch.stop()
        self.temp.cleanup()

    def _install_source(self):
        self.commit_url, self.tree_url_template = support._github_urls("example/knowledge", "main")

    def _tree(self):
        tree = []
        for path, value in sorted(self.bodies.items()):
            body = value.encode("utf-8")
            tree.append({"path": path, "type": "blob", "sha": support._git_blob_sha(body)})
        tree.append({"path": "notes.txt", "type": "blob", "sha": "ignored"})
        return tree

    def _urlopen(self, request, timeout=0):
        url = request.full_url
        if url == self.commit_url:
            return FakeResponse(json.dumps({"sha": self.commit}).encode("utf-8"))
        tree_url = self.tree_url_template.format(commit=self.commit)
        if url == tree_url:
            return FakeResponse(json.dumps({"truncated": self.truncated, "tree": self._tree()}).encode("utf-8"))
        prefix = support._raw_url("example/knowledge", self.commit, "")
        if url.startswith(prefix):
            path = url[len(prefix) :]
            if path in self.fail_paths:
                raise urllib.error.URLError("temporary network failure")
            if path not in self.bodies:
                raise urllib.error.HTTPError(url, 404, "missing", {}, None)
            return FakeResponse(self.bodies[path].encode("utf-8"))
        raise AssertionError(f"unexpected URL: {url}")

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = support.main(["--data-dir", str(self.data), *args])
        value = json.loads(output.getvalue())
        return code, value

    def write_input(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def sync(self):
        code, result = self.call("sync")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"], result)
        return result

    def requirement_payload(self, request_id="req-1", summary="保存需求"):
        return {
            "request_id": request_id,
            "summary": summary,
            "scenario": "群聊中发送消息",
            "desired_outcome": "记录后续处理",
            "source_refs": ["conversation:local"],
        }

    def curation_payload(self, candidate_id="case-1", resolved=True):
        return {
            "candidate_id": candidate_id,
            "title": "通知缺失案例",
            "symptom": "群聊中收不到通知",
            "environment": "桌面端群聊",
            "resolution": "检查通知设置并重试",
            "resolution_evidence": "用户确认通知恢复",
            "source_refs": ["conversation:local"],
            "resolved": resolved,
        }

    def test_sync_search_read_and_types(self):
        result = self.sync()
        self.assertEqual(result["source"]["commit"], "commit-one")
        self.assertEqual(result["files"], ["README.md", "cases/notification.md", "product/messaging.md"])

        code, result = self.call("search", "--query", "通知")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["total"], 1)
        hit = result["results"][0]
        self.assertEqual(hit["path"], "cases/notification.md")
        self.assertEqual(hit["type"], "case")
        self.assertFalse(hit["body_read"])
        self.assertNotIn("README.md", [item["path"] for item in result["results"]])

        code, result = self.call("read", "--path", "README.md")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["type"], "index")
        self.assertTrue(result["body_read"])
        self.assertEqual(result["commit"], "commit-one")

        code, result = self.call("read", "--path", "cases/notification.md")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["type"], "case")
        self.assertIn("检查通知设置", result["body"])

        code, result = self.call("read", "--path", "product/messaging.md")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["type"], "product")

    def test_crlf_cache_hash_uses_original_bytes(self):
        self.bodies["product/messaging.md"] = "# 消息发送\r\n消息发送支持文本和图片。\r\n"
        self.sync()
        code, result = self.call("read", "--path", "product/messaging.md")
        self.assertEqual(code, 0, result)
        self.assertIn("\r\n", result["body"])
        self.assertEqual(result["sha256"], support._sha256(self.bodies["product/messaging.md"].encode("utf-8")))

    def test_sync_failure_keeps_previous_cache_and_reports_truncation(self):
        self.sync()
        old_body = (self.data / "knowledge" / "cases/notification.md").read_bytes()
        self.commit = "commit-two"
        self.fail_paths = {"product/messaging.md"}
        code, result = self.call("sync")
        self.assertNotEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "SOURCE_UNAVAILABLE")
        self.assertEqual((self.data / "knowledge" / "cases/notification.md").read_bytes(), old_body)
        self.assertEqual(json.loads((self.data / "knowledge/manifest.json").read_text())["commit"], "commit-one")
        self.assertFalse(any(path.name.startswith(".knowledge-sync-") for path in self.data.iterdir()))

        self.fail_paths.clear()
        self.truncated = True
        code, result = self.call("sync")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "SOURCE_TREE_TRUNCATED")
        self.assertEqual(json.loads((self.data / "knowledge/manifest.json").read_text())["commit"], "commit-one")

    def test_sync_hash_mismatch_keeps_cache(self):
        self.sync()
        previous = (self.data / "knowledge/manifest.json").read_bytes()
        self.commit = "commit-two"
        self.truncated = False
        self.fail_paths.clear()
        # A valid-looking tree SHA that does not match the downloaded body must fail.
        original_tree = self._tree
        self._tree = lambda: [
            {"path": "README.md", "type": "blob", "sha": "0" * 40},
            {"path": "cases/notification.md", "type": "blob", "sha": support._git_blob_sha(self.bodies["cases/notification.md"].encode())},
            {"path": "product/messaging.md", "type": "blob", "sha": support._git_blob_sha(self.bodies["product/messaging.md"].encode())},
        ]
        try:
            code, result = self.call("sync")
        finally:
            self._tree = original_tree
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "SOURCE_HASH_MISMATCH")
        self.assertEqual((self.data / "knowledge/manifest.json").read_bytes(), previous)

    def test_not_synced_path_boundary_and_missing_file(self):
        code, result = self.call("search", "--query", "通知")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "NOT_SYNCED")

        self.sync()
        for bad in ("../README.md", "/etc/passwd", "cases\\notification.md"):
            code, result = self.call("read", "--path", bad)
            self.assertNotEqual(code, 0)
            self.assertEqual(result["error"]["code"], "INVALID_PATH")
        code, result = self.call("read", "--path", "cases/nope.md")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "PATH_NOT_FOUND")

    def test_requirement_save_idempotent_conflict_and_bad_json(self):
        input_path = self.write_input("requirement.json", self.requirement_payload())
        code, first = self.call("requirement-save", "--input", str(input_path))
        self.assertEqual(code, 0, first)
        self.assertEqual(first["status"], "saved")
        self.assertEqual(first["storage"], "local")
        self.assertFalse(first["idempotent"])
        self.assertTrue(first["written"])
        raw = json.loads((self.data / "requirements/req-1.json").read_text())
        self.assertNotIn("simulation", raw)
        self.assertNotIn("external_submission", raw)
        before = (self.data / "requirements/req-1.json").read_bytes()

        code, repeat = self.call("requirement-save", "--input", str(input_path))
        self.assertEqual(code, 0, repeat)
        self.assertTrue(repeat["idempotent"])
        self.assertFalse(repeat["written"])
        self.assertEqual(before, (self.data / "requirements/req-1.json").read_bytes())

        omitted_optional = self.requirement_payload()
        omitted_optional.pop("source_refs")
        omitted = self.write_input("requirement-omitted-optional.json", omitted_optional)
        code, result = self.call("requirement-save", "--input", str(omitted))
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "REQUEST_CONFLICT")

        conflict = self.write_input("requirement-conflict.json", self.requirement_payload(summary="不同内容"))
        code, result = self.call("requirement-save", "--input", str(conflict))
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "REQUEST_CONFLICT")

        code, result = self.call("requirement-read", "--id", "req-1")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["record"]["request_id"], "req-1")

        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        code, result = self.call("requirement-save", "--input", str(broken))
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "INVALID_JSON")

        (self.data / "requirements/req-1.json").write_text("{", encoding="utf-8")
        code, result = self.call("requirement-read", "--id", "req-1")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "RECORD_CORRUPT")

    def test_curation_unresolved_pending_approval_search_withdraw(self):
        unresolved = self.write_input("unresolved.json", self.curation_payload(resolved=False))
        code, result = self.call("curation-save", "--input", str(unresolved))
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "UNRESOLVED_CANDIDATE")

        candidate = self.write_input("candidate.json", self.curation_payload())
        code, result = self.call("curation-save", "--input", str(candidate))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "pending_review")
        before = (self.data / "curation/case-1.json").read_bytes()
        code, result = self.call("curation-save", "--input", str(candidate))
        self.assertEqual(code, 0, result)
        self.assertTrue(result["idempotent"])
        self.assertFalse(result["written"])
        self.assertEqual(before, (self.data / "curation/case-1.json").read_bytes())

        code, result = self.call("curation-list")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["results"][0]["status"], "pending_review")
        self.sync()
        code, result = self.call("search", "--query", "通知缺失案例")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["total"], 0)
        code, result = self.call("read", "--path", "curated/case-1.json")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "CURATED_NOT_APPROVED")

        code, result = self.call("curation-review", "--id", "case-1", "--decision", "approve", "--note", "证据充分")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "approved")
        code, result = self.call("search", "--query", "通知缺失案例")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["path"], "curated/case-1.json")
        code, result = self.call("read", "--path", "curated/case-1.json")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["body_read"])

        before = (self.data / "curation/case-1.json").read_bytes()
        code, result = self.call("curation-withdraw", "--id", "case-1", "--note", "资料过期")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "withdrawn")
        code, result = self.call("search", "--query", "通知缺失案例")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["total"], 0)
        code, result = self.call("read", "--path", "curated/case-1.json")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "CURATED_NOT_APPROVED")
        code, result = self.call("curation-read", "--id", "case-1")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "withdrawn")
        self.assertEqual(result["record"]["resolution"], "检查通知设置并重试")

        after_transition = (self.data / "curation/case-1.json").read_bytes()
        self.assertNotEqual(before, after_transition)
        code, result = self.call("curation-withdraw", "--id", "case-1", "--note", "再次撤回")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["idempotent"])
        self.assertFalse(result["written"])
        self.assertEqual(after_transition, (self.data / "curation/case-1.json").read_bytes())

    def test_curation_reject_and_withdraw_state_errors_and_conflict(self):
        candidate = self.write_input("candidate.json", self.curation_payload(candidate_id="case-2"))
        self.assertEqual(self.call("curation-save", "--input", str(candidate))[0], 0)
        code, result = self.call("curation-withdraw", "--id", "case-2", "--note", "不能撤回")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "CURATION_WITHDRAW_INVALID_STATE")
        code, result = self.call("curation-review", "--id", "case-2", "--decision", "reject", "--note", "证据不足")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "rejected")
        code, result = self.call("curation-review", "--id", "case-2", "--decision", "reject", "--note", "重复判定")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["idempotent"])
        code, result = self.call("curation-withdraw", "--id", "case-2", "--note", "仍不能撤回")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "CURATION_WITHDRAW_INVALID_STATE")

        conflict_payload = self.curation_payload(candidate_id="case-2")
        conflict_payload["resolution"] = "其他方案"
        conflict = self.write_input("candidate-conflict.json", conflict_payload)
        code, result = self.call("curation-save", "--input", str(conflict))
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "CURATION_CONFLICT")

        bad_candidate = self.write_input("bad-candidate.json", self.curation_payload(candidate_id="case-3"))
        self.assertEqual(self.call("curation-save", "--input", str(bad_candidate))[0], 0)
        (self.data / "curation/case-3.json").write_text("{", encoding="utf-8")
        code, result = self.call("curation-list")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "RECORD_CORRUPT")

    def test_sync_does_not_overwrite_records_and_state_isolation(self):
        requirement = self.write_input("requirement.json", self.requirement_payload())
        self.assertEqual(self.call("requirement-save", "--input", str(requirement))[0], 0)
        self.sync()
        self.commit = "commit-two"
        self.assertEqual(self.call("sync")[0], 0)
        code, result = self.call("requirement-read", "--id", "req-1")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "saved")

        other = self.root / "other-data"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = support.main(["--data-dir", str(other), "search", "--query", "通知"])
        result = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"]["code"], "NOT_SYNCED")
        self.assertFalse((other / "requirements/req-1.json").exists())

    def test_events_keep_command_outcome_and_body_read(self):
        self.sync()
        self.call("search", "--query", "通知")
        self.call("read", "--path", "cases/notification.md")
        lines = [json.loads(line) for line in (self.data / "events.jsonl").read_text().splitlines()]
        self.assertTrue(any(item["command"] == "sync" and item["success"] for item in lines))
        search_event = next(item for item in lines if item["command"] == "search")
        read_event = next(item for item in lines if item["command"] == "read")
        self.assertFalse(search_event["body_read"])
        self.assertTrue(read_event["body_read"])


if __name__ == "__main__":
    unittest.main()
