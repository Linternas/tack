import json, os, queue, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
from agent_work.session_office import SessionOffice, observed
from agent_work.store import Store, BoardError

PROFILE={'id':'a'*12,'home':'/test/codex','name':'test'}
THREAD='00000000-0000-4000-8000-000000000001'
KEY=PROFILE['id']+':'+THREAD

class Protocol:
    def __init__(self, approval=False, hang=False):
        self.calls=[];self.events=queue.Queue();self.closed=False;self.number=0;self.approval=approval;self.hang=hang
    def call(self,method,params,handle,cancel):
        self.calls.append((method,params))
        if method=='thread/list':return {'data':[{'id':THREAD,'name':'real thread','cwd':'/external/project','status':{'type':'active'}}],'nextCursor':'page2'}
        if method=='thread/read':return {'thread':{'id':params['threadId'],'name':'real thread'}}
        if method=='thread/start':return {'thread':{'id':THREAD,'model':'fixture'}}
        if method=='turn/start':
            self.number+=1
            if self.approval:self.events.put({'id':'permission','method':'item/commandExecution/requestApproval','params':{'command':'python3 test.py','threadId':THREAD}})
            elif not self.hang:self.complete()
            return {'turn':{'id':str(self.number)}}
        return {}
    def complete(self,status='completed'):
        self.events.put({'method':'item/completed','params':{'threadId':THREAD,'item':{'type':'agentMessage','text':'actual output'}}})
        self.events.put({'method':'turn/completed','params':{'threadId':THREAD,'turn':{'id':str(self.number),'status':status}}})
    def receive(self,handle,cancel,deadline):
        while True:
            cancel()
            if self.closed:raise BoardError('closed')
            try:value=self.events.get(timeout=.01)
            except queue.Empty:continue
            if 'id' in value:self.request_handler(value)
            else:handle(value)
            return {'notification':True}
    def send(self,method,params):
        self.calls.append((method,params))
        if method=='turn/interrupt':self.complete('interrupted')
    def write(self,value):
        self.calls.append(('reply',value))
        if value.get('id')=='permission':self.complete()
    def close(self):self.closed=True

class SessionOfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'work.sqlite3');self.office=SessionOffice(self.store,self.root)
        self.profile_patch=patch('agent_work.session_office.profiles',return_value={PROFILE['id']:PROFILE});self.profile_patch.start()
    def tearDown(self):self.office.close();self.profile_patch.stop();self.tmp.cleanup()
    def wait(self,state='idle'):
        end=time.monotonic()+2
        while time.monotonic()<end:
            current=self.office.running[KEY]['public']
            if current['state']==state:return current
            time.sleep(.01)
        self.fail(str(current))
    def start(self,protocol,team=None,role=None):
        with patch('agent_work.session_office.connect',return_value=protocol):
            return self.office.start({'profile':PROFILE['id'],'team_id':team,'role_id':role,'prompt':'Build calculator'})
    def test_catalog_is_metadata_only_readonly_and_external_status_is_not_claimed(self):
        server=Protocol()
        with patch('agent_work.session_office.connect',return_value=server):result=self.office.list(view='history',scope='all')
        row=result['sessions'][0]
        self.assertEqual(row['thread_id'],THREAD);self.assertFalse(row['controlled']);self.assertEqual(row['state'],'external')
        self.assertNotIn('turns',row);self.assertNotIn('_path',row);self.assertTrue(server.closed)
        with self.store.connect() as db:self.assertFalse(db.execute("select 1 from sqlite_master where name='office_sessions'").fetchone())
    def test_default_current_view_does_not_query_history(self):
        with patch('agent_work.session_office.connect',side_effect=AssertionError('history must not load')):
            result=self.office.list()
        self.assertEqual(result['sessions'],[]);self.assertEqual(result['next_cursors'],{});self.assertEqual(result['view'],'current');self.assertEqual(result['scope'],'project')
    def test_only_confirmed_current_project_work_and_user_wait_are_default(self):
        server=Protocol(hang=True);self.start(server);self.wait('working')
        original=self.office.running[KEY]
        for i,state in enumerate(('idle','failed','paused','awaiting_approval','waiting_user')):
            key=PROFILE['id']+':other'+str(i)
            raw={**original['raw'],'id':key,'thread_id':'other'+str(i)}
            self.office.running[key]={'raw':raw,'public':{**original['public'],'state':state},'server':Protocol(),'cancel':__import__('threading').Event()}
        foreign=PROFILE['id']+':foreign'
        self.office.running[foreign]={'raw':{**original['raw'],'id':foreign,'thread_id':'foreign','cwd':str(self.root)+'-other'},'public':{**original['public']},'server':Protocol(),'cancel':__import__('threading').Event()}
        with patch('agent_work.session_office.connect',side_effect=AssertionError('no history query')):result=self.office.list(scope='all')
        self.assertEqual({r['state'] for r in result['sessions']},{'working','awaiting_approval','waiting_user'})
        self.assertEqual(len(result['sessions']),3);self.assertNotIn(foreign,{r['id'] for r in result['sessions']})
        self.office.interrupt(KEY);self.wait('paused')
        self.assertNotIn(KEY,{r['id'] for r in self.office.list()['sessions']})
    def test_history_project_scope_and_all_scope_are_explicit(self):
        with patch('agent_work.session_office.connect',return_value=Protocol()):
            self.assertEqual(self.office.list(view='history')['sessions'],[])
            self.assertEqual(len(self.office.list(view='history',scope='all')['sessions']),1)
        with self.assertRaises(BoardError):self.office.list(view='bogus')
    def test_direct_session_executes_without_planner_goal_and_reuses_thread(self):
        server=Protocol();self.start(server);self.assertEqual(self.wait()['last_message'],'actual output')
        self.assertNotIn('goal coordinator',server.calls[0][1].get('developerInstructions',''));self.assertEqual(self.office.assignments()[KEY]['team_id'],None)
        self.office.send(KEY,{'prompt':'continue'});self.wait()
        self.assertEqual(sum(m=='thread/start' for m,p in server.calls),1)
        self.assertEqual(sum(m=='turn/start' for m,p in server.calls),2)
        with self.store.connect() as db:self.assertFalse(db.execute("select 1 from sqlite_master where name='goals'").fetchone())
    def test_preconfigured_role_instructions_and_skills_apply_and_plan_is_readonly(self):
        from agent_work.team import Team
        skill=self.root/'SKILL.md';skill.write_text('specific skill')
        team=Team(self.store,self.root);settings=team.settings();settings['roles']['plan']['skills']=[str(skill)];team.save(settings)
        server=Protocol();self.start(server,'default','plan');self.wait()
        start=next(p for m,p in server.calls if m=='thread/start');turn=next(p for m,p in server.calls if m=='turn/start')
        self.assertIn('specific skill',start['developerInstructions']);self.assertEqual(start['sandbox'],'read-only');self.assertEqual(turn['sandboxPolicy']['type'],'readOnly')
        self.assertEqual(self.office.assignments()[KEY]['role_id'],'plan')
    def test_optional_membership_version_conflict_and_no_role_without_team(self):
        server=Protocol();self.start(server);self.wait()
        result=self.office.save(KEY,{'version':1,'team_id':'default','role_id':'implement'})
        self.assertEqual(result['version'],2)
        with self.assertRaises(BoardError) as error:self.office.save(KEY,{'version':1,'team_id':None,'role_id':None})
        self.assertEqual(error.exception.status,409)
        self.office.save(KEY,{'version':2,'team_id':None,'role_id':None})
        with self.assertRaises(BoardError):self.office.save(KEY,{'version':3,'team_id':None,'role_id':'plan'})
    def test_cannot_send_or_interrupt_external_or_invalid_profile(self):
        for call in (lambda:self.office.send(KEY,{'prompt':'run'}),lambda:self.office.interrupt(KEY),lambda:self.office.list('not-a-profile'),lambda:self.office.find('../../auth.json')):
            with self.assertRaises(BoardError):call()
    def test_approval_is_explicit_and_stale_approval_rejected(self):
        server=Protocol(approval=True);self.start(server);self.wait('awaiting_approval')
        with self.assertRaises(BoardError):self.office.send(KEY,{'prompt':'duplicate'})
        with self.assertRaises(BoardError):self.office.approve(KEY,{'request_id':'stale','decision':'accept'})
        self.office.approve(KEY,{'request_id':'permission','decision':'accept'});self.wait()
        self.assertTrue(any(m=='reply' and p['result']['decision']=='accept' for m,p in server.calls))
    def test_simultaneous_approval_requests_are_queued_and_interrupt_declines_remaining(self):
        server=Protocol(hang=True);self.start(server);self.wait('working')
        for key in ('first','second'):
            server.request_handler({'id':key,'method':'item/commandExecution/requestApproval','params':{'command':key}})
        self.assertEqual(self.office.running[KEY]['public']['approval']['id'],'first')
        self.office.approve(KEY,{'request_id':'first','decision':'accept'})
        self.assertEqual(self.office.running[KEY]['public']['approval']['id'],'second')
        self.office.interrupt(KEY);self.wait('paused')
        self.assertTrue(any(m=='reply' and p['id']=='second' and p['result']['decision']=='decline' for m,p in server.calls))
    def test_direct_session_respects_project_write_paths(self):
        from agent_work.team import Team
        (self.root/'src').mkdir();team=Team(self.store,self.root);settings=team.settings();settings['permissions']['write_paths']=['src'];team.save(settings)
        server=Protocol();self.start(server);self.wait()
        start=next(p for m,p in server.calls if m=='thread/start');turn=next(p for m,p in server.calls if m=='turn/start')
        self.assertEqual(start['cwd'],str(self.root.resolve()/'src'))
        self.assertEqual(turn['sandboxPolicy']['writableRoots'],[str(self.root.resolve()/'src')])
    def test_interrupt_does_not_claim_task_completed(self):
        server=Protocol(hang=True);self.start(server);self.wait('working');self.office.interrupt(KEY);self.wait('paused')
        self.assertTrue(any(m=='turn/interrupt' for m,p in server.calls))
    def test_external_profile_failure_is_visible_not_empty_success(self):
        with patch('agent_work.session_office.connect',side_effect=BoardError('unavailable')):result=self.office.list(view='history',scope='all')
        self.assertEqual(result['sessions'],[]);self.assertEqual(result['errors'][0]['message'],'unavailable')
    def test_observation_reads_only_allowed_lifecycle_and_never_follows_outside_home(self):
        home=self.root/'profile';folder=home/'sessions';folder.mkdir(parents=True);path=folder/'thread.jsonl'
        path.write_text('\n'.join(json.dumps(v) for v in [{'type':'event_msg','timestamp':'now','payload':{'type':'task_started','private':'secret'}},{'type':'response_item','payload':{'text':'secret'}}]))
        self.assertEqual(observed({'path':str(path)},{'home':str(home)}),{'kind':'task_started','at':'now'})
        self.assertIsNone(observed({'path':str(path)},{'home':str(self.root/'other')}))

    def managed_project(self):
        self.state_patch = patch.dict(os.environ, {'AGENT_WORK_HOME': str(self.root/'state')})
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        from agent_work.projects import resolve
        workspace = self.root/'project'
        workspace.mkdir()
        self.project = resolve(workspace, create=True)
        self.office.close()
        self.store = Store(self.project['db_path'])
        self.office = SessionOffice(self.store, workspace)

    def test_archive_waits_for_direct_session_interruption(self):
        self.managed_project()
        from agent_work.project_lifecycle import change, preview
        server = Protocol(hang=True)
        self.start(server)
        self.wait('working')
        self.assertFalse(preview(self.project)['can_archive'])
        with self.assertRaises(BoardError) as error:
            change(self.project, 'archive', {'version': 1})
        self.assertEqual(error.exception.status, 409)
        self.office.interrupt(KEY)
        self.office.running[KEY]['worker'].join(2)
        self.assertFalse(self.office.running[KEY]['worker'].is_alive())
        self.assertTrue(preview(self.project)['can_archive'])
        change(self.project, 'archive', {'version': 1})
        change(self.project, 'delete', {'version': 2, 'confirmation': self.project['name'], 'include_backups': False})
        self.assertTrue(server.closed or self.office.running[KEY]['public']['state'] == 'paused')

    def test_approval_wait_blocks_archive_until_connection_stops(self):
        self.managed_project()
        from agent_work.project_lifecycle import change
        self.start(Protocol(approval=True))
        self.wait('awaiting_approval')
        with self.assertRaises(BoardError):
            change(self.project, 'archive', {'version': 1})
        self.office.close()
        with self.store.connect() as db:
            state = json.loads(db.execute('SELECT data FROM office_executions WHERE id=?', (KEY,)).fetchone()[0])
        self.assertNotEqual(state['status'], 'running')
        change(self.project, 'archive', {'version': 1})

    def test_worker_start_failure_releases_execution(self):
        self.managed_project()
        from agent_work.project_lifecycle import change
        server = Protocol(hang=True)
        with patch('agent_work.session_office.threading.Thread.start', side_effect=RuntimeError('cannot start')):
            with self.assertRaises(RuntimeError):
                self.start(server)
        self.assertTrue(server.closed)
        self.assertNotIn(KEY, self.office.running)
        change(self.project, 'archive', {'version': 1})

    def test_crashed_process_group_requires_proof_of_exit(self):
        from agent_work.project_lifecycle import office_execution_active
        record = {'status': 'running', 'process_group': 12345}
        with patch('agent_work.project_lifecycle.os.killpg'):
            self.assertTrue(office_execution_active(record))
        with patch('agent_work.project_lifecycle.os.killpg', side_effect=PermissionError):
            self.assertTrue(office_execution_active(record))
        with patch('agent_work.project_lifecycle.os.killpg', side_effect=ProcessLookupError):
            self.assertFalse(office_execution_active(record))
        self.assertTrue(office_execution_active({'status': 'running'}))

