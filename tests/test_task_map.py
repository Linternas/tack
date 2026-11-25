from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_work.store import BoardError, Store
from agent_work.task_map import get_map, set_map


class TaskMapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.ref = 'src/[id].tsx'
        (self.repo/'src').mkdir()
        (self.repo/self.ref).write_text('export const route = true;\n')
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')
        self.store = Store(self.root/'work.sqlite3')
        self.task = self.store.write({'title': '영향 범위 테스트'})['task']
        self.data = {
            'version': 0,
            'revision': self.git('rev-parse', 'HEAD'),
            'nodes': [
                {'id': 'route', 'kind': 'route', 'label': '결재함', 'ref': {'path': self.ref, 'line': 1}},
                {'id': 'api', 'kind': 'api', 'label': '목록 조회'},
            ],
            'edges': [{'from': 'route', 'to': 'api', 'label': '조회'}],
            'flows': [
                {'step': '빈 상태', 'condition': '검색 결과 없음', 'outcome': '빈 상태 표시', 'kind': 'code',
                 'ref': {'path': self.ref, 'line': 1}},
                {'step': '빈 상태', 'condition': '빈 fixture', 'outcome': '목록 0건', 'kind': 'browser',
                 'evidence': '로컬 브라우저에서 조회 전용 fixture를 관찰'},
            ],
        }

    def git(self, *args):
        return subprocess.run(['git','-C',str(self.repo),'-c','user.name=Fixture',
                               '-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false',
                               '-c','core.hooksPath=/dev/null',*args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_versions_and_project_isolation(self):
        key = self.task['id']
        self.assertEqual(get_map(self.store, self.repo, key), {'map': None})
        self.assertEqual(set_map(self.store, self.repo, key, self.data)['map']['version'], 1)
        with self.assertRaises(BoardError) as err:
            set_map(self.store, self.repo, key, self.data)
        self.assertEqual(err.exception.status, 409)
        newer = {**self.data, 'version': 1, 'flows': [self.data['flows'][0]]}
        self.assertEqual(set_map(self.store, self.repo, key, newer)['map']['version'], 2)
        self.assertEqual(get_map(Store(self.store.path, readonly=True), self.repo, key)['map']['flows'], newer['flows'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM task_maps').fetchone()[0], 2)
        self.assertEqual(self.store.detail(key)['task']['version'], self.task['version'])
        other = Store(self.root/'other.sqlite3')
        self.assertEqual(other.write({'title':'다른 프로젝트'})['task']['id'], key)
        self.assertEqual(get_map(other, self.repo, key), {'map': None})

    def test_freshness_checks_referenced_file_and_commit(self):
        key = self.task['id']
        self.assertIs(set_map(self.store, self.repo, key, self.data)['map']['stale'], False)
        (self.repo/'src/i.tsx').write_text('unrelated fixture\n')
        self.assertIs(get_map(self.store, self.repo, key)['map']['stale'], False)
        (self.repo/self.ref).write_text('uncommitted change\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.data['revision'])
        self.assertIs(get_map(self.store, self.repo, key)['map']['stale'], True)
        self.git('add', '.')
        self.git('commit', '-m', 'updated')
        self.assertIs(get_map(self.store, self.repo, key)['map']['stale'], True)
        self.assertIsNone(get_map(self.store, self.root/'missing', key)['map']['stale'])

    def test_archived_project_can_only_read_the_map(self):
        key = self.task['id']
        initial = set_map(self.store, self.repo, key, self.data)
        with self.store.connect() as db:
            db.execute("INSERT INTO metadata VALUES ('project_lifecycle',?)",
                       (json.dumps({'status': 'archived', 'version': 2}),))
        self.assertEqual(get_map(self.store, self.repo, key), initial)
        with self.assertRaises(BoardError) as err:
            set_map(self.store, self.repo, key, {**self.data, 'version': 1})
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(get_map(self.store, self.repo, key), initial)

    def test_rejects_dangling_edges_unsafe_refs_and_unevidenced_browser_claims(self):
        for issue in ('edge', 'ref', 'browser'):
            with self.subTest(issue=issue):
                invalid = deepcopy(self.data)
                if issue == 'edge':
                    invalid['edges'][0]['to'] = 'missing'
                elif issue == 'ref':
                    invalid['nodes'][0]['ref']['path'] = 'src/../outside.ts'
                else:
                    invalid['flows'][1].pop('evidence')
                with self.assertRaises(BoardError):
                    set_map(self.store, self.repo, self.task['id'], invalid)
        self.assertEqual(get_map(self.store,self.repo,self.task['id']), {'map': None})

    def test_flow_links_validate_and_round_trip(self):
        key = self.task['id']
        for invalid in (['missing'], ['api', 'api'], 'api', [None]):
            with self.subTest(invalid=invalid):
                broken = deepcopy(self.data)
                broken['flows'][0]['node_ids'] = invalid
                with self.assertRaises(BoardError):
                    set_map(self.store, self.repo, key, broken)
        self.data['flows'][0]['node_ids'] = ['route', 'api']
        set_map(self.store, self.repo, key, self.data)
        flows = get_map(self.store, self.repo, key)['map']['flows']
        self.assertEqual(flows[0]['node_ids'], ['route', 'api'])
        self.assertNotIn('node_ids', flows[1])

    def test_core_cli_uses_same_storage_contract(self):
        env = {**os.environ, 'AGENT_WORK_HOME': str(self.root/'unused-global-state')}
        def cli(*args, data=None):
            proc = subprocess.run([sys.executable,'-B','-m','agent_work','--workspace',str(self.repo),*args],
                                  input=data, capture_output=True,text=True,check=True,env=env)
            return json.loads(proc.stdout)
        cli('init', '--local')
        ticket = cli('task','add','작업 지도 CLI')['task']['id']
        self.assertEqual(cli('map','show',ticket), {'map': None})
        saved = cli('map','set',ticket,'--json','-',data=json.dumps(self.data))
        self.assertEqual(saved['map']['version'], 1)
        self.assertEqual(cli('map','show',ticket), saved)
