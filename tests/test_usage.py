import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from agent_work.store import Store, BoardError
from agent_work.usage import Usage
from agent_work.usage_adapter import codex_usage
from agent_work.orchestration import Orchestrator
from agent_work.http import BoardServer


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'work.sqlite3');self.task=self.store.write({'title':'usage'})['task'];self.usage=Usage(self.store)
    def tearDown(self):self.temp.cleanup()
    def start(self,key='RUN-A',**kwargs):self.usage.start(key,task_id=self.task['id'],**kwargs)

    def test_empty_readonly_does_not_migrate_and_unknown_is_not_zero(self):
        before=self.store.path.read_bytes()
        overview=Usage(Store(self.store.path,readonly=True)).overview()
        self.assertIsNone(overview['summary']['total_tokens']);self.assertEqual(overview['runs'],[])
        self.assertEqual(before,self.store.path.read_bytes())
        self.start();self.assertIsNone(self.usage.overview()['summary']['input_tokens'])
        self.usage.turn('RUN-A',{});row=self.usage.overview()['runs'][0]
        self.assertEqual(row['turns'],1);self.assertIsNone(row['total_tokens']);self.assertIsNone(row['cached_input_tokens'])

    def test_multiple_turns_cache_subset_and_missing_coverage(self):
        self.start(goal_id='G',role='implement',model='test',adapter='fake')
        self.usage.turn('RUN-A',codex_usage({'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}))
        self.usage.turn('RUN-A',codex_usage({'input_tokens':50,'input_tokens_details':{'cached_tokens':20},'output_tokens':5}))
        self.usage.finish('RUN-A','succeeded',1000)
        summary=self.usage.overview()['summary']
        self.assertEqual(summary['total_tokens'],165);self.assertEqual(summary['cached_input_tokens'],100)
        self.assertAlmostEqual(summary['cache_ratio'],100/150);self.assertEqual(summary['turns'],2)
        self.start('RUN-B',goal_id='G',role='verify',phase='revision',round_number=1)
        self.usage.turn('RUN-B',{'input_tokens':1000,'output_tokens':20})
        summary=self.usage.overview()['summary']
        self.assertAlmostEqual(summary['cache_ratio'],100/150);self.assertEqual(summary['cache_measured_runs'],1)
        self.assertEqual(summary['total_tokens'],1185)
        self.assertEqual(len(self.usage.overview(role='implement')['runs']),1)
        self.assertEqual(self.usage.overview(goal='absent')['runs'],[])
        self.assertEqual({r['name'] for r in self.usage.overview()['groups']['phase']},{'initial','revision'})

    def test_invalid_values_and_partial_turn_do_not_become_zero(self):
        self.start();self.usage.turn('RUN-A',{'input_tokens':0,'cached_input_tokens':0,'output_tokens':0})
        self.assertEqual(self.usage.overview()['summary']['total_tokens'],0)
        self.usage.turn('RUN-A',{'input_tokens':True,'cached_input_tokens':-1,'output_tokens':3.5})
        self.assertIsNone(self.usage.overview()['summary']['total_tokens'])
        self.assertEqual(codex_usage({'input_tokens':True})['input_tokens'],None)
        self.start('RUN-B');self.usage.turn('RUN-B',{'input_tokens':10,'cached_input_tokens':11,'output_tokens':2})
        self.assertIsNone(next(r for r in self.usage.overview()['runs'] if r['id']=='RUN-B')['cached_input_tokens'])
        with self.assertRaises(BoardError):self.usage.finish('RUN-A','done',-1)

    def test_legacy_rows_preserved_not_duplicated_or_backfilled(self):
        orch=Orchestrator(self.store,self.root)
        goal=orch.create('legacy',{'acceptance':'pass','steps':[{'title':'work','role':'implement','prompt':'work'}],'verification':[[sys.executable,'-c','pass']]})
        with self.store.connect() as db:
            db.execute('INSERT INTO executions VALUES (?,?,?,?,?,?)',('OLD',goal['id'],goal['steps'][0]['task_id'],json.dumps({'tokens':400,'role':'implement','status':'succeeded'}),'2026-01-01','2026-01-01'))
        before=self.store.path.read_bytes();result=self.usage.overview()
        self.assertEqual(result['summary']['total_tokens'],400);self.assertIsNone(result['summary']['input_tokens']);self.assertEqual(before,self.store.path.read_bytes())
        run,_=orch.run(goal,goal['steps'][0]);self.usage.turn(run,{'input_tokens':10,'output_tokens':2})
        self.assertEqual(self.usage.overview()['summary']['total_tokens'],412)
        self.assertEqual(len(self.usage.overview()['runs']),2)
        self.assertEqual(self.store.detail(self.task['id'])['task']['version'],1)

    def test_real_process_usage_and_existing_budget_accounting(self):
        orch=Orchestrator(self.store,self.root)
        goal=orch.create('events',{'acceptance':'pass','steps':[{'title':'verify','role':'verify','prompt':'work'}],'verification':[[sys.executable,'-c','pass']]})
        goal=orch.update(goal['id'],{'worker':'test'},goal['version']);run,_=orch.run(goal,goal['steps'][0])
        events=[{'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}},{'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'passed':True,'summary':'checked'})}},{'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':10,'output_tokens':5}}]
        script='import json,sys;sys.stdin.read();events='+repr(events)+';[print(json.dumps(e)) for e in events]'
        result=orch.process([sys.executable,'-c',script],self.root,goal['id'],'test',run,'test')
        self.assertEqual(result['status'],'succeeded');self.assertEqual(self.usage.overview()['summary']['total_tokens'],135)
        self.assertEqual(orch.goal(goal['id'])['token_used'],135)
        self.assertEqual(self.usage.overview()['summary']['cached_input_tokens'],90)

    def test_api_core_without_harness_and_error_boundary(self):
        self.start();self.usage.turn('RUN-A',{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2})
        server=BoardServer(('127.0.0.1',0),self.store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path in ['/api/usage','/api/usage?role=absent','/usage','/usage.js','/usage.css']:
                client=http.client.HTTPConnection('127.0.0.1',server.server_port);client.request('GET',path);res=client.getresponse();body=res.read();client.close();self.assertEqual(res.status,200,path)
                if path=='/api/usage':self.assertEqual(json.loads(body)['summary']['total_tokens'],12)
                if path.endswith('role=absent'):self.assertEqual(json.loads(body)['runs'],[])
            client=http.client.HTTPConnection('127.0.0.1',server.server_port);client.request('POST','/api/usage',body='{}',headers={'Content-Type':'application/json'});res=client.getresponse();res.read();client.close();self.assertEqual(res.status,404)
        finally:server.shutdown();server.server_close();thread.join()

    def test_run_phase_handles_cleared_requests_retry_and_question(self):
        orch=Orchestrator(self.store,self.root)
        goal=orch.create('phases',{'acceptance':'pass','steps':[{'title':'work','role':'implement','prompt':'work'}],'verification':[[sys.executable,'-c','pass']]})
        goal['pending_request']=None
        orch.run(goal,goal['steps'][0])
        orch.run(goal,goal['steps'][0])
        goal['pending_request']={'kind':'revision'}
        orch.run(goal,goal['steps'][0])
        orch.run(goal,goal['steps'][0],mode='question')
        self.assertEqual({r['phase'] for r in self.usage.overview()['runs']},{'initial','retry','revision','question'})
