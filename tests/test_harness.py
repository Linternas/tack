import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.team import Team
from agent_work.orchestration import Orchestrator
from agent_work.harness import install,enabled
from agent_work.setup import setup

SOURCE=Path(__file__).resolve().parents[1]
class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir()
        self.env=patch.dict(os.environ,{'AGENT_WORK_HOME':str(self.root/'state')});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_core_install_excludes_orchestration_and_preserves_passive_hooks(self):
        home=self.root/'home';result=setup(SOURCE,home,{'SHELL':'/bin/zsh'});runtime=Path(result['runtime'])
        self.assertFalse((runtime/'agent_work/crewset.py').exists());self.assertFalse((runtime/'agent_work/assets/crewset.roles.json').exists());self.assertFalse((runtime/'agent_work/orchestration.py').exists());self.assertFalse((runtime/'agent_work/assets/goals.html').exists());self.assertTrue((runtime/'agent_work/tracking.py').is_file())
        self.assertFalse((home/'.codex/skills/tack-orchestration/SKILL.md').exists());self.assertNotIn('`tack-orchestration` for', (home/'.codex/AGENTS.md').read_text())
        env={**os.environ,'TACK_HARNESS':'1'}
        run=lambda *args:subprocess.run([result['launcher'],'--workspace',str(self.workspace),*args],capture_output=True,text=True,env=env)
        task=json.loads(run('task','add','Manual task').stdout)['task'];self.assertEqual(run('bind','actual-thread',task['id']).returncode,0)
        self.assertNotEqual(run('goal','list').returncode,0);self.assertNotEqual(run('settings','show').returncode,0)
        from agent_work.projects import resolve
        project=resolve(self.workspace)
        with Store(project['db_path']).connect() as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='goals'").fetchone())
    def test_core_installed_dashboard_lists_projects_without_extension_imports(self):
        import re
        import urllib.request
        import urllib.error
        result=setup(SOURCE,self.root/'core-home',{'SHELL':'/bin/zsh'})
        base=[result['launcher'],'--workspace',str(self.workspace)]
        subprocess.run(base+['task','add','Core task'],capture_output=True,text=True,check=True)
        proc=subprocess.Popen(base+['dashboard','--port','0'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        try:
            line=proc.stdout.readline();origin=re.search(r'http://127.0.0.1:[0-9]+',line)[0]
            with urllib.request.urlopen(origin+'/api/projects') as response:data=json.load(response)
            self.assertEqual(data['projects'][0]['name'],'project');self.assertEqual(data['projects'][0]['open_tasks'],1)
            with urllib.request.urlopen(origin+'/api/context') as response:self.assertFalse(json.load(response)['orchestration_enabled'])
            for path in ['/api/orchestration','/api/team/catalog','/goals']:
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(origin+path)
                self.assertEqual(error.exception.code,404);error.exception.close()
        finally:proc.terminate();proc.wait(timeout=5);proc.stdout.close()

    def test_local_install_uses_existing_tack_db_and_is_idempotent(self):
        from agent_work.projects import resolve
        project=resolve(self.workspace,create=True);store=Store(project['db_path']);task=store.write({'title':'Existing'})['task']
        result=install(SOURCE,self.workspace);again=install(SOURCE,self.workspace)
        self.assertEqual(result['data'],project['db_path']);self.assertEqual(result['runtime'],again['runtime']);self.assertEqual(again['backups'],[])
        self.assertEqual(store.detail(task['id'])['task'],task);self.assertTrue(enabled(self.workspace))
        r=subprocess.run([result['launcher'],'settings','show'],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse((self.root/'.codex/skills/tack-orchestration/SKILL.md').exists())
    def test_installed_harness_accepts_big_goal_without_json_and_waits_for_answer(self):
        import time
        result=install(SOURCE,self.workspace);binary=self.root/'bin';binary.mkdir();codex=binary/'codex'
        roles=[{'id':key,**value} for key,value in Team(Store(self.root/'fixture.sqlite3')).settings()['roles'].items()]
        brief={'summary':'질문부터 합니다.','roles':roles,'questions':[{'id':'rule','question':'어떤 규칙을 바꿀까요?'}],'acceptance':'','verification':[],'steps':[]}
        events=[{'type':'thread.started','thread_id':'brief-test-thread'},{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(brief)}},{'type':'turn.completed'}]
        codex.write_text('#!'+sys.executable+'\nimport sys,json\nsys.stdin.read()\n'+'\n'.join('print('+repr(json.dumps(e))+')' for e in events)+'\n');codex.chmod(0o755)
        env={**os.environ,'PATH':str(binary)+os.pathsep+os.environ['PATH']}
        response=subprocess.run([result['launcher'],'goal','draft','큰 목표만 입력'],capture_output=True,text=True,env=env,check=True);goal=json.loads(response.stdout)
        orch=Orchestrator(Store(result['data']),self.workspace)
        for _ in range(100):
            state=orch.goal(goal['id'])
            if state['status']=='waiting_answer':break
            time.sleep(.03)
        self.assertEqual(state['status'],'waiting_answer',state);self.assertEqual(state['questions'],brief['questions']);self.assertFalse(state.get('approved_at'))

    def test_custom_roles_skills_and_settings_are_project_scoped(self):
        a=Team(Store(self.root/'a.sqlite3'),self.workspace);b=Team(Store(self.root/'b.sqlite3'),self.workspace)
        skill=self.workspace/'.agents/skills/founder/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text('Review the product goal and demand actual evidence.')
        config=a.settings();config['roles']['founder']={'name':'창업자','kind':'assess','model':'','instructions':'평가','skills':['.agents/skills/founder/SKILL.md']};a.save(config)
        self.assertIn('founder',a.settings()['roles']);self.assertNotIn('founder',b.settings()['roles'])
        config=a.settings();config['roles']['backend']={**config['roles'].pop('implement'),'name':'백엔드'};a.save(config)
        self.assertNotIn('implement',a.settings()['roles']);self.assertIn('backend',a.settings()['roles'])
        config=a.settings();config['roles']['founder']['skills']=['missing/SKILL.md']
        with self.assertRaises(BoardError):a.save(config)
    def test_big_goal_question_answer_then_team_plan_approval(self):
        store=Store(self.root/'brief.sqlite3');orch=Orchestrator(store,self.workspace);team=Team(store)
        goal=orch.draft('익숙한 규칙과 다른 체스 앱',{})
        roles=[{'id':key,**value} for key,value in team.settings()['roles'].items()]
        brief={'summary':'필요한 결정을 먼저 확인합니다.','roles':roles,'questions':[{'id':'rule','question':'어떤 규칙을 바꿀까요?'}],'steps':[],'acceptance':'','verification':[]}
        with patch.object(orch,'process',return_value={'status':'succeeded','exit_code':0,'brief':brief,'plan_steps':[]}):orch.work(goal['id'],goal['version'],approve=True)
        goal=orch.goal(goal['id']);self.assertEqual(goal['status'],'waiting_answer');self.assertFalse(goal['worker']);self.assertFalse(goal.get('approved_at'))
        request=team.send(orch,goal['id'],{'version':goal['version'],'kind':'answer','body':'폰이 처음부터 두 칸 이동할 수 없게','client_id':'answer'})
        duplicate=team.send(orch,goal['id'],{'version':goal['version'],'kind':'answer','body':'폰이 처음부터 두 칸 이동할 수 없게','client_id':'answer'});self.assertTrue(duplicate['duplicate'])
        brief.update(summary='구현·평가·검증 팀을 추천합니다.',questions=[],acceptance='폰은 한 칸만 이동',verification=[[sys.executable,'-m','unittest']],steps=[{'title':'구현','role':'implement','prompt':'rules.py만 수정','files':['rules.py']},{'title':'검증','role':'verify','prompt':'검증','files':['rules.py']}])
        with patch.object(orch,'process',return_value={'status':'succeeded','exit_code':0,'brief':brief,'plan_steps':brief['steps']}):orch.work(goal['id'],request['version'],approve=True)
        goal=orch.goal(goal['id']);self.assertEqual(goal['status'],'ready');self.assertFalse(goal['planning']);self.assertEqual(goal['acceptance'],'폰은 한 칸만 이동');self.assertEqual(len(goal['answers']),1);self.assertFalse(goal.get('approved_at'));self.assertEqual(goal['verification'],brief['verification'])
    def test_structured_question_response_is_success_not_invalid_plan(self):
        store=Store(self.root/'question.sqlite3');orch=Orchestrator(store,self.workspace);goal=orch.draft('큰 목표',{})
        roles=[{'id':key,**value} for key,value in Team(store).settings()['roles'].items()]
        brief={'summary':'질문','roles':roles,'questions':[{'id':'rule','question':'어떤 규칙인가요?'}],'acceptance':'','verification':[],'steps':[]}
        state=orch.update(goal['id'],{'worker':'test','execution_settings':Team(store).settings()},goal['version']);run,_=orch.run(state,state['steps'][0])
        events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(brief)}},{'type':'turn.completed'}]
        script=';'.join('print('+repr(json.dumps(event))+')' for event in events)
        result=orch.process([sys.executable,'-c',script],str(self.workspace),goal['id'],'test',run)
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['error'],'');self.assertEqual(result['brief']['questions'],brief['questions'])

    def test_assessment_can_rewind_product_planner_and_all_following_steps(self):
        store=Store(self.root/'loop.sqlite3');team=Team(store);cfg=team.settings();cfg['autopilot']=True;cfg['max_rounds']=1
        cfg['roles']['product']={'name':'기획자','kind':'plan','model':'','instructions':'','skills':[]};cfg['roles']['founder']={'name':'사업평가','kind':'assess','model':'','instructions':'','skills':[]};team.save(cfg)
        orch=Orchestrator(store,self.workspace);steps=[{'role':r,'title':r,'prompt':'approved goal','files':['rules.py']} for r in ['product','implement','founder','verify']]
        goal=orch.create('Goal',{'acceptance':'pass','steps':steps,'verification':[[sys.executable,'-c','assert True']]});calls=[]
        def process(argv,cwd,key,worker,run_id=None,prompt=None):
            if not run_id:return {'status':'succeeded','exit_code':0}
            with store.connect() as db:run=json.loads(db.execute('SELECT data FROM executions WHERE id=?',(run_id,)).fetchone()[0])
            role=run['role'];calls.append(role);fail=role=='founder' and calls.count('founder')==1
            if role=='product':
                self.assertIn('read-only',argv);self.assertIn('planning-role task',prompt);self.assertIn('Lack of implementation is expected',prompt)
            orch.run_update(run_id,review_summary='실제 평가',handoff={'target':'product','kind':'revision','body':'규칙의 차별성을 명확히 설명','files':['rules.py']} if fail else None)
            return {'status':'failed' if fail else 'succeeded','exit_code':0}
        with patch.object(orch,'process',side_effect=process):orch.work(goal['id'],goal['version'],approve=True)
        self.assertEqual(calls,['product','implement','founder','product','implement','founder','verify']);self.assertEqual(orch.goal(goal['id'])['status'],'done');self.assertEqual(orch.goal(goal['id'])['auto_rounds'],1)

if __name__=='__main__':unittest.main()
