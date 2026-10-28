import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from agent_work.store import Store, BoardError

def enable_harness(workspace):
    root=Path(workspace).resolve();folder=root/'.tack-orchestration';folder.mkdir(exist_ok=True);(folder/'install.json').write_text(json.dumps({'format':1,'workspace':str(root)}))

from agent_work.orchestration import Orchestrator, hook_command
from agent_work.hook_setup import merge_hooks

class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'work.sqlite3');self.orch=Orchestrator(self.store,self.root)
        self.payload={'acceptance':'test passes','steps':[{'title':'build','prompt':'implement the scoped change','role':'implement'}],'verification':[[sys.executable,'-c','assert 1+1==2']]}
    def tearDown(self):self.temp.cleanup()
    def goal(self):return self.orch.create('Goal',self.payload)
    def test_additive_migration_preserves_records_and_is_idempotent(self):
        task=self.store.write({'title':'old'})['task'];session=self.store.checkpoint({'task_id':task['id'],'goal':'old','next_action':'next'})
        before=self.store.detail(task['id']);self.orch.migrate();self.orch.migrate()
        self.assertEqual(before,self.store.detail(task['id']));self.assertEqual(self.store.sessions(),[session['session']])
        self.assertEqual(len(list((self.root/'backups').glob('*.sqlite3'))),1)
    def test_bad_plan_creates_no_partial_tasks(self):
        self.payload['steps'].append({'title':'bad','prompt':'','role':'implement'})
        with self.assertRaises(BoardError):self.goal()
        self.assertEqual(self.store.all(),[])
    def test_events_are_idempotent_and_never_store_private_payload(self):
        goal=self.goal();task=goal['steps'][0]['task_id'];self.orch.bind('thread',task)
        data={'hook_event_name':'SubagentStart','session_id':'thread','event_id':'event','agent_id':'child','prompt':'secret','tool_input':{'token':'secret'},'transcript_path':'private'}
        self.orch.observe(data);self.orch.observe(data)
        self.orch.observe({'hook_event_name':'Stop','session_id':'child','event_id':'child-stop'})
        overview=self.orch.overview();self.assertEqual(len(overview['observations']),2)
        self.assertTrue(all(o['task_id']==task for o in overview['observations']))
        self.assertNotIn('secret',json.dumps(overview));self.assertNotIn('private',json.dumps(overview['observations']))
    def test_unbound_session_is_not_attached_to_recent_task(self):
        self.goal();self.orch.observe({'hook_event_name':'SessionStart','session_id':'unbound'})
        self.assertIsNone(self.orch.overview()['observations'][0]['task_id'])
    def test_goal_version_and_single_checkout_owner(self):
        a=self.goal();b=self.goal()
        a=self.orch.update(a['id'],{'worker':'one','heartbeat':time.time()},a['version'])
        version=a['version'];self.orch.update(a['id'],{'heartbeat':time.time()},worker='one')
        self.assertEqual(self.orch.goal(a['id'])['version'],version)
        with self.assertRaises(BoardError):self.orch.update(a['id'],{'status':'paused'},1)
        with self.assertRaises(BoardError):self.orch.update(b['id'],{'worker':'two'},b['version'])
        with self.assertRaises(BoardError):self.orch.recover(a['id'],version,True)
    def test_completed_work_requires_verification_and_retries_only_verification(self):
        goal=self.goal()
        def process(argv,cwd,key,worker,run_id=None,prompt=None):return {'status':'succeeded' if run_id else 'failed','exit_code':0 if run_id else 1}
        with patch.object(self.orch,'process',side_effect=process):self.orch.work(goal['id'],goal['version'],approve=True)
        current=self.orch.goal(goal['id']);self.assertEqual(current['status'],'failed');self.assertEqual(current['next_step'],1)
        self.assertNotEqual(self.store.detail(goal['task_id'])['task']['status'],'done')
        calls=[]
        def passing(*args,**kwargs):calls.append(args);return {'status':'succeeded','exit_code':0}
        with patch.object(self.orch,'process',side_effect=passing):self.orch.work(goal['id'],current['version'],approve=True)
        self.assertEqual(len(calls),1);self.assertEqual(self.orch.goal(goal['id'])['status'],'done')
        self.assertEqual(self.store.detail(goal['task_id'])['task']['status'],'done')
    def test_interrupted_step_is_retried_and_goal_never_done(self):
        goal=self.goal()
        with patch.object(self.orch,'process',return_value={'status':'interrupted','exit_code':-15}):self.orch.work(goal['id'],goal['version'],approve=True)
        current=self.orch.goal(goal['id']);self.assertEqual(current['status'],'paused');self.assertEqual(current['next_step'],0);self.assertFalse(current['worker'])
        self.assertEqual(self.orch.overview()['runs'][0]['status'],'interrupted')
    def test_actual_process_pause_terminates_owned_process(self):
        goal=self.goal();goal=self.orch.update(goal['id'],{'worker':'test','status':'verifying'},goal['version'])
        result=[]
        worker=threading.Thread(target=lambda:result.append(self.orch.process([sys.executable,'-c','import time; time.sleep(30)'],str(self.root),goal['id'],'test')))
        worker.start();time.sleep(.2)
        latest=self.orch.goal(goal['id']);self.orch.pause(goal['id'],latest['version']);worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(result[0]['status'],'interrupted')
    def test_command_hooks_preserve_other_entries_and_are_idempotent(self):
        raw=json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command','command':'keep'}]}]}})
        first=merge_hooks(raw,Path('/path with space/tack'))
        self.assertEqual(first,merge_hooks(first,Path('/path with space/tack')))
        self.assertEqual(json.loads(first)['hooks']['Stop'][0]['hooks'][0]['command'],'keep')
        self.assertIn("'/path with space/tack' hook",first)
        custom=json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command','command':'custom','statusMessage':'Tack lifecycle record'}]}]}})
        with self.assertRaises(ValueError):merge_hooks(custom,Path('/tack'))
    def test_missing_worker_never_automatically_reruns(self):
        goal=self.goal();goal=self.orch.update(goal['id'],{'worker':'gone','heartbeat':time.time()-40,'status':'running'},goal['version'])
        self.assertEqual(self.orch.overview()['goals'][0]['status'],'needs_recheck')
        with self.assertRaises(BoardError):self.orch.reconcile(goal['id'],goal['version'])
        recovered=self.orch.recover(goal['id'],goal['version'],True)
        self.assertEqual(recovered['status'],'paused');self.assertFalse(recovered['worker'])
    def test_unregistered_hook_does_not_create_project(self):
        with patch.dict(os.environ,{'AGENT_WORK_HOME':str(self.root/'registry')}):
            self.assertEqual(hook_command({'cwd':str(self.root),'session_id':'s','hook_event_name':'SessionStart'}),{})
            self.assertFalse((self.root/'registry').exists())


