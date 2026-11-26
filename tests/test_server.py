import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

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

    def test_task_map_api_is_read_only_and_returns_saved_version(self):
        from agent_work.task_map import set_map

        task = self.store.write({'title': '지도 API'})['task']['id']
        url = f'/api/tasks/{task}/map'
        status, body, _ = self.request('GET', url)
        self.assertEqual((status, json.loads(body)), (200, {'map': None}))
        matrix = {'version': 0, 'revision': 'a'*40, 'nodes': [], 'edges': [],
                  'flows': [{'step': '빈 목록', 'condition': '', 'outcome': '안내 표시',
                             'kind': 'spec', 'evidence': 'QA 인수 기준'}]}
        saved = set_map(self.store, self.folder, task, matrix)
        status, body, _ = self.request('GET', url)
        self.assertEqual((status, json.loads(body)), (200, saved))
        self.assertEqual(self.request('GET', '/api/tasks/UNKNOWN-99/map')[0], 404)
        self.assertEqual(self.request('POST', url, matrix)[0], 404)

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

    def test_worktrees_use_git_and_match_only_active_checkout_sessions(self):
        import subprocess
        from agent_work.projects import worktrees

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'repo'
            root.mkdir()
            nested = root / 'linked checkout'
            missing = Path(folder) / 'missing'
            def git(*args):
                subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            git('init', '-b', 'main')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false',
                'commit', '--allow-empty', '-m', 'initial')
            git('worktree', 'add', '-b', 'feature', str(nested))
            git('worktree', 'add', '--detach', str(missing))
            missing.rename(Path(folder) / 'moved')
            rows = worktrees(nested, [
                {'workspace': str(root), 'status': 'completed', 'updated_at': '2026-11-20T01:00:00+00:00'},
                {'workspace': str(nested), 'status': 'active'},
                {'workspace': str(missing), 'status': 'active'},
            ], include_activity=True)
            self.assertEqual([(item['branch'], item['current'], item['active']) for item in rows],
                             [('main', False, False), ('feature', True, True)])
            self.assertEqual(Path(rows[1]['path']).resolve(), nested.resolve())
            self.assertEqual(rows[0]['last_session_at'], '2026-11-20T01:00:00+00:00')
            self.assertEqual(rows[1]['last_session_at'], '')
            self.assertTrue(rows[1]['last_git_at'])
        self.assertEqual(json.loads(self.request('GET', '/api/worktrees')[1]), {'worktrees': []})

    def test_worktree_removal_blocks_unsafe_states_and_keeps_the_branch(self):
        import subprocess
        from agent_work.projects import remove_worktree, worktrees

        with tempfile.TemporaryDirectory() as folder:
            root, linked, remote = (Path(folder) / name for name in ('repo', 'linked', 'remote.git'))
            root.mkdir()

            def git(path, *args):
                return subprocess.check_output(
                    ['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                     '-c', 'commit.gpgsign=false', *args], stderr=subprocess.PIPE, text=True).strip()

            git(root, 'init', '-b', 'main')
            (root / '.gitignore').write_text('*.env\nnode_modules/\n.husky/_/\n.husky/local-hook\n')
            git(root, 'add', '.gitignore')
            git(root, 'commit', '-m', 'initial')
            git(root, 'init', '--bare', str(remote))
            git(root, 'remote', 'add', 'origin', str(remote))
            git(root, 'worktree', 'add', '-b', 'feature', str(linked))
            payload = {'path': str(linked.resolve())}
            def row():
                return worktrees(root, [])[1]
            self.assertIsNone(row()['unpushed'])
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            git(linked, 'push', '-u', 'origin', 'feature')
            git(linked, 'commit', '--allow-empty', '-m', 'unpublished')
            self.assertEqual(row()['unpushed'], 1)
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            git(linked, 'push')
            (linked / 'unsaved.txt').write_text('keep')
            for artifact in ('node_modules/dependency', 'apps/web/node_modules/dependency', '.husky/_/husky.sh'):
                target = linked / artifact
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            (linked / '.husky/local-hook').touch()
            (linked / 'local.env').write_text('keep')
            self.assertEqual(row()['uncommitted'], 1)
            self.assertEqual(row()['ignored_paths'], ['.husky/local-hook', 'local.env'])
            (linked / '.husky/local-hook').unlink()
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            (linked / 'unsaved.txt').unlink()
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            confirmed = {**payload, 'ignored_paths': ['local.env']}
            task = self.store.write({'title': 'Worktree safety QA'})['task']
            session = self.store.checkpoint({'task_id': task['id'], 'goal': 'test protection',
                                            'next_action': 'check', 'workspace': str(linked)})['session']
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, confirmed)
            self.store.checkpoint({'version': session['version'], 'status': 'completed'}, session['id'])
            with self.assertRaises(BoardError):
                remove_worktree(linked, self.store, confirmed)
            (linked / 'additional.env').touch()
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, confirmed)
            (linked / 'additional.env').unlink()
            from unittest.mock import patch
            with patch('agent_work.projects.subprocess.run', wraps=subprocess.run) as commands:
                self.assertEqual(remove_worktree(root, self.store, {**confirmed, 'ignored_paths': ['local.env', 'already-gone.env']}),
                                 {'removed': True})
                remove = commands.call_args
                self.assertEqual(remove.args[0][3:6], ['worktree', 'remove', '--'])
                self.assertIsNone(remove.kwargs['timeout'])
            self.assertFalse(linked.exists())
            self.assertEqual(git(root, 'branch', '--list', 'feature'), 'feature')

    def test_detached_worktree_removes_shared_commit_or_preserves_unique_commit(self):
        import subprocess
        from agent_work.projects import remove_worktree, worktrees

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'repo'
            root.mkdir()
            linked = root.parent / 'detached'

            def git(path, *args):
                return subprocess.check_output(
                    ['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                     '-c', 'commit.gpgsign=false', *args], stderr=subprocess.PIPE, text=True).strip()

            git(root, 'init', '-b', 'main')
            (root / '.gitignore').write_text('.next/\n.swc/\ntsconfig.tsbuildinfo\nAGENTS.md\n')
            git(root, 'add', '.gitignore')
            git(root, 'commit', '-m', 'initial')
            git(root, 'worktree', 'add', '--detach', str(linked))
            row = worktrees(root, [])[1]
            self.assertEqual(row['retained_by'], 'main')
            self.assertFalse(row['backup_branch'])
            self.assertEqual(remove_worktree(root, self.store, {'path': str(linked), 'commit': row['commit']}),
                             {'removed': True})
            git(root, 'worktree', 'add', '--detach', str(linked))
            git(linked, 'commit', '--allow-empty', '-m', 'unique detached commit')
            for name in ('.next', '.swc'):
                (linked / name).mkdir()
                (linked / name / 'generated').touch()
            (linked / 'tsconfig.tsbuildinfo').touch()
            (linked / 'AGENTS.md').touch()
            row = worktrees(root, [])[1]
            self.assertEqual(row['ignored_paths'], ['AGENTS.md'])
            self.assertTrue(row['backup_branch'])
            payload = {'path': str(linked), 'commit': row['commit'], 'ignored_paths': row['ignored_paths']}
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            payload['backup_branch'] = row['backup_branch']
            (linked / 'unsaved').touch()
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            (linked / 'unsaved').unlink()
            git_dir = Path(git(linked, 'rev-parse', '--absolute-git-dir'))
            (git_dir / 'rebase-merge').mkdir()
            with self.assertRaises(BoardError):
                remove_worktree(root, self.store, payload)
            (git_dir / 'rebase-merge').rmdir()
            self.assertEqual(remove_worktree(root, self.store, payload),
                             {'removed': True, 'backup_branch': row['backup_branch']})
            self.assertFalse(linked.exists())
            self.assertEqual(git(root, 'rev-parse', 'refs/heads/' + row['backup_branch']), row['commit'])

    def test_project_note_round_trip_search_and_version(self):
        before = self.store.all()
        payload = {"title": "Ideas", "body": "재현 절차\n두 번째 줄"}
        self.assertEqual(self.request("GET", "/notes")[0], 200)
        self.assertEqual(self.request("POST", "/api/notes", {**payload, "body": " "})[0], 400)
        status, body, _ = self.request("POST", "/api/notes", payload)
        self.assertEqual(status, 201)
        note = json.loads(body)
        path = f'/api/notes/{note["id"]}'
        edit = {**payload, "title": "Updated", "version": note["version"]}
        self.assertEqual(self.request("PATCH", path, edit)[0], 200)
        self.assertEqual(self.request("PATCH", path, edit)[0], 409)
        status, body, _ = self.request("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["body"], payload["body"])
        from urllib.parse import urlencode
        status, body, _ = self.request("GET", "/api/notes?" + urlencode({"q": "두 번째"}))
        self.assertEqual(status, 200)
        self.assertEqual([item["title"] for item in json.loads(body)["notes"]], ["Updated"])
        _, body, _ = self.request("GET", "/api/export")
        self.assertEqual(json.loads(body)["notes"][0]["id"], note["id"])
        self.assertEqual(self.store.all(), before)

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
        for path, mime in (("/", "text/html"), ("/dashboard.js", "application/javascript"), ("/dashboard.css", "text/css"), ("/qa", "text/html"), ("/qa/", "text/html"), ("/qa.js", "application/javascript"), ("/qa.css", "text/css"), ("/favicon.svg", "image/svg+xml")):
            status, body, headers = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(mime, headers["Content-Type"])
            self.assertGreater(len(body), 100)
            if mime == "text/html":
                self.assertIn(b'<link rel="icon" href="/favicon.svg"', body)
            if mime == "image/svg+xml":
                self.assertTrue(body.startswith(b'<svg'))
        self.assertEqual(self.request("GET", "/api/source?path=docs/test.md")[0], 200)
        for path in ("/dashboard.sqlite3", "/seed.json", "/api/source?path=.env", "/api/source?path=../../etc/passwd"):
            self.assertEqual(self.request("GET", path)[0], 404)


class ExecutionHistoryHttpTests(unittest.TestCase):
    request = HttpTests.request

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name).resolve()
        self.store = Store(self.folder / 'http.sqlite3')
        project = {'id': 'test', 'name': 'Test', 'workspace': str(self.folder), 'source_roots': []}
        self.server = BoardServer(('127.0.0.1', 0), self.store, project)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_empty_history_and_removed_execution_routes_preserve_db(self):
        before = self.store.path.read_bytes()
        status, body, _ = self.request('GET', '/api/executions')
        self.assertEqual((status, json.loads(body)), (200, {'items': [], 'total': 0, 'has_more': False}))
        self.assertEqual(self.request('GET', '/api/executions/GOAL-MISSING')[0], 404)
        self.assertEqual(self.request('GET', '/api/executions?limit=101')[0], 400)
        for path in ('/team', '/goals', '/goals.js', '/workforce.html', '/office.js', '/api/goals',
                     '/api/orchestration', '/api/workforce', '/api/organization', '/api/demo-days',
                     '/api/session-office', '/api/team/settings'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)
        for path in ('/api/organization/teams', '/api/demo-days', '/api/orchestration/enable',
                     '/api/session-office/start', '/api/goals/draft', '/api/executions'):
            with self.subTest(path=path):
                self.assertEqual(self.request('POST', path, {})[0], 404)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_legacy_history_preserves_status_checkout_usage_and_details(self):
        task_id = self.store.write({'title': '기존 티켓'})['task']['id']
        goal_id, run_id, office_id = 'GOAL-OLD', 'EXEC-OLD', 'aabbccddee00:stored-session'
        old, new = '2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00'
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE goals (id TEXT PRIMARY KEY, task_id TEXT, version INTEGER, data TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE executions (id TEXT PRIMARY KEY, goal_id TEXT, task_id TEXT, data TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE office_executions (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
                CREATE TABLE office_sessions (workspace TEXT, id TEXT, version INTEGER, team_id TEXT, role_id TEXT, updated_at TEXT, PRIMARY KEY(workspace,id));
            """)
            goal = {'title': '기존 목표', 'workspace': str(self.folder), 'status': 'done',
                    'verification_results': [{'status': 'succeeded', 'exit_code': 0, 'argv': ['secret-command']}]}
            run = {'status': 'failed', 'role': 'review', 'tokens': 42, 'model': 'example',
                   'review_summary': '저장된 요약', 'prompt': 'secret-prompt', 'exit_code': 2}
            db.execute('INSERT INTO goals VALUES (?,?,?,?,?,?)', (goal_id, task_id, 1, json.dumps(goal), old, old))
            db.execute('INSERT INTO executions VALUES (?,?,?,?,?,?)', (run_id, goal_id, task_id, json.dumps(run), old, new))
            db.execute('INSERT INTO office_executions VALUES (?,?,?)', (office_id, '{"status":"completed","process_group":42}', new))
            db.execute('INSERT INTO office_sessions VALUES (?,?,?,?,?,?)', (str(self.folder), office_id, 1, None, 'read', new))
        before = self.store.path.read_bytes()

        status, body, _ = self.request('GET', '/api/executions?limit=1')
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual((data['total'], data['has_more']), (3, True))
        self.assertEqual([item['id'] for item in data['items']], [run_id])
        data = json.loads(self.request('GET', '/api/executions?offset=1')[1])
        self.assertEqual([item['id'] for item in data['items']], [office_id, goal_id])
        self.assertFalse(data['has_more'])

        for execution_id, kind, expected in (
            (goal_id, 'goal', {'verification_results': [{'status': 'succeeded', 'exit_code': 0}], 'created_at': old}),
            (run_id, 'role', {'summary': '저장된 요약', 'tokens': 42, 'model': 'example', 'exit_code': 2, 'created_at': old}),
            (office_id, 'session', {}),
        ):
            status, body, _ = self.request('GET', '/api/executions/' + quote(execution_id, safe=''))
            data = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual((data['item']['id'], data['item']['kind'], data['item']['workspace']),
                             (execution_id, kind, str(self.folder)))
            self.assertEqual(data['details'], expected)
        self.assertEqual(json.loads(self.request('GET', '/api/usage')[1])['summary']['total_tokens'], 42)
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
