import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agent_work import herdr
from agent_work.projects import resolve
from agent_work.store import BoardError, Store


class HerdrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root/'project'; self.workspace.mkdir()
        self.env = patch.dict(os.environ, AGENT_WORK_HOME=str(self.root/'data')); self.env.start()
        self.project = resolve(self.workspace, create=True)
        self.store = Store(self.project['db_path'])
        self.task = self.store.write({'title':'로그인 수정','status':'in_progress','acceptance':'회귀 검증','next_action':'오류 재현'})['task']
        self.other = self.store.write({'title':'다른 작업'})['task']
        self.terminal = {'address':'t6','state':'live','created_ms':123,'cwd':str(self.workspace),'status':{'app':'codex','state':'idle'},'foreground':{'pid':12345,'cwd':str(self.workspace)}}
        self.mock = patch('agent_work.herdr.subprocess',SimpleNamespace(run=lambda *a,**kw:subprocess.CompletedProcess(a[0],0,json.dumps(self.terminal),''),TimeoutExpired=subprocess.TimeoutExpired)); self.mock.start()

    def tearDown(self):
        self.mock.stop(); self.env.stop(); self.temp.cleanup()

    def command(self, action, **kwargs):
        return herdr.dispatch(SimpleNamespace(herdr_action=action,address='t6',query='',session=herdr.context('t6')['key'],from_task=None,**kwargs))

    def test_real_execution_binding_is_persistent_and_task_fields_are_unchanged(self):
        before = self.store.detail(self.task['id'])
        result = self.command('bind', task_id=self.task['id'])
        self.assertEqual(result['task']['acceptance'],'회귀 검증')
        self.assertEqual(self.command('inspect')['task']['id'],self.task['id'])
        self.assertEqual(self.store.detail(self.task['id']),before)
        session = result['session']['key']
        with self.assertRaises(BoardError):self.command('bind',task_id=self.other['id'])
        result = herdr.dispatch(SimpleNamespace(herdr_action='bind',address='t6',session=session,from_task=self.task['id'],task_id=self.other['id']))
        self.assertEqual(result['task']['id'],self.other['id'])
        result = herdr.dispatch(SimpleNamespace(herdr_action='unbind',address='t6',session=session,from_task=self.other['id']))
        self.assertIsNone(result['task'])
        self.assertEqual(self.store.detail(self.task['id']),before)

    def test_new_process_does_not_inherit_previous_connection(self):
        original = self.command('bind',task_id=self.task['id'])
        self.terminal['foreground']['pid'] += 1
        self.assertIsNone(self.command('inspect')['task'])
        with self.assertRaises(BoardError) as error:
            herdr.dispatch(SimpleNamespace(herdr_action='bind',address='t6',session=original['session']['key'],from_task=None,task_id=self.other['id']))
        self.assertEqual(error.exception.status,409)

    def test_search_and_verification_are_bounded(self):
        from agent_work.records import Records
        text = '테스트 근거 '*500
        Records(self.store).add(self.task['id'],'verification',text)
        self.command('bind',task_id=self.task['id'])
        result = herdr.snapshot(herdr.context('t6'),'로그인')
        self.assertEqual(len(result['tasks']),1)
        self.assertLessEqual(len(result['task']['verification']),801)
        self.assertIn('테스트 근거',result['task']['verification'])

    def test_scope_and_actual_agent_identity_are_required(self):
        with self.assertRaises(BoardError):herdr.context('remote/t6')
        self.terminal['status'] = None
        with self.assertRaises(BoardError):herdr.context('t6')
        self.terminal['status'] = {'app':'codex'}
        empty = self.root/'unregistered'; empty.mkdir()
        self.terminal['foreground']['cwd'] = str(empty)
        with self.assertRaises(BoardError):self.command('inspect')
        self.assertFalse((empty/'.agent-work').exists())

    def test_archived_project_remains_readonly_and_missing_tasks_are_rejected(self):
        from agent_work.project_lifecycle import change
        with self.assertRaises(BoardError):self.command('bind',task_id='LOCAL-9999')
        self.command('bind',task_id=self.task['id'])
        change(self.project,'archive',{'version':1})
        self.assertEqual(self.command('inspect')['task']['id'],self.task['id'])
        with self.assertRaises(BoardError):self.command('bind',task_id=self.task['id'])

    def test_managed_base36_execution_maps_to_its_actual_job_and_new_manual_agent_does_not_inherit_it(self):
        self.terminal.update(address='t1g',labels={'tack_workspace':str(self.workspace),'tack_goal':'G','tack_job':self.task['id']})
        self.terminal['status']={'app':'codex','state':'working','title':'Owned job'}
        self.terminal['foreground']['name']='python3'
        data={'herdr_address':'t1g','herdr_title':'Owned job','status':'running'}
        with self.store.connect() as db:
            db.execute('CREATE TABLE executions (id TEXT,goal_id TEXT,task_id TEXT,data TEXT)')
            db.execute('INSERT INTO executions VALUES (?,?,?,?)',('R','G',self.task['id'],json.dumps(data)))
        context=herdr.context('t1g')
        self.assertTrue(context['managed'])
        self.assertEqual(herdr.snapshot(context)['task']['id'],self.task['id'])
        data['status']='succeeded'
        with self.store.connect() as db:db.execute('UPDATE executions SET data=?',(json.dumps(data),))
        self.terminal['foreground']['name']='codex'
        self.terminal['status']['title']='New manual task'
        self.assertNotIn('managed',herdr.context('t1g'))