class OrchestrationHttpTests(unittest.TestCase):
    def test_http_goal_execution_disabled_and_origin_protected(self):
        import http.client
        from agent_work.http import BoardServer
        with tempfile.TemporaryDirectory() as folder:
            enable_harness(folder);store=Store(Path(folder)/'db.sqlite3');server=BoardServer(('127.0.0.1',0),store,{'id':'local','name':'test','workspace':folder,'source_roots':[]},harness=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method,path,data=None,origin=None):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                headers={'Content-Type':'application/json'}
                if origin:headers['Origin']=origin
                conn.request(method,path,json.dumps(data) if data is not None else None,headers)
                response=conn.getresponse();result=json.loads(response.read());status=response.status;conn.close();return status,result
            try:
                self.assertEqual(request('GET','/api/orchestration')[1]['goals'],[])
                body={'title':'web goal','acceptance':'passes','steps':[{'title':'step','prompt':'scoped'}],'verification':[[sys.executable,'-c','pass']]}
                self.assertEqual(request('POST','/api/goals',body,'https://evil.example')[0],403)
                status,goal=request('POST','/api/goals',body);self.assertEqual(status,201)
                self.assertEqual(request('POST',f"/api/goals/{goal['id']}/start",{'version':1})[0],403)
                self.assertEqual(len(request('GET','/api/orchestration')[1]['goals']),1)
            finally:server.shutdown();server.server_close();thread.join()

class GoalCliTests(unittest.TestCase):
    def test_installed_style_background_launcher_uses_runtime_from_other_cwd(self):
        import subprocess
        from agent_work.orchestration_cli import start
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace=root/'workspace';workspace.mkdir()
            with patch.dict(os.environ,{'AGENT_WORK_HOME':str(root/'state')}):
                from agent_work.projects import resolve
                enable_harness(workspace);project=resolve(workspace,create=True);orch=Orchestrator(Store(project['db_path']),workspace)
                goal=orch.create('background',{'acceptance':'pass','steps':[{'title':'verify','role':'verify','prompt':'Reply only OK, no tools.'}],'verification':[[sys.executable,'-c','pass']]})
                # Fake Codex exercises the real detached worker/JSON contract without network or credentials.
                binary=root/'bin';binary.mkdir();codex=binary/'codex'
                codex.write_text('#!'+sys.executable+'\nimport sys,json\nsys.stdin.read()\nprint(json.dumps({"type":"thread.started","thread_id":"fake-thread"}))\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps({"passed":True,"summary":"checked"})}}))\nprint(json.dumps({"type":"turn.completed"}))\n');codex.chmod(0o755)
                with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):
                    result=start(project,goal['id'],goal['version'],approve=True)
                    try:
                        for _ in range(100):
                            current=orch.goal(goal['id'])
                            if current['status'] in {'done','failed','needs_recheck'}:break
                            time.sleep(.05)
                        self.assertEqual(current['status'],'done',current)
                    finally:
                        try:os.waitpid(result['worker_launcher_pid'],0)
                        except ChildProcessError:pass

