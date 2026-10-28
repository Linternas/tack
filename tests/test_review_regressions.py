import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from agent_work.store import Store, BoardError
from agent_work.team import Team
from agent_work.orchestration import Orchestrator
from agent_work.harness import install
from agent_work.orchestration_cli import start

SOURCE=Path(__file__).resolve().parents[1]
class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir()
        self.env=patch.dict(os.environ,{'AGENT_WORK_HOME':str(self.root/'state')});self.env.start()
        self.store=Store(self.root/'work.sqlite3');self.orch=Orchestrator(self.store,self.workspace);self.team=Team(self.store)
        self.goal=self.orch.create('Goal',{'acceptance':'real result','steps':[{'title':r,'prompt':'scope','role':r,'files':['source.py']} for r in ['implement','verify']],'verification':[[sys.executable,'-m','unittest']]})
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def send(self,kind,target):
        return self.team.send(self.orch,self.goal['id'],{'version':self.orch.goal(self.goal['id'])['version'],'kind':kind,'target':target,'body':'check','client_id':kind+str(time.time_ns())})
    def passing(self,argv,cwd,key,worker,run_id=None,prompt=None):
        if run_id:self.orch.run_update(run_id,review_summary='performed')
        return {'status':'succeeded','exit_code':0}
    def test_question_and_review_never_create_initial_approval(self):
        request=self.send('question','implement')
        with patch.object(self.orch,'process',side_effect=self.passing):self.orch.work(self.goal['id'],request['version'])
        self.assertFalse(self.orch.goal(self.goal['id']).get('approved_at'))
        for kind,target in [('revision','implement'),('review','verify')]:
            with self.assertRaises(BoardError):self.send(kind,target)
        with patch.object(self.orch,'process') as process,self.assertRaises(BoardError):self.orch.work(self.goal['id'],self.orch.goal(self.goal['id'])['version'])
        process.assert_not_called()
    def test_initial_readonly_review_cannot_dispatch_following_implementation(self):
        settings=self.team.settings();settings['roles']['audit']={'name':'평가','kind':'assess','model':'','instructions':'','skills':[]};self.team.save(settings)
        goal=self.orch.create('Review first',{'acceptance':'real result','steps':[{'title':r,'prompt':'scope','role':r,'files':['source.py']} for r in ['audit','implement','verify']],'verification':[[sys.executable,'-m','unittest']]})
        with self.assertRaises(BoardError):self.team.send(self.orch,goal['id'],{'version':goal['version'],'kind':'review','target':'audit','body':'review only','client_id':'first-review'})
        self.assertFalse(self.orch.goal(goal['id']).get('approved_at'));self.assertFalse(self.orch.overview()['runs'])
    def test_explicit_approval_is_rechecked_after_role_capabilities_change(self):
        with patch.object(self.orch,'process',return_value={'status':'interrupted','exit_code':-15}):self.orch.work(self.goal['id'],self.goal['version'],approve=True)
        goal=self.orch.goal(self.goal['id']);self.assertTrue(goal['approved_at']);self.assertTrue(goal['approved_contract'])
        settings=self.team.settings();settings['roles']['verify']['kind']='implement';settings['roles']['audit']={'name':'감사','kind':'verify','instructions':'','model':'','skills':[]};self.team.save(settings)
        with patch.object(self.orch,'process') as process,self.assertRaises(BoardError):self.orch.work(goal['id'],goal['version'])
        process.assert_not_called()
    def test_passed_true_with_user_question_stops_before_next_role(self):
        seen=[]
        def process(argv,cwd,key,worker,run_id=None,prompt=None):
            seen.append(run_id);self.orch.run_update(run_id,review_summary='Need user choice',verdict=True,handoff={'target':'user','kind':'question','body':'Choose rules','files':[]})
            return {'status':'succeeded','exit_code':0}
        with patch.object(self.orch,'process',side_effect=process):self.orch.work(self.goal['id'],self.goal['version'],approve=True)
        goal=self.orch.goal(self.goal['id']);self.assertEqual(goal['status'],'waiting_answer');self.assertEqual(goal['next_step'],0);self.assertEqual(len(seen),1);self.assertEqual(goal['questions'][0]['question'],'Choose rules')
        self.assertNotEqual(self.store.detail(goal['steps'][0]['task_id'])['task']['status'],'done')
    def test_workspace_mismatch_rejected_before_launcher_or_worker(self):
        other=self.root/'other';other.mkdir();orch=Orchestrator(self.store,other)
        with patch.object(orch,'process') as process,self.assertRaises(BoardError):orch.work(self.goal['id'],self.goal['version'],approve=True)
        process.assert_not_called()
        with patch('agent_work.harness.require_enabled'),patch('subprocess.Popen') as popen,self.assertRaises(BoardError):start({'workspace':str(other),'db_path':str(self.store.path)},self.goal['id'],self.goal['version'],approve=True)
        popen.assert_not_called();self.assertFalse(self.orch.goal(self.goal['id'])['worker'])
    def test_closed_or_unread_stdin_remains_cancellable_and_child_is_reaped(self):
        for close in (False,True):
            goal=self.orch.update(self.goal['id'],{'worker':'test','status':'running','pause_requested':False},self.orch.goal(self.goal['id'])['version'])
            pidfile=self.root/'pid';result=[]
            script=f'import os,time;open({str(pidfile)!r},"w").write(str(os.getpid()));'+('os.close(0);' if close else '')+'time.sleep(30)'
            thread=threading.Thread(target=lambda:result.append(self.orch.process([sys.executable,'-c',script],str(self.workspace),goal['id'],'test',prompt='x'*2000000)))
            thread.start()
            for _ in range(100):
                if pidfile.exists():break
                time.sleep(.02)
            self.assertTrue(pidfile.exists());pid=int(pidfile.read_text());pidfile.unlink()
            state=self.orch.goal(goal['id']);self.orch.pause(goal['id'],state['version']);thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(result[0]['status'],'interrupted')
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
    def test_existing_empty_implementation_and_verification_guidance_gets_defaults(self):
        settings=self.team.settings()
        for role in ['implement','verify']:settings['roles'][role]['instructions']=''
        self.team.save(settings);seen=[]
        def process(argv,cwd,key,worker,run_id=None,prompt=None):
            if run_id:
                seen.append(prompt)
                self.assertIn('User-configured role guidance:',prompt)
            return {'status':'succeeded','exit_code':0}
        with patch.object(self.orch,'process',side_effect=process):self.orch.work(self.goal['id'],self.goal['version'],approve=True)
        self.assertIn('실제 변경과 수행한 검증',seen[0]);self.assertIn('수행하지 않은 검증을 통과로 표시하지 않는다',seen[1])
        self.assertEqual(self.team.settings()['roles']['implement']['instructions'],'')
    def test_full_result_is_stored_and_handed_to_next_role(self):
        long='Requirement\n'*1000+'FINAL-CONDITION'
        goal=self.orch.update(self.goal['id'],{'worker':'test','execution_settings':self.team.settings()},self.goal['version']);run,_=self.orch.run(goal,goal['steps'][0])
        events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'passed':True,'summary':long,'handoff':None})}},{'type':'turn.completed'}]
        script=';'.join('print('+repr(json.dumps(event))+')' for event in events)
        self.assertEqual(self.orch.process([sys.executable,'-c',script],str(self.workspace),goal['id'],'test',run)['status'],'succeeded')
        with self.store.connect() as db:self.assertEqual(json.loads(db.execute('SELECT data FROM executions WHERE id=?',(run,)).fetchone()[0])['review_summary'],long)
        def check(argv,cwd,key,worker,run_id=None,prompt=None):self.assertIn('FINAL-CONDITION',prompt);return {'status':'succeeded','exit_code':0}
        with patch.object(self.orch,'process',side_effect=check):self.orch.execute_role(goal,goal['steps'][1],goal['id'],'test')
    def brief(self,roles,**overrides):
        return {'summary':'team','roles':roles,'questions':[],'acceptance':'model changed acceptance','verification':[[sys.executable,'-m','compileall']],'steps':[{'title':r,'role':r,'prompt':'scope','files':['source.py']} for r in ['implement','verify']],**overrides}
    def test_scoped_planning_can_ask_and_preserves_explicit_contract(self):
        goal=self.orch.draft('Scoped',{'acceptance':'USER acceptance','verification':[[sys.executable,'-m','unittest']]})
        roles=[{'id':r,**cfg} for r,cfg in self.team.settings()['roles'].items()];brief=self.brief(roles,questions=[{'id':'rule','question':'which rule?'}],steps=[])
        def ask(argv,cwd,key,worker,run_id=None,prompt=None):
            self.assertTrue(any(str(arg).endswith('brief.schema.json') for arg in argv));return {'status':'succeeded','exit_code':0,'brief':brief,'plan_steps':brief['steps']}
        with patch.object(self.orch,'process',side_effect=ask):self.orch.work(goal['id'],goal['version'])
        state=self.orch.goal(goal['id']);self.assertEqual(state['status'],'waiting_answer')
        request=self.team.send(self.orch,goal['id'],{'version':state['version'],'kind':'answer','body':'rule A','client_id':'answer'})
        brief=self.brief(roles)
        with patch.object(self.orch,'process',side_effect=ask):self.orch.work(goal['id'],request['version'])
        state=self.orch.goal(goal['id']);self.assertEqual(state['acceptance'],'USER acceptance');self.assertEqual(state['verification'],[[sys.executable,'-m','unittest']]);self.assertFalse(state.get('approved_at'))
    def test_goal_specific_guidance_and_skills_do_not_leak_into_team_or_next_goal(self):
        skill=self.workspace/'.agents/skills/evaluator/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text('Specific evaluation skill')
        roles=[{'id':r,**cfg} for r,cfg in self.team.settings()['roles'].items()]
        for role in roles:role.update(instructions='ONLY GOAL A',skills=['.agents/skills/evaluator/SKILL.md'])
        roles.append({'id':'designer','name':'디자이너','kind':'plan','model':'','skills':[],'instructions':'ONLY GOAL A'})
        goal=self.orch.draft('A',{});brief=self.brief(roles)
        with patch.object(self.orch,'process',return_value={'status':'succeeded','exit_code':0,'brief':brief,'plan_steps':brief['steps']}):self.orch.work(goal['id'],goal['version'])
        state=self.orch.goal(goal['id']);settings=self.team.settings()
        self.assertNotIn('ONLY GOAL A',settings['roles']['designer']['instructions']);self.assertEqual(settings['roles']['implement']['skills'],[])
        self.assertEqual(state['role_guidance']['designer'],'ONLY GOAL A');self.assertEqual(state['role_skills']['implement'],['.agents/skills/evaluator/SKILL.md'])
        state={**state,'execution_settings':settings}
        def check(argv,cwd,key,worker,run_id=None,prompt=None):self.assertIn('ONLY GOAL A',prompt);self.assertIn('Specific evaluation skill',prompt);return {'status':'succeeded','exit_code':0}
        with patch.object(self.orch,'process',side_effect=check):self.orch.execute_role(state,state['steps'][1],goal['id'],'')
        b=self.orch.draft('B',{});self.assertNotIn('role_guidance',b);self.assertNotIn('role_skills',b)
    def test_stock_skill_upgrade_legacy_and_custom_preservation(self):
        old=install(SOURCE,self.workspace);skill=self.workspace/'.agents/skills/tack-orchestration/SKILL.md';old_bytes=skill.read_bytes();installed_store=Store(old['data']);existing=installed_store.write({'title':'Preserve existing data'})['task']
        source=self.root/'updated';shutil.copytree(SOURCE/'agent_work',source/'agent_work');shutil.copytree(SOURCE/'templates',source/'templates')
        (source/'templates/tack-orchestration.md').write_bytes(old_bytes+b'\nUpdated stock guidance.\n')
        # Legacy marker has no saved hash, but known shipped stock is recognised.
        skill.write_bytes(old_bytes.split(b'\nGoal-specific role guidance')[0])
        marker=self.workspace/'.tack-orchestration/install.json';data=json.loads(marker.read_text());data.pop('skill_sha256');marker.write_text(json.dumps(data))
        upgraded=install(source,self.workspace);self.assertIn(b'Updated stock guidance',skill.read_bytes());self.assertTrue(upgraded['backups']);self.assertEqual(json.loads(marker.read_text())['skill_sha256'],hashlib.sha256(skill.read_bytes()).hexdigest())
        self.assertEqual(install(source,self.workspace)['backups'],[]);self.assertTrue(Path(old['runtime']).is_dir());self.assertEqual(installed_store.detail(existing['id'])['task'],existing)
        skill.write_text('My custom instructions');before=marker.read_bytes()
        with self.assertRaises(BoardError):install(source,self.workspace)
        self.assertEqual(skill.read_text(),'My custom instructions');self.assertEqual(marker.read_bytes(),before)
