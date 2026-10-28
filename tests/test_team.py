import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from agent_work.store import Store, BoardError

def enable_harness(workspace):
    root=Path(workspace).resolve();folder=root/'.tack-orchestration';folder.mkdir(exist_ok=True);(folder/'install.json').write_text(json.dumps({'format':1,'workspace':str(root)}))

from agent_work.orchestration import Orchestrator
from agent_work.team import Team, conversation, validate_handoff

class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'work.sqlite3');self.orch=Orchestrator(self.store,self.root);self.team=Team(self.store)
        (self.root/'source.py').write_text('value = 1\n')
        self.goal=self.orch.create('Scope',{'acceptance':'checks pass','steps':[
            {'title':'implement','prompt':'Fix source.py only','role':'implement','files':['source.py']},
            {'title':'verify','prompt':'Review source.py','role':'verify','files':['source.py']}],
            'verification':[[sys.executable,'-c','assert True']]})
    def tearDown(self):self.temp.cleanup()
    def send(self,kind='note',target=None,body='hello',client='request',version=None):
        return self.team.send(self.orch,self.goal['id'],{'kind':kind,'target':target,'body':body,'client_id':client,'version':version or self.orch.goal(self.goal['id'])['version']})
    def policy(self,**change):return self.team.save({**self.team.settings(),**change})
    def passing(self,argv,cwd,key,worker,run_id=None,prompt=None):
        if run_id:self.orch.run_update(run_id,review_summary='Actually checked',handoff=None,verdict=True)
        return {'status':'succeeded','exit_code':0}
    def finish(self):
        with patch.object(self.orch,'process',side_effect=self.passing):self.orch.work(self.goal['id'],self.orch.goal(self.goal['id'])['version'],approve=True)
    def test_settings_read_does_not_migrate_and_write_preserves_tasks(self):
        before=self.store.all();self.assertFalse(self.team.exists());self.assertEqual(self.team.settings()['version'],0);self.assertFalse(self.team.exists())
        saved=self.policy();self.assertEqual(saved['version'],1);self.assertEqual(before,self.store.all())
        stale=copy.deepcopy(saved);self.policy(max_rounds=3)
        with self.assertRaises(BoardError):self.team.save(stale)
        self.assertEqual(self.team.settings()['max_rounds'],3)
    def test_settings_validate_names_models_and_limits(self):
        for patch_value in [{'max_rounds':True},{'max_minutes':0},{'max_tokens':2000001},{'autopilot':1}]:
            with self.subTest(patch_value=patch_value),self.assertRaises(BoardError):self.policy(**patch_value)
        for name in ['tack','with space','@impl','verify']:
            settings=self.team.settings();settings['roles']['implement']['name']=name
            with self.subTest(name=name),self.assertRaises(BoardError):self.team.save(settings)
        settings=self.team.settings();settings['roles']['implement']['model']='--bad'
        with self.assertRaises(BoardError):self.team.save(settings)
    def test_note_is_idempotent_no_execution_and_conflicting_retry_is_rejected(self):
        version=self.goal['version'];first=self.send();second=self.send()
        self.assertFalse(first['execute']);self.assertTrue(second['duplicate']);self.assertEqual(first['message']['id'],second['message']['id'])
        self.assertEqual(self.orch.goal(self.goal['id'])['version'],version)
        with self.assertRaises(BoardError):self.send(body='different')
        self.assertEqual(len(conversation(self.orch,self.goal['id'])['messages']),2)
    def test_stale_version_and_unknown_or_conflicting_mention_preserve_records(self):
        self.orch.update(self.goal['id'],{'error':'state changed'},self.goal['version'])
        for payload in [{'version':1},{'body':'@unknown hello'},{'body':'@구현 hello','target':'verify'}]:
            with self.subTest(payload=payload),self.assertRaises(BoardError):self.send(**payload)
        self.assertEqual(len(conversation(self.orch,self.goal['id'])['messages']),1)
    def test_action_requires_approval_prerequisites_and_single_pending_request(self):
        with self.assertRaises(BoardError):self.send('revision','implement')
        with self.assertRaises(BoardError):self.send('review','verify')
        result=self.send('question',body='@구현 설명해줘');self.assertTrue(result['execute'])
        with self.assertRaises(BoardError):self.send('question','verify',client='other')
        self.assertFalse(self.send(client='note')['execute'])
    def test_question_is_read_only_and_preserves_completed_task_state(self):
        self.finish();before=self.store.all();result=self.send('question','implement',body='Explain source.py')
        prompts=[]
        def answer(argv,cwd,key,worker,run_id=None,prompt=None):
            prompts.append((argv,prompt));return self.passing(argv,cwd,key,worker,run_id,prompt)
        with patch.object(self.orch,'process',side_effect=answer):self.orch.work(self.goal['id'],result['version'],approve=True)
        state=self.orch.goal(self.goal['id']);self.assertEqual(state['status'],'done');self.assertIsNone(state['pending_request']);self.assertEqual(before,self.store.all())
        self.assertIn('read-only',prompts[0][0]);self.assertIn('READ-ONLY QUESTION',prompts[0][1]);self.assertNotIn('resume',prompts[0][0])
        self.assertEqual(conversation(self.orch,self.goal['id'])['messages'][-1]['kind'],'answer')
    def run_review_cycle(self,handoff,auto=True,rounds=2,always_fail=False):
        self.policy(autopilot=auto,max_rounds=rounds);calls=[]
        def process(argv,cwd,key,worker,run_id=None,prompt=None):
            if not run_id:return self.passing(argv,cwd,key,worker)
            with self.store.connect() as db:run=json.loads(db.execute('SELECT data FROM executions WHERE id=?',(run_id,)).fetchone()[0])
            calls.append(run['role']);failed=run['role']=='verify' and (always_fail or calls.count('verify')==1)
            self.orch.run_update(run_id,review_summary='Fix actual defect' if failed else 'Checked',handoff=handoff if failed else None,verdict=not failed)
            return {'status':'failed' if failed else 'succeeded','exit_code':0,'error':'review_rejected' if failed else ''}
        with patch.object(self.orch,'process',side_effect=process):self.orch.work(self.goal['id'],self.goal['version'],approve=True)
        return self.orch.goal(self.goal['id']),calls
    def test_autopilot_routes_actual_review_to_implementation_then_rechecks(self):
        state,calls=self.run_review_cycle({'target':'implement','kind':'revision','body':'Fix value and recheck','files':['source.py']})
        self.assertEqual(calls,['implement','verify','implement','verify']);self.assertEqual(state['status'],'done');self.assertEqual(state['auto_rounds'],1)
        feed=conversation(self.orch,self.goal['id'])['messages'];self.assertTrue(any(m['data'].get('source')=='autopilot' for m in feed))
    def test_manual_mode_and_scope_or_user_decision_never_auto_execute(self):
        for handoff,auto in [({'target':'implement','kind':'revision','body':'fix','files':['source.py']},False),({'target':'implement','kind':'revision','body':'outside','files':['other.py']},True),({'target':'user','kind':'question','body':'choose behavior','files':[]},True)]:
            with self.subTest(handoff=handoff,auto=auto):
                # Each run explicitly reuses the failed goal; no hidden retries.
                self.goal=self.orch.update(self.goal['id'],{'status':'ready','next_step':0},self.orch.goal(self.goal['id'])['version'])
                state,calls=self.run_review_cycle(handoff,auto);self.assertEqual(calls,['implement','verify']);self.assertEqual(state['status'],'waiting_answer' if handoff['target']=='user' else 'failed')
    def test_round_limit_stops_repeated_review_failure(self):
        state,calls=self.run_review_cycle({'target':'implement','kind':'revision','body':'fix','files':['source.py']},rounds=1,always_fail=True)
        self.assertEqual(len(calls),4);self.assertEqual(state['error'],'round_limit');self.assertEqual(state['auto_rounds'],1)
    def test_settings_snapshot_and_token_limit_stop_next_role(self):
        self.policy(max_tokens=1000)
        def process(argv,cwd,key,worker,run_id=None,prompt=None):
            self.policy(max_tokens=2000);self.orch.update(key,{'token_used':1100},worker=worker)
            return self.passing(argv,cwd,key,worker,run_id,prompt)
        with patch.object(self.orch,'process',side_effect=process):self.orch.work(self.goal['id'],self.goal['version'],approve=True)
        state=self.orch.goal(self.goal['id']);self.assertEqual(state['status'],'paused');self.assertEqual(state['error'],'token_limit');self.assertEqual(state['execution_settings']['max_tokens'],1000)
        self.assertEqual(len(conversation(self.orch,self.goal['id'])['runs']),1)
    def test_completed_response_over_token_cap_is_kept_before_next_role_stops(self):
        self.policy(max_tokens=1000)
        state=self.orch.update(self.goal['id'],{'worker':'test','status':'running','execution_settings':self.team.settings(),'token_used':0},self.goal['version'])
        run,_=self.orch.run(state,state['steps'][0])
        events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'passed':True,'summary':'Performed','handoff':None})}}, {'type':'turn.completed','usage':{'input_tokens':1000,'output_tokens':20}}]
        script='import time;'+''.join('print('+repr(json.dumps(event))+',flush=True);' for event in events)+'time.sleep(.7)'
        result=self.orch.process([sys.executable,'-c',script],str(self.root),state['id'],'test',run)
        self.assertEqual(result['status'],'succeeded');self.assertEqual(self.orch.goal(state['id'])['token_used'],1020)
        self.assertEqual(self.orch.budget_reason(self.orch.goal(state['id'])),'token_limit')
    def test_time_limit_terminates_owned_process(self):
        state=self.orch.update(self.goal['id'],{'worker':'test','status':'running','deadline_at':time.time()+.1},self.goal['version'])
        result=self.orch.process([sys.executable,'-c','import time;time.sleep(20)'],str(self.root),state['id'],'test')
        self.assertEqual(result['status'],'interrupted');self.assertEqual(result['error'],'time_limit')
    def test_stale_chat_state_never_looks_running_and_bad_handoff_rejected(self):
        self.orch.update(self.goal['id'],{'worker':'gone','status':'running','heartbeat':time.time()-40},self.goal['version'])
        self.assertEqual(conversation(self.orch,self.goal['id'])['goal']['status'],'needs_recheck')
        for value in [{'target':'implement'}, {'target':'implement','kind':'revision','body':'bad','files':'x'}]:
            with self.assertRaises(BoardError):validate_handoff(value)