class VerdictTests(unittest.TestCase):
    def test_negative_review_cannot_succeed_even_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');orch=Orchestrator(store,folder)
            goal=orch.create('review',{'acceptance':'pass','steps':[{'title':'review','role':'verify','prompt':'review'}],'verification':[[sys.executable,'-c','pass']]})
            goal=orch.update(goal['id'],{'worker':'test'},goal['version']);run,_=orch.run(goal,goal['steps'][0])
            script='import json,sys;sys.stdin.read();print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps({"passed":False,"summary":"requirement missing"})}}));print(json.dumps({"type":"turn.completed"}))'
            result=orch.process([sys.executable,'-c',script],folder,goal['id'],'test',run,'review')
            self.assertEqual(result['exit_code'],0);self.assertEqual(result['status'],'failed')
            self.assertEqual(result['error'],'review_rejected_or_missing_verdict')



class PlanningTests(unittest.TestCase):
    def test_planning_stops_for_review_before_any_implementation(self):
        with tempfile.TemporaryDirectory() as folder:
            orch=Orchestrator(Store(Path(folder)/'db'),folder)
            goal=orch.draft('goal',{'acceptance':'tests pass','verification_text':'python3 -m unittest'})
            plan=[{'title':'implement','role':'implement','prompt':'edit calculator.py only','files':['calculator.py']},{'title':'review','role':'verify','prompt':'check the acceptance','files':['calculator.py','tests.py']}]
            with patch.object(orch,'process',return_value={'status':'succeeded','exit_code':0,'plan_steps':plan}) as process:
                orch.work(goal['id'],goal['version'],approve=True)
                self.assertEqual(process.call_count,1)
                argv=process.call_args.args[0]
                self.assertIn('read-only',argv);self.assertIn('--output-schema',argv)
            ready=orch.goal(goal['id'])
            self.assertEqual(ready['status'],'ready');self.assertEqual(ready['next_step'],1)
            self.assertFalse(ready['planning']);self.assertFalse(ready['worker'])
            self.assertEqual(orch.store.detail(ready['steps'][1]['task_id'])['task']['status'],'todo')
            with patch.object(orch,'process',return_value={'status':'succeeded','exit_code':0}) as process:
                orch.work(ready['id'],ready['version'],approve=True);self.assertEqual(process.call_count,3)
            self.assertEqual(orch.goal(ready['id'])['status'],'done')
    def test_draft_parses_quoted_commands_but_rejects_shell_chaining(self):
        with tempfile.TemporaryDirectory() as folder:
            orch=Orchestrator(Store(Path(folder)/'db'),folder)
            with self.assertRaises(BoardError):orch.draft('goal',{'acceptance':'pass','verification_text':'python3 a.py && rm x'})
            self.assertEqual(orch.store.all(),[])
            goal=orch.draft('goal',{'acceptance':'pass','verification_text':'python3 "file with space.py"'})
            self.assertEqual(goal['verification'],[['python3','file with space.py']])
    def test_invalid_generated_plan_does_not_create_partial_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            orch=Orchestrator(Store(Path(folder)/'db'),folder)
            goal=orch.draft('goal',{'acceptance':'pass','verification_text':'python3 -m unittest'})
            goal=orch.update(goal['id'],{'worker':'worker'},goal['version'])
            before=orch.store.all()
            with self.assertRaises(BoardError):orch.accept_plan(goal['id'],'worker',[{'title':'only implement','role':'implement','prompt':'edit'}])
            self.assertEqual(orch.store.all(),before)



