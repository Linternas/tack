import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_work.crewset import catalog,apply,project_roles
from agent_work.store import Store,BoardError
from agent_work.team import Team
from agent_work.orchestration import Orchestrator
from agent_work.harness import install

SOURCE=Path(__file__).resolve().parents[1]
class CrewsetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir()
        self.store=Store(self.root/'work.sqlite3');self.team=Team(self.store,self.workspace);self.orch=Orchestrator(self.store,self.workspace)
    def tearDown(self):self.temp.cleanup()
    def config(self,roles,docs='plans'):
        path=self.workspace/'.crewset/config.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps({'schemaVersion':1,'preset':'product-team','language':'ko','docsDir':docs,'roles':roles}))
    def test_catalog_preserves_seven_upstream_roles_license_and_provenance(self):
        result=catalog();self.assertEqual(result['source']['commit'],'2823b93a39bc89f041aa8cfc27a11c2249edc1de')
        self.assertEqual([r['source_id'] for r in result['roles']],['cto','designer','planner','frontend-app','frontend-web','backend','qa'])
        self.assertEqual([r['id'] for r in result['roles']][-4:],['frontend_app','frontend_web','backend','qa'])
        self.assertIn('MIT License',(SOURCE/'agent_work/assets/crewset.LICENSE').read_text())
        web=next(r for r in result['roles'] if r['id']=='frontend_web');self.assertIn('API 계약과 DTO',web['config']['instructions']);self.assertEqual(web['config']['kind'],'implement')
        self.assertFalse(self.team.exists());self.assertEqual(list(self.workspace.iterdir()),[])
    def test_apply_preserves_models_skills_custom_roles_tasks_and_existing_ids(self):
        settings=self.team.settings();settings['roles']['frontend_web']={'name':'나의웹','kind':'implement','model':'custom-model','instructions':'custom instructions','skills':[]};self.team.save(settings)
        before=copy.deepcopy(self.team.settings());task=self.store.write({'title':'Keep'})['task']
        result=apply(self.team,before['version'],['frontend-web','qa'])
        self.assertEqual(result['retained'],['frontend_web']);self.assertEqual(result['added'],['qa']);self.assertEqual(self.team.settings()['roles']['frontend_web'],before['roles']['frontend_web'])
        self.assertEqual(self.store.detail(task['id'])['task'],task);self.assertTrue(set(before['roles'])<=set(result['settings']['roles']))
        again=apply(self.team,result['settings']['version'],['frontend-web','qa']);self.assertEqual(again['added'],[]);self.assertEqual(again['settings']['version'],result['settings']['version'])
        with self.assertRaises(BoardError):apply(self.team,0,['backend'])
    def test_existing_crewset_config_and_project_notes_import_without_markdown_ledgers(self):
        self.config(['backend','qa']);doc=self.workspace/'plans/sessions/role-backend.md';doc.parent.mkdir(parents=True)
        doc.write_text('<!-- crewset:role-backend:begin schema=1 -->\nlegacy session-writing instructions\n<!-- crewset:role-backend:end -->\n\n## 프로젝트별 메모\nAPI source: src/contracts.py\n')
        before={str(p.relative_to(self.workspace)):p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()}
        result=apply(self.team,0,workspace=self.workspace)
        self.assertEqual(set(result['added']),{'backend','qa'});self.assertIn('src/contracts.py',result['settings']['roles']['backend']['instructions']);self.assertNotIn('legacy session-writing instructions',result['settings']['roles']['backend']['instructions'])
        after={str(p.relative_to(self.workspace)):p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()};self.assertEqual(before,after)
    def test_unsafe_paths_and_invalid_configs_leave_team_unmodified(self):
        for docs in ['../outside','/absolute','C:\\outside']:
            self.config(['qa'],docs)
            with self.subTest(docs=docs),self.assertRaises(BoardError):apply(self.team,0,workspace=self.workspace)
        self.config(['wizard'])
        with self.assertRaises(BoardError):project_roles(self.workspace)
        self.assertEqual(self.team.settings()['version'],0);self.assertFalse(self.team.exists())
    def test_malformed_project_note_boundary_is_not_silently_imported(self):
        self.config(['backend']);doc=self.workspace/'plans/sessions/role-backend.md';doc.parent.mkdir(parents=True);doc.write_text('## 프로젝트별 메모\ncustom\n')
        with self.assertRaises(BoardError):apply(self.team,0,workspace=self.workspace)
        self.assertFalse(self.team.exists())
    def test_goal_recommendation_reuses_profiles_and_preserves_configured_models(self):
        profiles={r['id']:r['config'] for r in catalog()['roles']};cfg=self.team.settings();cfg['roles']['frontend_web']={**profiles['frontend_web'],'model':'my-web-model','instructions':'my existing guidance'};self.team.save(cfg)
        previous=copy.deepcopy(self.team.settings()['roles']['frontend_web'])
        goal=self.orch.draft('A web goal',{})
        roles=[{'id':key,**value} for key,value in self.team.settings()['roles'].items()]+[{'id':'qa',**profiles['qa']}]
        roles=copy.deepcopy(roles);next(r for r in roles if r['id']=='frontend_web').update(model='',instructions='new goal assignment')
        brief={'summary':'Crewset web/qa recommended','roles':roles,'questions':[],'acceptance':'pass','verification':[[sys.executable,'-m','unittest']], 'steps':[{'title':'web','role':'frontend_web','prompt':'scope','files':['web.py']},{'title':'qa','role':'qa','prompt':'check','files':['web.py']}]}
        with patch.object(self.orch,'process',return_value={'status':'succeeded','exit_code':0,'brief':brief,'plan_steps':brief['steps']}):self.orch.work(goal['id'],goal['version'],approve=True)
        self.assertEqual(self.orch.goal(goal['id'])['status'],'ready');self.assertEqual(self.team.settings()['roles']['frontend_web'],previous);self.assertIn('실패, 경계값',self.team.settings()['roles']['qa']['instructions'])
        self.assertIn('implement',self.team.settings()['roles']);self.assertEqual(self.orch.goal(goal['id'])['role_guidance']['frontend_web'],'new goal assignment')
    def test_scoped_planner_output_accepts_unregistered_crewset_role_ids(self):
        goal=self.orch.draft('Web',{'acceptance':'pass','verification':[[sys.executable,'-m','unittest']]})
        steps=[{'title':r,'role':r,'prompt':'scope','files':['web.py']} for r in ['frontend_web','qa']]
        state=self.orch.update(goal['id'],{'worker':'test','execution_settings':self.team.settings()},goal['version']);run,_=self.orch.run(state,state['steps'][0])
        events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'steps':steps})}},{'type':'turn.completed'}]
        script=';'.join('print('+repr(json.dumps(e))+')' for e in events)
        result=self.orch.process([sys.executable,'-c',script],str(self.workspace),goal['id'],'test',run)
        self.assertEqual(result['status'],'succeeded')
        ready=self.orch.accept_plan(goal['id'],'test',result['plan_steps']);self.assertEqual(ready['status'],'ready');self.assertIn('qa',self.team.settings()['roles']);self.assertEqual(self.team.settings()['roles']['frontend_web']['kind'],'implement')

    def test_team_changes_during_planning_are_not_overwritten_by_recommendation(self):
        goal=self.orch.draft('Goal',{});roles=[{'id':key,**value} for key,value in self.team.settings()['roles'].items()]
        brief={'summary':'plan','roles':roles,'questions':[],'acceptance':'pass','verification':[[sys.executable,'-m','unittest']], 'steps':[{'title':'build','role':'implement','prompt':'scope','files':['a.py']},{'title':'verify','role':'verify','prompt':'check','files':['a.py']}]}
        def process(*args,**kwargs):
            settings=self.team.settings();settings['roles']['implement']['model']='user-updated-model';self.team.save(settings)
            return {'status':'succeeded','exit_code':0,'brief':brief,'plan_steps':brief['steps']}
        with patch.object(self.orch,'process',side_effect=process),self.assertRaises(BoardError):self.orch.work(goal['id'],goal['version'],approve=True)
        self.assertEqual(self.team.settings()['roles']['implement']['model'],'user-updated-model');self.assertEqual(len(self.orch.goal(goal['id'])['steps']),1)

    def test_crewset_qa_manual_retry_routes_back_to_frontend_role(self):
        apply(self.team,0,['frontend-web','qa']);calls=[]
        goal=self.orch.create('Web',{'acceptance':'pass','steps':[{'title':r,'role':r,'prompt':'scope','files':['web.py']} for r in ['frontend_web','qa']],'verification':[[sys.executable,'-c','assert True']]})
        def process(argv,cwd,key,worker,run_id=None,prompt=None):
            if not run_id:return {'status':'succeeded','exit_code':0}
            with self.store.connect() as db:run=json.loads(db.execute('SELECT data FROM executions WHERE id=?',(run_id,)).fetchone()[0])
            role=run['role'];calls.append(role);failed=role=='qa' and calls.count('qa')==1
            self.orch.run_update(run_id,review_summary='actual review',handoff=None)
            return {'status':'failed' if failed else 'succeeded','exit_code':0}
        with patch.object(self.orch,'process',side_effect=process):
            self.orch.work(goal['id'],goal['version'],approve=True);current=self.orch.goal(goal['id']);self.assertEqual(current['status'],'failed');self.orch.work(goal['id'],current['version'],approve=True)
        self.assertEqual(calls,['frontend_web','qa','frontend_web','qa']);self.assertEqual(self.orch.goal(goal['id'])['status'],'done')
    def test_local_cli_catalog_and_non_destructive_import(self):
        with patch.dict(os.environ,{'AGENT_WORK_HOME':str(self.root/'data')}):
            result=install(SOURCE,self.workspace);cli=result['launcher']
            run=lambda *args:json.loads(subprocess.check_output([cli,*args],text=True))
            self.assertEqual(len(run('settings','catalog')['roles']),7)
            result=run('settings','crewset','--version','0','--roles','frontend-web,qa');self.assertEqual(set(result['added']),{'frontend_web','qa'})
            self.assertFalse((self.workspace/'plans').exists());self.assertFalse((self.workspace/'AGENTS.md').exists())

if __name__=='__main__':unittest.main()