class SessionOfficeHttpTests(unittest.TestCase):
    def setUp(self):
        import threading
        from agent_work.http import BoardServer
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'work.sqlite3')
        self.server=BoardServer(('127.0.0.1',0),self.store,{'id':'test','workspace':str(self.root),'name':'Test'},harness=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.enabled=patch('agent_work.harness.require_enabled');self.enabled.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.enabled.stop();self.tmp.cleanup()
    def request(self,path,body=None,headers=None):
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        connection.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),{'Content-Type':'application/json',**(headers or {})})
        response=connection.getresponse();result=response.status,json.loads(response.read());connection.close();return result
    def test_core_never_exposes_codex_session_api(self):
        self.server.harness=False
        self.assertEqual(self.request('/api/session-office')[0],404)
    def test_readonly_and_cross_origin_block_mutation_before_runtime(self):
        self.assertEqual(self.request('/api/session-office/start',{})[0],403)
        self.server.allow_execution=True
        self.assertEqual(self.request('/api/session-office/start',{}, {'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.server.session_offices,{})
    def test_catalog_and_new_asset_route_available_only_in_harness(self):
        with patch.object(SessionOffice,'list',return_value={'sessions':[],'profiles':[],'errors':[],'next_cursors':{}}):
            status,result=self.request('/api/session-office')
        self.assertEqual(status,200);self.assertEqual(result['workspace'],str(self.root.resolve()));self.assertFalse(result['execution_enabled'])