class FileEvidenceTests(unittest.TestCase):
    def test_hashes_prove_protected_files_unchanged_without_copying_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'source.py').write_text('before');(root/'test.py').write_text('protected')
            orch=Orchestrator(Store(root/'db'),root)
            before=orch.fingerprints(['source.py','test.py','new.py'])
            (root/'source.py').write_text('after')
            current=orch.fingerprints(list(before))
            self.assertEqual(before['test.py'],current['test.py'])
            self.assertNotEqual(before['source.py'],current['source.py'])
            self.assertEqual(before['new.py'],'missing')
            (root/'source.py').write_text('keep\nold\n')
            lines=orch.line_fingerprints(['source.py'])
            (root/'source.py').write_text('keep\nnew\n')
            changes=orch.line_changes(lines)['source.py']
            self.assertEqual(changes[0],{'kind':'equal','before_lines':[1,1],'current_lines':[1,1]})
            self.assertEqual(changes[1]['kind'],'replace')
            self.assertNotIn('keep',json.dumps(lines))
            self.assertNotIn('protected',json.dumps(before))
    def test_evidence_never_reads_outside_workspace_or_git_internal_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace=root/'project';workspace.mkdir();(root/'outside').write_text('private')
            (workspace/'link').symlink_to(root/'outside')
            orch=Orchestrator(Store(root/'db'),workspace)
            for name in ['../outside','link','.git/config']:
                with self.assertRaises(BoardError):orch.fingerprints([name])



class BindingTransitionTests(unittest.TestCase):
    def test_explicit_task_transition_preserves_historical_event_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            orch=Orchestrator(Store(Path(folder)/'db'),folder)
            a=orch.store.write({'title':'first'})['task']['id'];b=orch.store.write({'title':'second'})['task']['id']
            orch.bind('thread',a);orch.observe({'session_id':'thread','hook_event_name':'Stop','event_id':'before'})
            with self.assertRaises(BoardError):orch.bind('thread',b)
            orch.bind('thread',b,expected_task=a)
            orch.observe({'session_id':'thread','hook_event_name':'Stop','event_id':'after'})
            with self.assertRaises(BoardError):orch.bind('thread',a,expected_task=a)
            self.assertEqual([e['task_id'] for e in orch.overview()['observations']],[b,a])



class ApprovedExecutionTests(unittest.TestCase):
    def test_review_failure_explicit_resume_revisits_implementation_with_feedback(self):
        with tempfile.TemporaryDirectory() as folder:
            orch=Orchestrator(Store(Path(folder)/'db'),folder)
            goal=orch.create('goal',{'acceptance':'pass','steps':[{'title':'implement','role':'implement','prompt':'after approval edit source'},{'title':'review','role':'verify','prompt':'review source'}],'verification':[[sys.executable,'-c','pass']]})
            def rejected(*args):
                role='verify' if 'read-only' in args[0] else 'implement'
                return {'status':'failed' if role=='verify' else 'succeeded','exit_code':0}
            with patch.object(orch,'process',side_effect=rejected) as proc:
                orch.work(goal['id'],goal['version'],approve=True)
                self.assertTrue(all('ALREADY approved' in call.args[-1] for call in proc.call_args_list))
            failed=orch.goal(goal['id']);self.assertEqual(failed['next_step'],1)
            with patch.object(orch,'process',return_value={'status':'succeeded','exit_code':0}) as proc:
                orch.work(goal['id'],failed['version'],approve=True)
                self.assertEqual(proc.call_count,3)
                self.assertIn('workspace-write',proc.call_args_list[0].args[0])
            self.assertEqual(orch.goal(goal['id'])['status'],'done')



class GlobalOverviewTests(unittest.TestCase):
    def test_global_view_keeps_project_identity_and_prioritizes_open_goals_without_writes(self):
        import http.client
        from agent_work.projects import resolve
        from agent_work.http import BoardServer
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'AGENT_WORK_HOME':str(Path(folder)/'state')}):
            roots=[Path(folder)/name for name in ('a','b')]
            for root in roots:root.mkdir()
            projects=[resolve(root,create=True) for root in roots]
            stores=[Store(p['db_path']) for p in projects]
            engines=[Orchestrator(store,p['workspace']) for store,p in zip(stores,projects)]
            plan={'acceptance':'pass','steps':[{'title':'review','role':'verify','prompt':'check'}],'verification':[[sys.executable,'-c','pass']]}
            done=engines[0].create('completed',plan)
            with patch.object(engines[0],'process',return_value={'status':'succeeded','exit_code':0}):engines[0].work(done['id'],done['version'],approve=True)
            engines[1].create('pending',plan)
            before=[store.all() for store in stores]
            [enable_harness(p['workspace']) for p in projects]
            server=BoardServer(('127.0.0.1',0),stores[0],projects[0],harness=True);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('GET','/api/orchestration?all=1');response=conn.getresponse();data=json.loads(response.read());conn.close()
                self.assertEqual(response.status,200)
                self.assertEqual([g['title'] for g in data['goals']],['pending','completed'])
                self.assertEqual(data['goals'][0]['project_id'],projects[1]['id'])
                self.assertEqual([store.all() for store in stores],before)
            finally:server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
