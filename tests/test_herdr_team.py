import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agent_work.herdr_team import dispatch
from agent_work.projects import resolve
from agent_work.store import Store, BoardError
from agent_work.workforce import Workforce
from agent_work.herdr_process import HerdrProcess


class HerdrTeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, AGENT_WORK_HOME=str(self.root/'data'))
        self.env.start()
        self.workspace = self.root/'project'; self.workspace.mkdir()
        self.project = resolve(self.workspace, create=True)
        self.work = Workforce(Store(self.project['db_path']),self.workspace)
        self.goal = self.work.create('연결 검증')

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def call(self, action, data=None, goal=None):
        return dispatch(SimpleNamespace(action=action,payload=json.dumps(data or {}),goal=goal),self.project)

    def test_connect_preserves_permissions_and_modes(self):
        before = self.work.team.settings()
        self.assertEqual(self.call('connect')['runtime'],'herdr-codex-exec')
        after = self.work.team.settings()
        self.assertEqual(before['permissions'],after['permissions'])
        self.assertEqual(before['execution_mode'],after['execution_mode'])
        with self.work.store.connect() as db:
            self.work.set_goal(db,self.goal['id'],worker='existing-worker')
        with self.assertRaises(BoardError):self.call('connect')

    def test_create_and_approval_use_existing_queue_with_versions(self):
        self.call('connect')
        with patch('agent_work.herdr_team.start') as start:
            result = self.call('create',{'title':'새 목표','acceptance':'통과','verification_text':'python3 -m unittest'})
            key = result['goal']['id']
            start.assert_called_once()
            self.assertEqual(result['goal']['status'],'ready')
        goal = self.work.goal(key)
        result = self.call('assign',{'version':goal['version'],'title':'구현','employee':'implement','instructions':'구현과 검증','files':['demo.py'],'depends_on':[],'client_id':'unique'},key)
        job = next(j for j in result['jobs'] if j['kind']=='task')
        self.assertEqual(job['status'],'awaiting_approval')
        with patch('agent_work.herdr_team.start') as start:
            self.call('approve',{'version':result['goal']['version']},key)
            start.assert_called_once()
        self.assertEqual(self.work.job(key,job['id'])['status'],'queued')
        with self.assertRaises(BoardError):self.call('approve',{'version':goal['version']},key)

    def test_status_never_starts_an_agent_and_results_are_bounded(self):
        job = self.work.jobs(self.goal['id'])[0]
        with self.work.store.connect() as db:self.work.change(db,job['id'],{'result':'a'*10000})
        with patch('agent_work.herdr_team.start') as start:
            result = self.call('status',goal=self.goal['id'])
            start.assert_not_called()
        self.assertLessEqual(len(result['jobs'][0]['result']),1801)


class HerdrProcessTests(unittest.TestCase):
    def launch(self, code, parent=None):
        real = subprocess
        workers = {}
        self.commands = []
        def run(argv, **kwargs):
            self.commands.append(argv)
            if argv[:2] == ['herdr-app','ls']:
                return real.CompletedProcess(argv,0,json.dumps({'spaces':[{'id':'SPACE','tabs':[{'terminals':[{'terminal':'t12'}]}]}]}),'')
            if argv[:3] == ['herdr-app','do','tab.new']:
                workers['process'] = real.Popen(json.loads(argv[argv.index('--argv')+1]),stdout=real.DEVNULL,stderr=real.DEVNULL)
                return real.CompletedProcess(argv,0,json.dumps({'terminal':'t1g'}),'')
            if argv[1] == 'label':
                return real.CompletedProcess(argv,0,'{}','')
            if argv[1] == 'run':
                proc = real.Popen(argv[argv.index('--')+1:],stdout=real.DEVNULL,stderr=real.DEVNULL)
                workers['process'] = proc
                return real.CompletedProcess(argv,0,json.dumps({'address':'t1b'}),'')
            if argv[1] == 'kill':
                workers['process'].send_signal(signal.SIGTERM)
                workers['process'].wait(timeout=8)
                return real.CompletedProcess(argv,0,'{}','')
            return real.CompletedProcess(argv,0,json.dumps({'state':'live' if workers['process'].poll() is None else 'finished'}),'')
        mock = patch('agent_work.herdr_process.subprocess',SimpleNamespace(run=run,TimeoutExpired=real.TimeoutExpired))
        mock.start(); self.addCleanup(mock.stop)
        process = HerdrProcess([sys.executable,'-c',code],os.getcwd(),dict(os.environ),'private prompt','QA',{'tack_role':'implement'},parent=parent)
        self.addCleanup(lambda: process.stop() if process.poll() is None else None)
        self.addCleanup(lambda: workers['process'].wait(timeout=8))
        return process

    def test_real_worker_streams_json_and_preserves_nonzero_exit(self):
        p = self.launch('import json;print(json.dumps({"type":"turn.completed"}),flush=True);raise SystemExit(3)')
        folder = p.folder
        self.assertEqual(json.loads(p.readline())['type'],'turn.completed')
        self.assertEqual(p.wait(timeout=8),3)
        self.assertFalse((folder/'prompt').exists())
        p.close(); self.assertFalse(folder.exists())

    def test_owned_worker_can_be_stopped(self):
        p = self.launch('import json,time;print(json.dumps({"type":"thread.started"}),flush=True);time.sleep(60)')
        self.assertTrue(p.readline())
        p.stop()
        self.assertNotEqual(p.wait(timeout=8),0)
        p.close()

    def test_native_tab_is_created_in_the_original_project_space(self):
        p = self.launch('import json;print(json.dumps({"type":"turn.completed"}),flush=True)',parent='t12')
        self.assertTrue(p.readline());self.assertEqual(p.wait(timeout=8),0)
        command = next(c for c in self.commands if c[:3]==['herdr-app','do','tab.new'])
        self.assertEqual(command[command.index('--space')+1],'SPACE')
        self.assertEqual(p.address,'t1g')
        p.close()

    def test_expired_controller_lease_stops_the_worker(self):
        import time
        p = self.launch('import json,time;print(json.dumps({"type":"thread.started"}),flush=True);time.sleep(60)')
        self.assertTrue(p.readline())
        expired = time.time()-40
        os.utime(p.folder/'lease',(expired,expired))
        time.sleep(1.5)
        self.assertNotEqual(p.wait(timeout=8),0)
        p.close()
