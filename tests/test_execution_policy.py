import copy,json,os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce
from agent_work.execution_policy import DEFAULT_PERMISSIONS,permissions,file_reason,command_reason,changes_reason,effective


def response(action='complete',**values):
    return {'status':'succeeded','exit_code':0,'team_result':{**dict(action=action,summary='실제 테스트 결과',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None),**values}}

class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'work.sqlite3');self.w=Workforce(self.store,self.root)
        (self.root/'tests').mkdir();(self.root/'tests/test_ok.py').write_text('import unittest\nclass Check(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.checks=[[sys.executable,'-m','unittest','discover','-s','tests']]
    def tearDown(self):self.tmp.cleanup()
    def settings(self,mode='autopilot',**changes):
        s=self.w.team.settings();s.update(execution_mode=mode,autopilot=mode!='manual',**changes);return self.w.team.save(s)
    def create(self,mode='autopilot',**changes):
        self.settings(mode,**changes);return self.w.create('사칙연산 계산기 CLI')
    def execute(self,g,fn):
        with patch.object(self.w,'employee_run',side_effect=fn):self.w.run(g['id'],self.w.goal(g['id'])['version'])
        return self.w.snapshot(g['id'])
    def proposal(self,files=None,checks=None):
        return response('propose',acceptance='사칙연산 테스트 통과',verification=checks or self.checks,tasks=[{'title':'계산기 구현','employee':'implement','instructions':'사칙연산 계산기와 테스트 구현','files':files or ['calculator.py'],'depends_on':[]}])

    def test_legacy_boolean_maps_to_approved_never_new_autopilot(self):
        self.w.team.migrate()
        legacy=self.w.team.settings();legacy.pop('execution_mode');legacy.pop('permissions');legacy['autopilot']=True;legacy.pop('version')
        with self.store.connect() as db:db.execute('INSERT INTO team_settings VALUES (1,1,?,?)',(json.dumps(legacy),'fixture'))
        self.assertEqual(self.w.team.settings()['execution_mode'],'approved')
        self.assertEqual(self.w.team.settings()['permissions'],DEFAULT_PERMISSIONS)
        s=self.w.team.settings();s['roles']['implement']['instructions']='preserve policy';self.w.team.save(s)
        self.assertEqual(self.w.team.settings()['execution_mode'],'approved')
        self.settings('manual')
        self.assertEqual(effective({'execution_settings':{'autopilot':True}},self.w.team.settings())['execution_mode'],'manual')

    def test_all_three_modes_roundtrip_and_settings_version_is_enforced(self):
        for mode in ('manual','approved','autopilot'):
            s=self.settings(mode);self.assertEqual(self.w.team.settings()['execution_mode'],mode)
            with self.assertRaises(BoardError):self.w.team.save({**s,'version':s['version']-1})
        for value in ('unknown',[],True):
            with self.assertRaises(BoardError):self.w.team.save({**self.w.team.settings(),'execution_mode':value})

    def test_permissions_reject_traversal_invalid_types_and_empty_command_prefix(self):
        for change in ({'write_paths':['../outside']},{'protected_paths':['/etc']},{'write_paths':['src/*']},{'network':1},{'check_prefixes':[[]]}):
            with self.assertRaises(BoardError):permissions({**copy.deepcopy(DEFAULT_PERMISSIONS),**change})
        self.assertTrue(file_reason(['.env.production'],DEFAULT_PERMISSIONS,self.root))
        self.assertTrue(file_reason(['src2/file.py'],{**DEFAULT_PERMISSIONS,'write_paths':['src']},self.root))
        self.assertFalse(file_reason(['src/file.py'],{**DEFAULT_PERMISSIONS,'write_paths':['src']},self.root))
        self.assertTrue(command_reason(['python3','-c','print(1)'],DEFAULT_PERMISSIONS,True))
        self.assertFalse(command_reason(self.checks[0],DEFAULT_PERMISSIONS,True))

    def test_old_goals_cannot_gain_autopilot_from_project_settings(self):
        g=self.create('manual');self.settings('autopilot')
        snap=self.execute(g,lambda *args:self.proposal())
        self.assertEqual(snap['goal']['status'],'awaiting_approval')
        self.assertTrue(all(not j.get('automatic_approval') for j in snap['jobs']))
        self.assertEqual(effective({'execution_settings':{'autopilot':True}},self.w.team.settings())['execution_mode'],'approved')

    def test_manual_and_approved_modes_both_wait_for_initial_plan_approval(self):
        for mode in ('manual','approved'):
            g=self.create(mode);snap=self.execute(g,lambda *args:self.proposal())
            self.assertEqual(snap['goal']['status'],'awaiting_approval')
            self.assertFalse((self.root/'calculator.py').exists())

    def test_autopilot_stops_on_forbidden_files_and_unapproved_verification(self):
        for files,checks in [(['.env'],self.checks),(['calculator.py'],[[sys.executable,'-c','assert True']])]:
            g=self.create();snap=self.execute(g,lambda *args:self.proposal(files,checks))
            self.assertEqual(snap['goal']['status'],'awaiting_approval')
            pending=next(j for j in snap['jobs'] if j['kind']=='task');self.assertTrue(pending['result']);self.assertIsNone(pending['approved_hash'])
            if files==['.env']:
                with self.assertRaises(BoardError):self.w.approve(g['id'],snap['goal']['version'])
            else:
                approved=self.w.approve(g['id'],snap['goal']['version']);self.assertFalse(next(j for j in approved['jobs'] if j['kind']=='task')['automatic_approval'])

    def test_permission_reduction_blocks_already_automatically_approved_job(self):
        g=self.create();s=self.w.team.settings();g=self.w.orch.update(g['id'],{'worker':'owner','execution_settings':s},g['version'])
        seed=self.w.jobs(g['id'])[0];self.w.apply(g['id'],seed['id'],self.proposal()['team_result'],'owner');self.w.auto_approve(g['id'],'owner')
        task=next(j for j in self.w.jobs(g['id']) if j['kind']=='task');self.assertTrue(task['automatic_approval'])
        s=self.w.team.settings();s['permissions']['write_paths']=['src'];self.w.team.save(s)
        from agent_work.execution_policy import job_reason
        self.assertTrue(job_reason(self.w.goal(g['id']),task,self.w.team.settings(),self.root,True))
        self.assertTrue(changes_reason({'calculator.py':{'before':{},'after':None}},self.w.goal(g['id']),self.w.team.settings()))

    def test_autopilot_preserves_material_questions(self):
        g=self.create();snap=self.execute(g,lambda *args:response('ask',target='user',body='유료 API 결제가 필요합니다. 승인하시나요?'))
        self.assertEqual(snap['goal']['status'],'waiting_answer');self.assertEqual(snap['jobs'][0]['status'],'waiting_user')

    def test_autopilot_real_cli_process_to_verified_result_without_approval_click(self):
        g=self.create();binary=self.root/'bin';binary.mkdir();fake=binary/'codex'
        code="""import json,sys,uuid
from pathlib import Path
p=sys.stdin.read();job=json.loads(p.split('Assigned job: ',1)[1].split('\\nColleagues:',1)[0]);r=dict(action='complete',summary='actual calculator implementation',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None)
if job['kind']=='plan':
 r.update(action='propose',acceptance='사칙연산과 예외 테스트 통과',verification=[[sys.executable,'-m','unittest','discover','-s','tests']],tasks=[dict(title='계산기 구현',employee='implement',instructions='사칙연산 CLI 구현과 테스트',files=['calculator.py','tests/test_calculator.py'],depends_on=[])])
elif job['kind']=='task':
 Path('calculator.py').write_text('import sys,operator\\nOPS={"+":operator.add,"-":operator.sub,"*":operator.mul,"/":operator.truediv}\\ndef calculate(a,op,b): return OPS[op](a,b)\\nif __name__=="__main__": print(calculate(float(sys.argv[1]),sys.argv[2],float(sys.argv[3])))\\n')
 Path('tests/test_calculator.py').write_text('import unittest\\nfrom calculator import calculate\\nclass CalculatorTests(unittest.TestCase):\\n def test_operations(self):\\n  for op,value in [("+",5),("-",-1),("*",6),("/",2/3)]: self.assertEqual(calculate(2,op,3),value)\\n def test_zero(self):\\n  with self.assertRaises(ZeroDivisionError): calculate(1,"/",0)\\n')
else:
 import subprocess
 subprocess.run([sys.executable,'-m','unittest','discover','-s','tests'],check=True,stdout=sys.stderr)
 r.update(action='review',verdict='approved')
print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(r)}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':100}}))
"""
        fake.write_text('#!'+sys.executable+'\n'+code);fake.chmod(0o755)
        with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):self.w.run(g['id'],g['version'])
        snap=self.w.snapshot(g['id'])
        self.assertEqual(snap['goal']['status'],'awaiting_acceptance',[(j['status'],j['result']) for j in snap['jobs']])
        self.assertEqual(snap['goal']['worker'],'');self.assertEqual(len(snap['runs']),3)
        self.assertTrue(next(j for j in snap['jobs'] if j['kind']=='task')['automatic_approval'])
        self.assertTrue(all(c['status']=='succeeded' for c in snap['goal']['verification_results']))
        out=subprocess.check_output([sys.executable,str(self.root/'calculator.py'),'8','/','2'],text=True)
        self.assertEqual(out.strip(),'4.0')
        self.assertFalse(any(m['sender']=='user' and m['kind']=='decision' for m in snap['messages']))

    def test_autopilot_cannot_reapprove_revision_after_round_limit(self):
        g=self.create(max_rounds=1);implementations=[]
        def execute(goal,job,worker):
            if job['kind']=='plan':return self.proposal()
            if job['kind']=='review':return response('review',verdict='changes_requested',summary='구현 수정 필요')
            implementations.append(job['id']);(Path(job['execution_workspace'])/'calculator.py').write_text('revision='+str(len(implementations)))
            return response()
        snap=self.execute(g,execute)
        self.assertEqual(len(implementations),2)
        self.assertEqual(snap['goal']['auto_rounds'],1)
        self.assertEqual(snap['goal']['status'],'awaiting_approval')
        self.assertEqual(next(j for j in snap['jobs'] if j['kind']=='task')['status'],'awaiting_approval')

    def test_network_setting_is_explicit_and_cannot_expand_existing_goal_grant(self):
        g=self.create();settings=self.w.team.settings();settings['permissions']['network']=True;self.w.team.save(settings)
        settings=self.w.team.settings();g=self.w.orch.update(g['id'],{'worker':'owner','execution_settings':settings},g['version'])
        argv_seen=[]
        def process(argv,*args):argv_seen.append(argv);return response()
        with patch.object(self.w.orch,'process',side_effect=process):self.w.employee_run(g,self.w.jobs(g['id'])[0],'owner')
        self.assertIn('sandbox_workspace_write.network_access=false',argv_seen[0])

    def test_disabled_check_permission_blocks_actual_process_launcher(self):
        g=self.create();s=self.w.team.settings();s['permissions']['run_checks']=False;self.w.team.save(s)
        with patch('agent_work.orchestration.subprocess.Popen',side_effect=AssertionError('must not execute')):
            with self.assertRaises(BoardError):self.w.orch.process(self.checks[0],str(self.root),g['id'],'owner')

    def test_missing_usage_prevents_further_autopilot_dispatch(self):
        g=self.create();binary=self.root/'fake-bin';binary.mkdir();fake=binary/'codex'
        result=self.proposal()['team_result']
        fake.write_text('#!'+sys.executable+'\nimport sys,json;sys.stdin.read();print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'+repr(json.dumps(result))+'}}));print(json.dumps({"type":"turn.completed"}))\n');fake.chmod(0o755)
        with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):self.w.run(g['id'],g['version'])
        snap=self.w.snapshot(g['id']);self.assertEqual(snap['goal']['status'],'paused');self.assertEqual(snap['goal']['error'],'usage_unknown')
        self.assertEqual(len(snap['runs']),1);self.assertFalse((self.root/'calculator.py').exists())

    def test_symlink_and_normalized_path_cannot_bypass_protected_or_allowed_paths(self):
        (self.root/'private').mkdir();(self.root/'src').mkdir();(self.root/'src/link').symlink_to(self.root/'private',target_is_directory=True)
        p={**copy.deepcopy(DEFAULT_PERMISSIONS),'write_paths':['src'],'protected_paths':['private']}
        self.assertTrue(file_reason(['src/link/secret.py'],p,self.root))
        self.assertTrue(file_reason(['src/../private/secret.py'],p,self.root))
        self.assertFalse(file_reason(['.env.example'],{**p,'write_paths':['.'],'protected_paths':[]},self.root))

    def test_final_verification_cannot_mutate_integrated_source(self):
        p=copy.deepcopy(DEFAULT_PERMISSIONS);p['check_prefixes']=[[sys.executable,'-c']]
        g=self.create(permissions=p)
        checks=[[sys.executable,'-c','from pathlib import Path;Path("calculator.py").write_text("tampered")']]
        def execute(goal,job,worker):
            if job['kind']=='plan':return self.proposal(checks=checks)
            if job['kind']=='review':return response('review',verdict='approved')
            (Path(job['execution_workspace'])/'calculator.py').write_text('original source');return response()
        snap=self.execute(g,execute)
        self.assertEqual(snap['goal']['status'],'failed')
        self.assertIn('소스를 변경',snap['goal']['verification_results'][0]['error'])
        self.assertEqual((self.root/'calculator.py').read_text(),'original source')

    def test_cli_create_launches_only_new_autopilot_goals(self):
        from argparse import Namespace
        from agent_work.orchestration_cli import dispatch
        args=Namespace(command='team',team_action='create',title='계산기 CLI',acceptance='',verification_text='')
        for mode in ('manual','approved','autopilot'):
            self.settings(mode)
            with patch('agent_work.orchestration_cli.start',return_value={'accepted':True}) as start:
                data=dispatch(args,{'workspace':str(self.root)},self.store,lambda value:None)
                self.assertEqual(start.call_count,1 if mode=='autopilot' else 0)
                self.assertEqual('launch' in data,mode=='autopilot')
