import json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce
from agent_work.codex_app_server import AppServer

class WebOfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'work.sqlite3');self.work=Workforce(self.store,self.root)
        (self.root/'tests').mkdir();(self.root/'tests/test_calc.py').write_text('import unittest\nclass Test(unittest.TestCase):\n def test_ok(self):self.assertTrue(True)\n')
    def tearDown(self):
        for runtime in self.work.runtimes.values():runtime.close()
        self.tmp.cleanup()
    def fake(self,mode='normal'):
        binary=self.root/'bin';binary.mkdir();fake=binary/'codex';trace=self.root/'.tack-orchestration/trace.jsonl';trace.parent.mkdir(exist_ok=True)
        script="""import sys,json,uuid,time
from pathlib import Path
thread=None
for raw in sys.stdin:
 m=json.loads(raw);method=m.get('method');p=m.get('params',{})
 if 'id' not in m:continue
 with Path(TRACE).open('a') as f:f.write(json.dumps({'pid':__import__('os').getpid(),'method':method,'params':p})+'\\n')
 result={}
 if method in ('thread/start','thread/resume'):
  thread=p.get('threadId') or 'thread-'+str(uuid.uuid4());result={'thread':{'id':thread}}
 if method=='turn/start':
  turn=str(uuid.uuid4());result={'turn':{'id':turn}};print(json.dumps({'id':m['id'],'result':result}),flush=True)
  if MODE=='hang':continue
  job=json.loads(p['input'][0]['text'].split('Assigned job: ',1)[1].split('\\nColleagues:',1)[0]);r=dict(action='complete',summary='actual fixture output',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None)
  if job['kind']=='plan':r.update(action='propose',acceptance='tests pass',verification=[[sys.executable,'-m','unittest','discover','-s','tests']],tasks=[dict(title='계산기 구현',employee='implement',instructions='계산기 CLI 구현',files=['calculator.py'],depends_on=[])])
  elif job['kind']=='task':Path(p['cwd'],'calculator.py').write_text('print(2+2)')
  elif job['kind']=='review':r.update(action='review',verdict='approved')
  if MODE=='approval':print(json.dumps({'id':'approval-1','method':'item/commandExecution/requestApproval','params':{'command':['git','push'],'threadId':thread,'turnId':turn}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'threadId':thread,'turnId':turn,'item':{'type':'agentMessage','text':json.dumps(r)}}}),flush=True)
  if MODE!='unknown':print(json.dumps({'method':'thread/tokenUsage/updated','params':{'threadId':thread,'turnId':turn,'tokenUsage':{'last':{'inputTokens':100,'cachedInputTokens':50,'outputTokens':20}}}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':thread,'turn':{'id':turn,'status':'failed' if MODE=='failed' else 'completed'}}}),flush=True)
  continue
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
"""
        fake.write_text('#!'+sys.executable+'\nTRACE='+repr(str(trace))+'\nMODE='+repr(mode)+'\n'+script);fake.chmod(0o755);return binary,trace
    def configure(self):
        s=self.work.team.settings();s.update(runtime='codex-app-server',execution_mode='autopilot',autopilot=True);return self.work.team.save(s)
    def execute(self,mode='normal'):
        self.configure();goal=self.work.create('계산기 CLI');binary,trace=self.fake(mode)
        with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):self.work.run(goal['id'],goal['version'])
        return goal,self.work.snapshot(goal['id']),trace
    def test_app_server_runs_real_stdio_workflow_and_records_usage(self):
        goal,snapshot,trace=self.execute()
        self.assertEqual(snapshot['goal']['status'],'awaiting_acceptance',snapshot['goal'].get('error'))
        self.assertEqual(snapshot['goal']['token_used'],360)
        self.assertEqual((self.root/'calculator.py').read_text(),'print(2+2)')
        self.assertTrue(all(run['adapter']=='codex-app-server' for run in snapshot['runs']))
        messages=[json.loads(v) for v in trace.read_text().splitlines()]
        starts=[v for v in messages if v['method']=='turn/start'];self.assertEqual(len(starts),3)
        self.assertTrue(all(v['params']['approvalPolicy']=='never' for v in starts))
        self.assertTrue(any(v['params']['sandboxPolicy']['type']=='workspaceWrite' and v['params']['sandboxPolicy']['networkAccess'] is False for v in starts))
        self.assertEqual(self.work.runtimes,{})
    def test_failed_turn_cannot_become_success_even_with_valid_result(self):
        goal,snapshot,trace=self.execute('failed');self.assertEqual(snapshot['goal']['status'],'failed');self.assertFalse((self.root/'calculator.py').exists())
    def test_missing_usage_stops_automatic_followup(self):
        goal,snapshot,trace=self.execute('unknown');self.assertEqual(snapshot['goal']['error'],'usage_unknown');self.assertEqual(len(snapshot['runs']),1)
    def test_requested_employee_is_read_only_planner_and_invalid_employee_rejected(self):
        g=self.work.create('개발 직원에게 직접 지시',employee='implement');job=self.work.jobs(g['id'])[0]
        self.assertEqual(job['employee'],'implement');self.assertEqual(job['kind'],'plan');self.assertFalse(self.work.can_write(job,self.work.team.settings()['roles']['implement']))
        with self.assertRaises(BoardError):self.work.create('wrong',employee='unknown')
    def test_office_aggregation_marks_orphaned_work_for_recheck(self):
        g=self.work.create('오피스');job=self.work.jobs(g['id'])[0]
        with self.store.connect() as db:self.work.change(db,job['id'],{'status':'working'})
        office=self.work.office();person=next(e for e in office['employees'] if e['id']=='plan')
        self.assertEqual(person['state'],'needs_recheck');self.assertEqual(office['counts']['working'],0)
        self.assertEqual(person['current_job']['goal_id'],g['id'])
    def test_connection_reuses_process_and_thread_for_same_scoped_employee(self):
        settings=self.configure();g=self.work.create('같은 직원');g=self.work.orch.update(g['id'],{'worker':'owner','status':'running','execution_settings':settings,'deadline_at':time.time()+60},g['version'])
        binary,trace=self.fake();first=self.work.jobs(g['id'])[0]
        try:
            with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):
                a=self.work.employee_run(g,first,'owner')
                with self.store.connect() as db:second=self.work.insert(db,g['id'],employee='plan',title='후속 기획',instructions='추가 기획',kind='plan',status='queued')
                b=self.work.employee_run(g,self.work.job(g['id'],second),'owner')
            calls=[json.loads(v) for v in trace.read_text().splitlines()]
            self.assertEqual(len({v['pid'] for v in calls}),1);self.assertEqual(sum(v['method']=='initialize' for v in calls),1)
            self.assertTrue(any(v['method']=='thread/resume' for v in calls));self.assertEqual(a['status'],b['status'])
        finally:
            for runtime in self.work.runtimes.values():runtime.close()
    def test_pause_interrupts_owned_app_server_and_does_not_complete_job(self):
        settings=self.configure();g=self.work.create('중단');g=self.work.orch.update(g['id'],{'worker':'owner','status':'running','execution_settings':settings,'deadline_at':time.time()+60},g['version'])
        binary,trace=self.fake('hang');job=self.work.jobs(g['id'])[0];result=[]
        with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):
            thread=threading.Thread(target=lambda:result.append(self.work.employee_run(g,job,'owner')));thread.start()
            deadline=time.time()+5
            while time.time()<deadline and (not trace.exists() or 'turn/start' not in trace.read_text()):time.sleep(.02)
            current=self.work.goal(g['id']);self.work.orch.pause(g['id'],current['version']);thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(result[0]['status'],'interrupted')
        for runtime in self.work.runtimes.values():runtime.close()
    def test_silent_app_server_refreshes_heartbeat_without_notifications(self):
        from types import SimpleNamespace
        settings = self.configure()
        goal = self.work.create('silent worker')
        goal = self.work.orch.update(goal['id'], {'worker': 'owner', 'status': 'running', 'execution_settings': settings, 'heartbeat': time.time(), 'deadline_at': time.time()+120}, goal['version'])
        binary, trace = self.fake('hang')
        job = self.work.jobs(goal['id'])[0]
        offset = [0]
        clock = SimpleNamespace(monotonic=time.monotonic, time=lambda: time.time()+offset[0])
        result = []
        with patch.dict(os.environ, {'PATH': str(binary)+os.pathsep+os.environ['PATH']}), patch('agent_work.codex_app_server.time', clock), patch('agent_work.orchestration.time', clock):
            worker = threading.Thread(target=lambda: result.append(self.work.employee_run(goal, job, 'owner')))
            worker.start()
            try:
                deadline = time.monotonic()+5
                while time.monotonic()<deadline and (not trace.exists() or 'turn/start' not in trace.read_text()):
                    time.sleep(.02)
                self.assertIn('turn/start', trace.read_text())
                offset[0] = 40
                deadline = time.monotonic()+3
                while time.monotonic()<deadline and clock.time()-self.work.goal(goal['id'])['heartbeat']>2:
                    time.sleep(.02)
                self.assertLess(clock.time()-self.work.goal(goal['id'])['heartbeat'], 2)
                overview = self.work.orch.overview()
                current = next(g for g in overview['goals'] if g['id']==goal['id'])
                self.assertEqual(current['status'], 'running')
                self.assertTrue(worker.is_alive())
                self.assertEqual(overview['runs'][0]['event_count'], 0)
            finally:
                if worker.is_alive():
                    current = self.work.goal(goal['id'])
                    self.work.orch.pause(goal['id'], current['version'])
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result[0]['status'], 'interrupted')

    def test_office_filters_other_workspace_and_counts_final_acceptance(self):
        goal=self.work.create('current')
        self.work.orch.update(goal['id'],{'status':'awaiting_acceptance'},goal['version'])
        other=self.root/'other';other.mkdir();foreign=Workforce(self.store,other).create('foreign')
        office=self.work.office()
        self.assertEqual({j['goal_id'] for j in office['jobs']},{goal['id']})
        self.assertEqual(office['counts']['waiting'],1)
    def test_server_approval_request_is_explicitly_declined(self):
        import io,queue
        server=object.__new__(AppServer);server.messages=queue.Queue()
        server.messages.put({'id':'approval','method':'item/commandExecution/requestApproval','params':{}})
        replies=[];server.write=replies.append
        server.receive(lambda event:None,lambda:None,time.monotonic()+2)
        self.assertEqual(replies,[{'id':'approval','result':{'decision':'decline'}}])
    def test_unexpected_connection_exit_is_not_success(self):
        import queue
        server=object.__new__(AppServer);server.messages=queue.Queue();server.messages.put(None)
        with self.assertRaises(BoardError):server.receive(lambda event:None,lambda:None,time.monotonic()+2)
    def test_office_sprites_are_local_and_atlas_bounds_are_valid(self):
        import struct
        assets=Path(__file__).parents[1]/'agent_work/assets'
        raw=(assets/'office-art.js').read_text().split('window.TackOfficeArt=',1)[1]
        sprites,end=json.JSONDecoder().raw_decode(raw)
        if 'Object.assign(window.TackOfficeArt,' in raw[end:]:
            extra=raw[end:].split('Object.assign(window.TackOfficeArt,',1)[1]
            sprites.update(json.JSONDecoder().raw_decode(extra)[0])
        self.assertGreater(len(sprites),30)
        for sprite in sprites.values():
            self.assertTrue(sprite['url'].startswith('/office-'))
            self.assertNotIn('..',sprite['url'])
            path=assets/sprite['url'].lstrip('/')
            png=path.read_bytes();self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
            width,height=struct.unpack('>II',png[16:24])
            self.assertLessEqual(sprite.get('sx',0)+sprite['width'],width)
            self.assertLessEqual(sprite.get('sy',0)+sprite['height'],height)
    def test_office_read_does_not_create_state_tables(self):
        before=self.store.all();office=self.work.office();self.assertEqual(len(office['employees']),3);self.assertEqual(office['jobs'],[]);self.assertEqual(before,self.store.all())