class TeamHttpTests(unittest.TestCase):
    def test_settings_and_chat_routes_preserve_versions_and_execution_gate(self):
        import http.client
        import threading
        from agent_work.http import BoardServer
        with tempfile.TemporaryDirectory() as folder:
            enable_harness(folder);store=Store(Path(folder)/'work.sqlite3');orch=Orchestrator(store,folder)
            goal=orch.create('Goal',{'acceptance':'pass','steps':[{'title':'Implement','role':'implement','prompt':'Scoped'}],'verification':[[sys.executable,'-c','pass']]})
            server=BoardServer(('127.0.0.1',0),store,{'id':'local','name':'test','workspace':folder,'source_roots':[]},harness=True)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            def request(method,path,payload=None,origin=None):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                headers={'Content-Type':'application/json'}
                if origin:headers['Origin']=origin
                conn.request(method,path,json.dumps(payload) if payload is not None else None,headers)
                response=conn.getresponse();status=response.status;data=json.loads(response.read());conn.close();return status,data
            try:
                status,roles=request('GET','/api/team/catalog');self.assertEqual(status,200);self.assertEqual(len(roles['roles']),7);self.assertFalse(Team(store).exists())
                status,settings=request('GET','/api/team/settings');self.assertEqual(status,200);self.assertEqual(settings['version'],0);self.assertFalse(Team(store).exists())
                self.assertEqual(request('PATCH','/api/team/settings',settings,'https://evil.example')[0],403)
                self.assertEqual(request('PATCH','/api/team/settings',settings)[1]['version'],1)
                self.assertEqual(request('PATCH','/api/team/settings',settings)[0],409)
                url='/api/goals/'+goal['id']
                message={'version':goal['version'],'body':'Record decision','kind':'note','client_id':'note'}
                self.assertEqual(request('POST',url+'/messages',message)[0],201)
                self.assertEqual(request('POST',url+'/messages',{**message,'kind':'question','target':'implement','client_id':'question'})[0],403)
                server.allow_execution=True
                with patch('agent_work.orchestration_cli.start',return_value={'accepted':True}) as launch:
                    status,data=request('POST',url+'/messages',{**message,'kind':'question','target':'implement','client_id':'question'})
                    self.assertEqual(status,201);self.assertTrue(data['execute']);launch.assert_called_once()
                    self.assertEqual(launch.call_args.args[2],goal['version']+1)
                status,data=request('GET',url+'/chat');self.assertEqual(status,200);self.assertEqual(data['goal']['pending_request']['kind'],'question');self.assertEqual(len(data['messages']),3)
            finally:server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
