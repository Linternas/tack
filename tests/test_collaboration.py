import hashlib,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce
from agent_work.collaboration import Collaboration


def result(action='complete',data=None,**fields):
    return {'status':'succeeded','exit_code':0,'team_result':{'action':action,'summary':'actual fixture evidence','target':None,'body':'','tasks':[],'acceptance':'','verification':[],'verdict':None,**({'collaboration':data} if data is not None else {}),**fields}}

class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'db.sqlite3');self.w=Workforce(self.store,self.root);self.c=Collaboration(self.w)
        (self.root/'api.json').write_text('{"fields": []}');(self.root/'backend.py').write_text('value = 1\n');(self.root/'frontend.py').write_text('value = 0\n')
        settings=self.w.team.settings();settings['roles']['backend']={**settings['roles']['implement'],'name':'백엔드'};self.w.team.save(settings)
        self.verify=[[sys.executable,'-c','import json;from pathlib import Path;assert isinstance(json.loads(Path("api.json").read_text())["fields"],list)']]
        self.goal=self.w.create('catalog',acceptance='approved catalog scope',verification_text=' '.join(self.verify[0][:2])+" '"+self.verify[0][2]+"'")
        seed=self.w.jobs(self.goal['id'])[0]
        with self.store.connect() as db:
            self.w.change(db,seed['id'],{'status':'done','result':'fixture plan'})
            self.fe=self.w.insert(db,self.goal['id'],title='frontend',employee='implement',instructions='implement approved catalog',files=['frontend.py'])
        self.w.approve(self.goal['id'],self.w.goal(self.goal['id'])['version'])
    def tearDown(self):self.tmp.cleanup()
    def grant(self,**changes):
        p={'version':self.w.goal(self.goal['id'])['version'],'mode':'delegated','requirements':['catalog'],'files_by_role':{'implement':['frontend.py'],'backend':['backend.py','api.json']},'documents':{'api.json':{'owner':'backend','consumers':['implement']}},'verification':self.verify,'max_tokens':10000,'max_minutes':20,'max_rounds':2,'max_parallel':3,'role_tokens':{},**changes}
        return self.c.save_policy(self.goal['id'],p)
    def request_data(self,**changes):return {'to_role':'backend','title':'add catalog fields','instructions':'add stock field within approved catalog','acceptance':['stock field documented and verified'],'files':['backend.py','api.json'],'requirements':['catalog'],'change_type':'additive','contract_path':'api.json','expected_revision':hashlib.sha256((self.root/'api.json').read_bytes()).hexdigest(),'wait_for':'contract_confirmed','verification':self.verify,'idempotency_key':'catalog-fields-v1',**changes}
    def run_team(self,fn):
        with patch.object(self.w,'employee_run',side_effect=fn):self.w.run(self.goal['id'],self.w.goal(self.goal['id'])['version'])
        return self.w.snapshot(self.goal['id'])
    def execute(self,goal,job,worker):
        root=Path(job['execution_workspace'])
        if job['kind']=='review':return result('review',verdict='approved')
        if job['kind']=='contract_review':
            r=next(r for r in self.c.snapshot(goal['id'])['revisions'] if r['id']==job['revision_id'])
            self.assertEqual(json.loads((root/'api.json').read_text())['fields'],['stock'])
            self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],[])
            return result('contract_review',{'revision_id':r['id'],'sha256':r['sha256'],'verdict':'approved','summary':'actual consumer check'})
        if job['employee']=='backend':
            (root/'api.json').write_text('{"fields": ["stock"]}');(root/'backend.py').write_text('value = 2\n')
            return result('contract_proposal',{'path':'api.json','change_type':'additive','summary':'stock contract implemented'})
        if not job['replies']:return result('work_request',self.request_data())
        self.assertEqual(json.loads((root/'api.json').read_text())['fields'],['stock']);(root/'frontend.py').write_text('value = 2\n');return result()
    def test_request_contract_consumer_review_resume_and_final_user_acceptance(self):
        self.grant();snap=self.run_team(self.execute)
        self.assertEqual(snap['goal']['status'],'awaiting_acceptance',[(j['title'],j['status'],j['result']) for j in snap['jobs']])
        self.assertEqual(snap['collaboration']['revisions'][0]['status'],'confirmed');self.assertEqual(snap['collaboration']['requests'][0]['status'],'completed')
        self.assertEqual((self.root/'frontend.py').read_text(),'value = 2\n');self.assertTrue(all(j['status']=='done' for j in snap['jobs']))
        self.w.accept(self.goal['id'],snap['goal']['version']);self.assertEqual(self.w.goal(self.goal['id'])['status'],'done')
    def claim_fe(self):
        g=self.w.goal(self.goal['id']);self.w.orch.update(g['id'],{'worker':'owner','status':'running','execution_settings':self.w.team.settings(),'pause_requested':False},g['version']);return self.c.claim(g['id'],self.fe,'owner')
    def test_request_duplicate_is_stable_and_changed_content_is_rejected(self):
        self.grant();job=self.claim_fe();data=self.request_data();r=self.c.request(self.goal['id'],self.fe,data,job['attempt_id'],'owner')
        # Replay is checked before changing the waiting requester's state.
        with self.store.connect() as db:self.w.change(db,self.fe,{'status':'working'})
        duplicate=self.c.request(self.goal['id'],self.fe,data,job['attempt_id'],'owner');self.assertEqual(r['id'],duplicate['id'])
        with self.assertRaises(BoardError):self.c.request(self.goal['id'],self.fe,{**data,'title':'different'},job['attempt_id'],'owner')
        self.assertEqual(len(self.c.snapshot(self.goal['id'])['requests']),1)
    def test_outside_requirements_and_breaking_change_require_user_approval(self):
        self.grant();job=self.claim_fe();r=self.c.request(self.goal['id'],self.fe,self.request_data(requirements=['payments'],change_type='breaking'),job['attempt_id'],'owner')
        self.assertEqual(r['status'],'awaiting_approval');self.assertEqual(self.w.job(self.goal['id'],r['job_id'])['status'],'awaiting_approval')
    def test_old_attempt_and_paused_goal_cannot_publish(self):
        self.grant();job=self.claim_fe()
        with self.store.connect() as db:self.w.change(db,self.fe,{'attempt_id':'replacement'})
        with self.assertRaises(BoardError):self.c.request(self.goal['id'],self.fe,self.request_data(),job['attempt_id'],'owner')
        self.assertEqual(self.c.snapshot(self.goal['id'])['requests'],[])
        g=self.w.goal(self.goal['id']);self.w.orch.pause(g['id'],g['version'])
        with self.assertRaises(BoardError):self.c.check_attempt(g['id'],self.fe,'replacement','owner')
    def test_policy_cannot_expand_team_limits_or_allow_unknown_roles(self):
        for changes in ({'max_parallel':9},{'max_tokens':2000001},{'files_by_role':{'unknown':['backend.py']}},{'verification':[]}):
            with self.assertRaises(BoardError):self.grant(**changes)
    def test_events_are_durable_and_idempotent(self):
        self.grant()
        with self.store.connect() as db:self.c.event(db,self.goal['id'],'task_unblocked',{'job_id':self.fe},'same');self.c.event(db,self.goal['id'],'task_unblocked',{'job_id':self.fe},'same')
        other=Collaboration(Workforce(Store(self.store.path),self.root));other.deliver(self.goal['id']);other.deliver(self.goal['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collaboration_events WHERE event_key='same'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM team_messages WHERE kind='task_unblocked'").fetchone()[0],1)
    def test_implementation_and_integration_conditions_wait_for_actual_verification(self):
        self.grant();original=self.execute
        def execute(g,j,w):
            if j['employee']=='implement' and j['kind']=='task' and not j['replies']:return result('work_request',self.request_data(wait_for='integration_verified'))
            if j['employee']=='implement' and j['kind']=='task' and j['replies']:
                request=self.c.snapshot(g['id'])['requests'][0]
                self.assertTrue(request['integration_verified']);self.assertEqual(request['status'],'completed')
            return original(g,j,w)
        snap=self.run_team(execute)
        self.assertEqual(snap['goal']['status'],'awaiting_acceptance')
        self.assertEqual(len([j for j in snap['jobs'] if j['kind']=='integration']),1)
        self.assertTrue(snap['collaboration']['requests'][0]['integration_verified'])
    def test_changed_contract_during_consumer_review_preserves_artifact(self):
        self.grant();original=self.execute
        def execute(g,j,w):
            value=original(g,j,w)
            if j['kind']=='contract_review':(self.root/'api.json').write_text('{"fields": ["external"]}')
            return value
        snap=self.run_team(execute)
        self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],['external'])
        self.assertEqual(snap['collaboration']['revisions'][0]['status'],'conflict')
        self.assertEqual(self.w.job(self.goal['id'],self.fe)['status'],'waiting_conditions')
        be=next(j for j in snap['jobs'] if j['employee']=='backend');self.assertEqual(be['status'],'conflict');self.assertTrue(be['staged'])
    def test_publication_crash_recovery_does_not_repeat_implementation(self):
        self.grant();calls=[];original=self.execute
        def execute(g,j,w):calls.append((j['employee'],j['kind']));return original(g,j,w)
        with patch.object(Collaboration,'finish_publication',side_effect=RuntimeError('simulated crash after file integration')):snap=self.run_team(execute)
        self.assertEqual(snap['collaboration']['revisions'][0]['status'],'publishing')
        self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],['stock'])
        # Root worker has exited; explicit next run reconciles the persisted publication journal.
        snap=self.run_team(execute)
        self.assertEqual(snap['goal']['status'],'awaiting_acceptance',[(j['status'],j['result']) for j in snap['jobs']])
        self.assertEqual(calls.count(('backend','task')),1)
    def test_revocation_and_document_pin_changes_prevent_resume(self):
        self.grant();job=self.claim_fe();r=self.c.request(self.goal['id'],self.fe,self.request_data(),job['attempt_id'],'owner')
        g=self.w.goal(self.goal['id']);self.c.revoke(g['id'],{'version':g['version']});self.c.synchronize(g['id'])
        self.assertEqual(self.w.job(g['id'],self.fe)['status'],'waiting_conditions')
        self.assertTrue(self.c.allowed(g['id'],self.w.job(g['id'],r['job_id'])))
        with self.assertRaises(Exception):self.c.check_pins(g['id'],{'contract_pins':[{'path':'api.json','sha256':'wrong'}]})
    def test_same_fulfillment_reuses_existing_job_and_detects_cycle(self):
        self.grant();first=self.claim_fe();r1=self.c.request(self.goal['id'],self.fe,self.request_data(),first['attempt_id'],'owner')
        with self.store.connect() as db:
            other=self.w.insert(db,self.goal['id'],title='second frontend work',employee='implement',instructions='same requirement',files=['frontend.py'],status='queued')
        second=self.c.claim(self.goal['id'],other,'owner');r2=self.c.request(self.goal['id'],other,self.request_data(idempotency_key='other-request'),second['attempt_id'],'owner')
        self.assertEqual(r1['job_id'],r2['job_id']);self.assertEqual(r2['canonical_request_id'],r1['id'])
        with self.store.connect() as db:
            self.w.change(db,r1['job_id'],{'depends_on':[other]})
            with self.assertRaises(BoardError):self.c.check_cycle(db,self.goal['id'],other,r1['job_id'])
    def test_rejected_contract_requires_approval_and_no_blind_repeat(self):
        self.grant();original=self.execute
        def execute(g,j,w):
            if j['kind']=='contract_review':
                r=next(r for r in self.c.snapshot(g['id'])['revisions'] if r['id']==j['revision_id'])
                return result('contract_review',{'revision_id':r['id'],'sha256':r['sha256'],'verdict':'changes_requested','summary':'consumer requires compatibility tests'})
            return original(g,j,w)
        snap=self.run_team(execute);be=next(j for j in snap['jobs'] if j['employee']=='backend')
        self.assertEqual(be['status'],'awaiting_approval');self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],[])
        self.w.approve(self.goal['id'],snap['goal']['version']);snap=self.run_team(execute)
        be=next(j for j in snap['jobs'] if j['employee']=='backend');self.assertEqual(be['status'],'waiting_user')
        self.assertIn('진전',be['question']);self.assertEqual(len(snap['collaboration']['revisions']),1)
    def test_publication_wrong_digest_and_outside_fields_are_rejected(self):
        self.grant();original=self.execute
        def execute(g,j,w):
            value=original(g,j,w)
            if j['kind']=='contract_review':value['team_result']['collaboration']['sha256']='not-reviewed'
            return value
        snap=self.run_team(execute)
        self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],[])
        self.assertNotEqual(snap['collaboration']['revisions'][0]['status'],'confirmed')
        job=self.w.job(self.goal['id'],self.fe)
        with self.assertRaises(BoardError):self.c.request(self.goal['id'],self.fe,{**self.request_data(),'deploy':True},job['attempt_id'],'owner')
    def test_manual_request_approval_and_failing_checks_never_confirm(self):
        self.grant(mode='manual');original=self.execute;snap=self.run_team(original)
        r=snap['collaboration']['requests'][0];self.assertEqual(r['status'],'awaiting_approval')
        self.c.approve_request(self.goal['id'],{'version':snap['goal']['version'],'request_id':r['id'],'request_version':r['version']})
        def execute(g,j,w):
            value=original(g,j,w)
            if j['employee']=='backend':(Path(j['execution_workspace'])/'api.json').write_text('invalid json')
            return value
        snap=self.run_team(execute)
        self.assertFalse(snap['collaboration']['revisions']);self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],[])
    def test_pm_can_propose_only_owned_approved_document(self):
        self.grant(files_by_role={'plan':['api.json'],'implement':['frontend.py']},documents={'api.json':{'owner':'plan','consumers':['implement']}})
        with self.store.connect() as db:self.w.change(db,self.fe,{'status':'done','result':'fixture'})
        g=self.w.goal(self.goal['id']);snap=self.w.assign(g['id'],{'version':g['version'],'title':'requirements document','employee':'plan','instructions':'approved document change','files':['api.json'],'depends_on':[],'client_id':'pm-doc'})
        doc=next(j for j in snap['jobs'] if j['title']=='requirements document');self.assertEqual(doc['kind'],'document')
        self.w.approve(g['id'],snap['goal']['version'])
        def execute(goal,job,worker):
            if job['kind']=='document':
                (Path(job['execution_workspace'])/'api.json').write_text('{"fields": ["stock"]}')
                return result('contract_proposal',{'path':'api.json','change_type':'additive','summary':'approved PM document proposal'})
            return self.execute(goal,job,worker)
        snap=self.run_team(execute)
        self.assertEqual(snap['goal']['status'],'awaiting_acceptance',[(j['title'],j['status'],j['result']) for j in snap['jobs']])
        self.assertEqual(snap['collaboration']['revisions'][0]['owner'],'plan')
    def test_expired_delegation_and_changed_goal_do_not_dispatch(self):
        self.grant();p=self.c.snapshot(self.goal['id'])['policy']
        with self.store.connect() as db:self.c.update(db,p['id'],{'expires_at':0})
        with patch.object(self.w,'employee_run',side_effect=AssertionError('expired')):self.w.run(self.goal['id'],self.w.goal(self.goal['id'])['version'])
        self.assertEqual(self.w.goal(self.goal['id'])['status'],'paused')
        self.grant();job=self.w.job(self.goal['id'],self.fe)
        with self.store.connect() as db:self.w.change(db,self.fe,{'approval_id':self.c.policy(db,self.goal['id'])['id']})
        g=self.w.goal(self.goal['id']);self.w.orch.update(g['id'],{'acceptance':'changed scope'},g['version'])
        self.assertTrue(self.c.allowed(g['id'],self.w.job(g['id'],self.fe)))
    def test_role_budget_and_unknown_request_fields_are_rejected(self):
        self.grant(role_tokens={'implement':10})
        from agent_work.usage import Usage
        usage=Usage(self.store);usage.start('budget-run',task_id=self.fe,goal_id=self.goal['id'],role='implement');usage.turn('budget-run',{'input_tokens':10,'cached_input_tokens':0,'output_tokens':1})
        self.assertEqual(self.c.budget(self.goal['id'],'implement'),'role_token_limit')
        job=self.claim_fe()
        for values in ({'wait_for':[]},{'change_type':{}},{'from_role':'admin'}):
            with self.assertRaises(BoardError):self.c.request(self.goal['id'],self.fe,{**self.request_data(),**values},job['attempt_id'],'owner')
    def test_pending_events_do_not_resume_a_manually_paused_requester(self):
        self.grant();original=self.execute
        def execute(g,j,w):
            value=original(g,j,w)
            if j['kind']=='contract_review':
                current=self.w.goal(g['id']);self.w.orch.pause(g['id'],current['version'])
            return value
        snap=self.run_team(execute);self.assertEqual(snap['goal']['status'],'paused')
        self.assertEqual(json.loads((self.root/'api.json').read_text())['fields'],[])
        self.assertEqual(self.w.job(self.goal['id'],self.fe)['status'],'waiting_conditions')
    def test_contract_autopilot_obeys_goal_delegation_round_limit(self):
        settings=self.w.team.settings();settings['autopilot']=True;settings['max_rounds']=3;self.w.team.save(settings)
        self.grant(max_rounds=1);calls=[];original=self.execute
        def execute(g,j,w):
            if j['kind']=='contract_review':
                r=next(r for r in self.c.snapshot(g['id'])['revisions'] if r['id']==j['revision_id'])
                return result('contract_review',{'revision_id':r['id'],'sha256':r['sha256'],'verdict':'changes_requested','summary':'need another actual improvement'})
            value=original(g,j,w)
            if j['employee']=='backend':
                calls.append(j['id']);(Path(j['execution_workspace'])/'api.json').write_text(json.dumps({'fields':['stock'],'iteration':len(calls)}))
            return value
        snap=self.run_team(execute);self.assertEqual(len(calls),2);self.assertEqual(snap['goal']['auto_rounds'],1)
        self.assertEqual(next(j for j in snap['jobs'] if j['employee']=='backend')['status'],'awaiting_approval')
    def test_http_policy_routes_require_execution_and_reject_stale_versions(self):
        import http.client,threading
        from agent_work.http import BoardServer
        self.grant();policy=self.c.snapshot(self.goal['id'])['policy'];body={k:policy[k] for k in ('mode','requirements','files_by_role','documents','verification','max_tokens','max_minutes','max_rounds','max_parallel','role_tokens')};body['version']=self.w.goal(self.goal['id'])['version']
        project={'id':'test','name':'fixture','workspace':str(self.root),'db_path':str(self.store.path),'source_roots':[]}
        folder=self.root/'.tack-orchestration';folder.mkdir(exist_ok=True);(folder/'install.json').write_text(json.dumps({'format':1,'workspace':str(self.root.resolve())}))
        def request(server,payload):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/api/workforce/'+self.goal['id']+'/delegate',json.dumps(payload),{'Content-Type':'application/json'});r=c.getresponse();data=r.read();status=r.status;c.close();return status,data
        for enabled in (False,True):
            server=BoardServer(('127.0.0.1',0),self.store,project,allow_execution=enabled,harness=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch.object(Collaboration,'kick',return_value=None):status,_=request(server,body)
                self.assertEqual(status,200 if enabled else 403)
                if enabled:
                    with patch.object(Collaboration,'kick',side_effect=AssertionError('stale request')):self.assertEqual(request(server,body)[0],409)
            finally:server.shutdown();server.server_close();thread.join()
    def test_archived_artifact_tampering_is_rejected_before_integration(self):
        from agent_work.workspaces import Workspaces
        spaces=Workspaces(self.root,self.store.path);attempt=spaces.capture('G','J');(Path(attempt['root'])/'backend.py').write_text('approved')
        record=spaces.archive(attempt);(Path(record['artifact'])/'after/backend.py').write_text('tampered')
        with self.assertRaises(BoardError):spaces.integrate(attempt,record,['backend.py'],True)
        self.assertEqual((self.root/'backend.py').read_text(),'value = 1\n')
    def test_real_codex_json_subprocess_contract_flow(self):
        import os,shutil
        self.grant();fake=self.root/'fake-bin';fake.mkdir();script=fake/'codex'
        script.write_text('#!'+sys.executable+'\n'+'''import sys,json,uuid,hashlib
from pathlib import Path
prompt=sys.stdin.read();job=json.loads(prompt.split('Assigned job: ',1)[1].split('\\nColleagues:',1)[0]);context=json.loads(prompt.split('Controller collaboration context: ',1)[1].split('\\nApproved verification argv:',1)[0]);r=dict(action='complete',summary='actual fake subprocess result',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None,collaboration=None)
if job['kind']=='review':r.update(action='review',verdict='approved')
elif job['kind']=='contract_review':
 rev=context['revision'];assert json.loads(Path('api.json').read_text())['fields']==['stock'];r.update(action='contract_review',collaboration=dict(revision_id=rev['id'],sha256=rev['sha256'],verdict='approved',summary='actual consumer files checked'))
elif 'backend.py' in job['files']:
 Path('api.json').write_text('{"fields": ["stock"]}');Path('backend.py').write_text('value = 2\\n');r.update(action='contract_proposal',collaboration=dict(path='api.json',change_type='additive',summary='actual contract and backend changes'))
elif not job['replies']:
 r.update(action='work_request',collaboration=dict(to_role='backend',title='catalog fields',instructions='add stock field',acceptance=['stock field documented and verified'],files=['backend.py','api.json'],requirements=['catalog'],change_type='additive',contract_path='api.json',expected_revision=hashlib.sha256(Path('api.json').read_bytes()).hexdigest(),wait_for='integration_verified',verification=context['delegation']['verification'],idempotency_key='subprocess-request'))
else:Path('frontend.py').write_text('value = 2\\n')
thread=sys.argv[sys.argv.index('resume')+1] if 'resume' in sys.argv else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':thread}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(r)}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':50,'output_tokens':20}}))
''');script.chmod(0o755)
        with patch.dict(os.environ,{'PATH':str(fake)+os.pathsep+os.environ['PATH']}):
            self.assertEqual(shutil.which('codex'),str(script));self.w.run(self.goal['id'],self.w.goal(self.goal['id'])['version'])
        snap=self.w.snapshot(self.goal['id']);self.assertEqual(snap['goal']['status'],'awaiting_acceptance',[(j['title'],j['status'],j['result']) for j in snap['jobs']]);self.assertGreater(snap['goal']['token_used'],0)
        self.assertTrue(snap['collaboration']['requests'][0]['integration_verified']);self.assertEqual(snap['collaboration']['revisions'][0]['status'],'confirmed')
    def test_unknown_usage_halts_automatic_work_instead_of_counting_zero(self):
        self.grant()
        from agent_work.usage import Usage
        usage=Usage(self.store);usage.start('unknown-run',task_id=self.fe,goal_id=self.goal['id'],role='implement');usage.finish('unknown-run','succeeded',1)
        self.assertEqual(self.c.budget(self.goal['id']),'usage_unknown')
        self.grant();self.assertEqual(self.c.budget(self.goal['id']),'')
