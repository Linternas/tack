import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_work.store import BoardError, Store
from agent_work.http import BoardServer


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.seed = self.folder / "seed.json"
        self.seed.write_text(json.dumps([{"id": "TEST-001", "title": "기존 항목"}]))
        self.store = Store(self.folder / "test.sqlite3", self.seed)

    def tearDown(self):
        self.directory.cleanup()

    def test_persistence_history_and_no_seed_reset(self):
        created = self.store.write({"title": "새 확인 사항", "summary": "실제 결과는 미확인"})["task"]
        saved = self.store.write({"version": created["version"], "status": "in_progress", "note": "원인 조사 시작"}, created["id"])
        reopened = Store(self.store.path, self.seed)
        self.assertEqual(len(reopened.all()), 2)
        self.assertEqual(reopened.detail(created["id"])["task"]["status"], "in_progress")
        self.assertEqual(len(saved["history"]), 2)
        self.assertEqual(saved["history"][0]["note"], "원인 조사 시작")

    def test_concurrent_updates_do_not_overwrite(self):
        def update(title):
            try:
                return self.store.write({"version": 1, "title": title}, "TEST-001")["task"]["title"]
            except BoardError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ["첫 변경", "둘째 변경"]))
        self.assertEqual(results.count(409), 1)
        self.assertEqual(self.store.detail("TEST-001")["task"]["version"], 2)

    def test_failed_validation_preserves_record_and_history(self):
        with self.assertRaises(BoardError):
            self.store.write({"version": 1, "title": ""}, "TEST-001")
        result = self.store.detail("TEST-001")
        self.assertEqual(result["task"]["title"], "기존 항목")
        self.assertEqual(result["task"]["version"], 1)
        self.assertEqual(len(result["history"]), 1)

    def test_qa_completion_requires_actual_result(self):
        created = self.store.write({"title": "실제 QA", "category": "qa"})["task"]
        with self.assertRaises(BoardError):
            self.store.write({"version": 1, "status": "done", "resolution": "자동 테스트 통과"}, created["id"])
        qa = {**created["qa"], "actual": "QA 환경에서 수동 확인: 새로고침 후 값 유지"}
        saved = self.store.write({"version": 1, "status": "done", "qa_result": "passed", "qa": qa, "resolution": "수동 절차와 기대 결과 전체 확인"}, created["id"])
        self.assertEqual(saved["task"]["status"], "done")

    def test_bad_dependency_and_unknown_field_are_rejected(self):
        for payload in ({"title": "실패", "blocked_by": ["MISSING"]}, {"title": "실패", "token": "invalid"}, {"title": "실패", "sources": [{"label": "링크", "url": "javascript:alert(1)"}]}):
            with self.assertRaises(BoardError):
                self.store.write(payload)

    def test_archive_restore_and_backup(self):
        self.store.write({"version": 1, "status": "archived"}, "TEST-001")
        self.store.write({"version": 2, "status": "needs_recheck"}, "TEST-001")
        destination = self.store.backup()
        with sqlite3.connect(destination) as connection:
            self.assertEqual(connection.execute("SELECT version FROM tasks WHERE id='TEST-001'").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 3)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.directory.name)
        seed = cls.folder / "seed.json"
        seed.write_text("[]")
        cls.store = Store(cls.folder / "http.sqlite3", seed)
        (cls.folder / "docs").mkdir()
        (cls.folder / "docs/test.md").write_text("test source")
        cls.server = BoardServer(("127.0.0.1", 0), cls.store, {"id":"test","name":"Test","workspace":str(cls.folder),"source_roots":["docs"]})
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        data = json.dumps(body) if body is not None else None
        connection.request(method, path, data, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        connection.close()
        return result

    def test_full_create_update_reload_export(self):
        status, body, _ = self.request("POST", "/api/tasks", {"title": "HTTP 저장", "category": "decision", "status": "waiting_decision"})
        self.assertEqual(status, 201)
        task = json.loads(body)["task"]
        status, body, _ = self.request("PATCH", f'/api/tasks/{task["id"]}', {"version": task["version"], "status": "done", "resolution": "사용자 결정 확인"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["task"]["version"], 2)
        status, body, _ = self.request("GET", f'/api/tasks/{task["id"]}')
        self.assertEqual(json.loads(body)["task"]["status"], "done")
        status, body, headers = self.request("GET", "/api/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(any(item["id"] == task["id"] for item in json.loads(body)["tasks"]))

    def test_invalid_write_returns_json_and_does_not_save(self):
        before = len(self.store.all())
        status, body, _ = self.request("POST", "/api/tasks", {"title": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertEqual(len(self.store.all()), before)

    def test_manual_qa_completion_persists_partial_update_and_history(self):
        _, body, _ = self.request("POST", "/api/tasks", {"title": "사용자 완료 QA", "category": "qa", "qa": {"preconditions": "테스트 환경 준비", "steps": "저장 후 재조회", "expected": "입력값 유지"}})
        task = json.loads(body)["task"]
        path = f'/api/tasks/{task["id"]}'
        payload = {"version": task["version"], "status": "done", "qa_result": "passed", "last_checked": "2026-10-22", "resolution": "QA 완료 (통과)\n재조회 후 값 유지", "note": "사용자 QA 완료 처리 · 통과", "qa": {**task["qa"], "actual": "재조회 후 값 유지", "evidence": "screenshots/qa-save.png"}}
        status, _, _ = self.request("PATCH", path, {**payload, "qa_result": "failed"})
        self.assertEqual(status, 400)
        self.assertEqual(self.store.detail(task["id"])["task"]["version"], task["version"])
        status, _, _ = self.request("PATCH", path, payload)
        self.assertEqual(status, 200)
        reopened = Store(self.store.path, self.folder / "seed.json")
        saved = reopened.detail(task["id"])
        self.assertEqual(saved["task"]["status"], "done")
        self.assertEqual(saved["task"]["qa_result"], "passed")
        self.assertEqual(saved["task"]["qa"]["steps"], "저장 후 재조회")
        self.assertEqual(saved["task"]["qa"]["actual"], "재조회 후 값 유지")
        self.assertEqual(saved["task"]["last_checked"], "2026-10-22")
        self.assertEqual(saved["history"][0]["note"], "사용자 QA 완료 처리 · 통과")
        status, _, _ = self.request("PATCH", path, payload)
        self.assertEqual(status, 409)
        self.assertEqual(len(reopened.detail(task["id"])["history"]), 2)

    def test_cross_site_writes_and_wrong_host_are_rejected(self):
        self.assertEqual(self.request("POST", "/api/tasks", {"title": "거절"}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/api/tasks", headers={"Host": "example.com"})[0], 403)

    def test_qa_workflow_create_edit_problem_note_delete_and_restore(self):
        qa = {"preconditions": "QA 화면에서 시작", "steps": "저장한다.\n다시 조회한다.", "expected": "값 유지", "actual": "", "evidence": ""}
        status, body, _ = self.request("POST", "/api/tasks", {"category": "qa", "title": "워크플로우 시나리오", "qa": qa, "note": "QA 시나리오 추가"})
        self.assertEqual(status, 201)
        task = json.loads(body)["task"]
        path = f'/api/tasks/{task["id"]}'
        qa["steps"] = "값을 입력한다.\n저장한다.\n다시 조회한다."
        status, body, _ = self.request("PATCH", path, {"version": task["version"], "qa": qa, "note": "QA 시나리오 수정"})
        self.assertEqual(status, 200)
        task = json.loads(body)["task"]
        qa["actual"] = "3단계 재조회에서 값이 사라짐"
        status, body, _ = self.request("PATCH", path, {"version": task["version"], "status": "needs_recheck", "qa_result": "failed", "qa": qa, "note": "수동 QA · 문제 있음\n3단계 재조회에서 값이 사라짐"})
        self.assertEqual(status, 200)
        task = json.loads(body)["task"]
        for target in ("archived", "needs_recheck"):
            status, body, _ = self.request("PATCH", path, {"version": task["version"], "status": target, "note": "QA 시나리오 삭제 또는 복원"})
            self.assertEqual(status, 200)
            task = json.loads(body)["task"]
            self.assertEqual(task["qa"]["actual"], "3단계 재조회에서 값이 사라짐")
            self.assertEqual(task["qa"]["steps"], qa["steps"])
        reopened = Store(self.store.path, self.folder / "seed.json").detail(task["id"])
        self.assertEqual(reopened["task"]["qa_result"], "failed")
        self.assertTrue(any("3단계 재조회" in entry["note"] for entry in reopened["history"]))
        self.assertEqual(len(reopened["history"]), 5)

    def test_assets_and_read_only_source_boundary(self):
        for path, mime in (("/", "text/html"), ("/dashboard.js", "application/javascript"), ("/dashboard.css", "text/css"), ("/qa", "text/html"), ("/qa/", "text/html"), ("/qa.js", "application/javascript"), ("/qa.css", "text/css")):
            status, body, headers = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(mime, headers["Content-Type"])
            self.assertGreater(len(body), 100)
        self.assertEqual(self.request("GET", "/api/source?path=docs/test.md")[0], 200)
        for path in ("/dashboard.sqlite3", "/seed.json", "/api/source?path=.env", "/api/source?path=../../etc/passwd"):
            self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
