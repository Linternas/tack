import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from agent_work.projects import resolve
from agent_work.store import Store


class CliOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.env = patch.dict(os.environ, AGENT_WORK_HOME=str(self.root / 'state'))
        self.env.start()
        self.store = Store(resolve(self.workspace, create=True)['db_path'])
        self.dep = self.store.write({'title': 'API contract', 'category': 'api', 'status': 'waiting_api', 'detail': 'dependency-secret-detail', 'next_action': 'next-contract'})['task']
        self.task = self.store.write({'title': '수자원 MixedCase', 'category': 'task', 'status': 'in_progress', 'blocked_by': [self.dep['id']], 'next_action': 'long-next-action', 'acceptance': 'target acceptance'})['task']
        self.done = self.store.write({'title': 'Finished', 'status': 'done', 'resolution': 'verified'})['task']
        self.session = self.store.checkpoint({'task_id': self.task['id'], 'goal': 'current goal', 'next_action': 'current next', 'decisions': 'important decision'})['session']

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def cli(self, *args, data=None, ok=True):
        run = subprocess.run(['python3', '-m', 'agent_work', '--workspace', str(self.workspace), *args], input=json.dumps(data) if data else None, capture_output=True, text=True)
        self.assertEqual(run.returncode == 0, ok, run.stderr)
        return json.loads(run.stdout) if ok else run.stderr

    def test_status_default_index_and_full_compatibility(self):
        result = self.cli('status')
        self.assertEqual(result['counts'], {'in_progress': 1, 'waiting_api': 1})
        self.assertEqual({t['id'] for t in result['tasks']}, {self.task['id'], self.dep['id']})
        selected = next(t for t in result['tasks'] if t['id'] == self.task['id'])
        self.assertEqual(selected['blocked_by'], [self.dep['id']])
        self.assertNotIn('next_action', selected)
        self.assertEqual(result['sessions'][0]['version'], 1)
        self.assertNotIn('next_action', result['sessions'][0])
        full = self.cli('status', '--full')
        self.assertEqual(full['tasks'][0].keys(), {'id','title','category','status','version','blocked_by','next_action'})
        self.assertIn('next_action', full['sessions'][0])
        self.assertEqual(len(self.cli('status', '--all')['tasks']), 3)

    def test_filters_explicit_completed_status_and_sessions(self):
        result = self.cli('status', '--status', 'in_progress,todo', '--category', 'task', '--search', 'mixedcase')
        self.assertEqual([t['id'] for t in result['tasks']], [self.task['id']])
        self.assertEqual(len(result['sessions']), 1)
        self.assertEqual(self.cli('status', '--status', 'done')['tasks'][0]['id'], self.done['id'])
        self.assertEqual(self.cli('status', '--search', 'next-contract')['tasks'][0]['id'], self.dep['id'])
        self.assertEqual(self.cli('status', '--category', 'qa')['counts'], {})
        self.assertEqual(self.cli('status', '--category', 'qa')['sessions'], [])
        self.cli('status', '--status', 'typo', ok=False)
        self.cli('status', '--category', 'typo', ok=False)

    def test_write_receipts_full_results_and_conflicts(self):
        created = self.cli('task', 'add', 'New')['task']
        self.assertEqual(set(created), {'id','version','status','updated_at'})
        started = self.cli('task', 'start', created['id'], '--version', '1')['task']
        self.assertEqual(started['version'], 2)
        self.cli('task', 'start', created['id'], '--version', '1', ok=False)
        updated = self.cli('task', 'update', created['id'], '--json', '-', data={'version': 2, 'next_action': 'later'})
        self.assertNotIn('history', updated)
        full = self.cli('task', 'update', created['id'], '--full', '--json', '-', data={'version': 3, 'next_action': 'again'})
        self.assertEqual(len(full['history']), 4)
        self.assertEqual(full['task']['next_action'], 'again')
        self.cli('task', 'update', created['id'], '--json', '-', data={'version': 4, 'status': 'done'}, ok=False)
        session = self.cli('checkpoint', created['id'], '--json', '-', data={'goal': 'work', 'next_action': 'verify'})
        self.assertEqual(set(session['session']), {'id','task_id','status','version','updated_at'})
        run_id = session['session']['id']
        full = self.cli('checkpoint', created['id'], '--session', run_id, '--full', '--json', '-', data={'version': 1, 'status': 'completed'})
        self.assertEqual(len(full['checkpoints']), 2)
        self.assertEqual(self.store.detail(created['id'])['task']['status'], 'in_progress')
        self.cli('checkpoint', created['id'], '--session', run_id, '--json', '-', data={'version': 1, 'status': 'completed'}, ok=False)
        self.assertIn('history', self.cli('task', 'add', 'Full add', '--full'))

    def test_resume_preserves_target_and_current_session_and_full_dependency(self):
        result = self.cli('resume', self.task['id'])
        self.assertEqual(result['task'], self.store.detail(self.task['id'])['task'])
        self.assertEqual(result['sessions'][0]['decisions'], 'important decision')
        dependency = result['dependencies'][0]
        self.assertEqual(dependency['next_action'], 'next-contract')
        self.assertNotIn('detail', dependency)
        self.assertNotIn('qa', dependency)
        full = self.cli('resume', self.task['id'], '--full', '--session', self.session['id'])
        self.assertEqual(full['dependencies'][0]['detail'], 'dependency-secret-detail')
        self.assertEqual(self.store.resume(self.task['id'])['dependencies'][0]['detail'], 'dependency-secret-detail')
