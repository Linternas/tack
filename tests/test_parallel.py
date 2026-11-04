import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce
from agent_work.workspaces import Workspaces,Conflict


def result(action='complete',**fields):return {'status':'succeeded','exit_code':0,'team_result':{'action':action,'summary':'actual test result','target':None,'body':'','tasks':[],'acceptance':'','verification':[],'verdict':None,**fields}}


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'db.sqlite3');self.work=Workforce(self.store,self.root)
        settings=self.work.team.settings();settings['roles']['dev2']={**settings['roles']['implement'],'name':'개발2'};self.work.team.save(settings)
        (self.root/'tests').mkdir();(self.root/'tests/test_checks.py').write_text('import unittest\nclass Checks(unittest.TestCase):\n def test_ok(self):self.assertTrue(True)\n')
        self.goal=self.work.create('parallel',verification_text=sys.executable+' -m unittest discover -s tests')
        seed=self.work.jobs(self.goal['id'])[0]
        with self.store.connect() as db:self.work.change(db,seed['id'],{'status':'done','result':'fixture planning'})
    def tearDown(self):self.tmp.cleanup()
    def add(self,employee,file,title='work',deps=None):
        if file and not (self.root/file).exists():(self.root/file).write_text('base')
        with self.store.connect() as db:return self.work.insert(db,self.goal['id'],title=title,employee=employee,instructions='implement scoped file',files=[file] if file else [],depends_on=deps or [])
    def approve(self):self.work.approve(self.goal['id'],self.work.goal(self.goal['id'])['version'])
    def run_team(self,fn):
        with patch.object(self.work,'employee_run',side_effect=fn):self.work.run(self.goal['id'],self.work.goal(self.goal['id'])['version'])
        return self.work.snapshot(self.goal['id'])
    def test_two_writers_overlap_in_separate_copies_and_integrate_disjoint_files(self):
        self.add('implement','a.py');self.add('dev2','b.py');self.approve();barrier=threading.Barrier(2);roots=[]
        def execute(goal,job,worker):
            if job['kind']=='review':return result('review',verdict='approved')
            roots.append(job['execution_workspace']);barrier.wait(timeout=5)
            self.assertNotEqual(Path(job['execution_workspace']).resolve(),self.root)
            (Path(job['execution_workspace'])/job['files'][0]).write_text(job['employee'])
            return result()
        snapshot=self.run_team(execute)
        self.assertEqual(len(set(roots)),2);self.assertEqual((self.root/'a.py').read_text(),'implement');self.assertEqual((self.root/'b.py').read_text(),'dev2')
        self.assertEqual(snapshot['goal']['status'],'awaiting_acceptance')
    def test_same_file_conflict_is_preserved_and_not_silently_overwritten(self):
        self.add('implement','shared.py');self.add('dev2','shared.py');self.approve();barrier=threading.Barrier(2)
        def execute(goal,job,worker):
            if job['kind']=='review':return result('review',verdict='approved')
            barrier.wait(timeout=5);(Path(job['execution_workspace'])/'shared.py').write_text(job['employee']);return result()
        snapshot=self.run_team(execute);conflicts=[j for j in snapshot['jobs'] if j['status']=='conflict']
        self.assertEqual(len(conflicts),1);self.assertEqual(snapshot['goal']['status'],'waiting_answer')
        self.assertNotEqual((self.root/'shared.py').read_text(),conflicts[0]['employee'])
        self.assertEqual((Path(conflicts[0]['staged']['artifact'])/'after/shared.py').read_text(),conflicts[0]['employee'])
    def test_partial_work_survives_colleague_question_before_integration(self):
        self.add('implement','partial.py');self.approve();observed=[]
        def execute(goal,job,worker):
            if job['kind']=='question':
                self.assertEqual((self.root/'partial.py').read_text(),'base');return result(summary='continue')
            if job['kind']=='review':return result('review',verdict='approved')
            target=Path(job['execution_workspace'])/'partial.py'
            if not job['replies']:
                target.write_text('partial');return result('ask',target='plan',body='confirm boundary')
            observed.append(target.read_text());target.write_text('complete');return result()
        snapshot=self.run_team(execute)
        self.assertEqual(observed,['partial']);self.assertEqual((self.root/'partial.py').read_text(),'complete');self.assertEqual(snapshot['goal']['status'],'awaiting_acceptance')
    def test_out_of_scope_change_stays_out_of_main_workspace(self):
        self.add('implement','approved.py');self.approve()
        def execute(goal,job,worker):
            root=Path(job['execution_workspace']);(root/'approved.py').write_text('allowed');(root/'unapproved.py').write_text('blocked');return result()
        snapshot=self.run_team(execute);job=next(j for j in snapshot['jobs'] if j['kind']=='task')
        self.assertEqual(job['status'],'failed');self.assertEqual((self.root/'approved.py').read_text(),'base');self.assertFalse((self.root/'unapproved.py').exists());self.assertTrue(job['staged'])
    def test_dependencies_and_same_employee_have_explicit_queue_reasons(self):
        first=self.add('implement','a.py');second=self.add('implement','b.py',deps=[first]);self.approve()
        with self.store.connect() as db:self.work.change(db,first,{'status':'working'})
        snap=self.work.snapshot(self.goal['id']);job=next(j for j in snap['jobs'] if j['id']==second)
        self.assertIn('선행',job['wait_reason']);self.assertEqual(snap['scheduler']['running'],1)
    def test_token_increments_are_atomic_and_foreign_owner_is_rejected(self):
        goal=self.work.orch.update(self.goal['id'],{'worker':'owner'},self.goal['version'])
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:self.work.orch.add_tokens(goal['id'],'owner',7),range(80)))
        self.assertEqual(self.work.goal(goal['id'])['token_used'],560)
        with self.assertRaises(BoardError):self.work.orch.add_tokens(goal['id'],'foreign',100)
    def test_max_parallel_is_bounded_and_legacy_settings_preserve_it(self):
        settings=self.work.team.settings();settings['max_parallel']=4;self.work.team.save(settings)
        legacy=self.work.team.settings();legacy.pop('max_parallel');self.work.team.save(legacy);self.assertEqual(self.work.team.settings()['max_parallel'],4)
        for invalid in (0,9,True):
            settings=self.work.team.settings();settings['max_parallel']=invalid
            with self.assertRaises(BoardError):self.work.team.save(settings)

    def test_real_child_processes_overlap_and_pause_only_owned_groups(self):
        import subprocess
        self.add('implement','a.py');self.add('dev2','b.py');self.approve()
        fake=self.root/'fake-bin';fake.mkdir();exe=fake/'codex'
        exe.write_text('#!'+sys.executable+'\n'+'''import json,sys,time,uuid
from pathlib import Path
prompt=sys.stdin.read()
job=json.loads(prompt.split('Assigned job: ',1)[1].split('\\nColleagues:',1)[0])
print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)
if job['files']:Path(job['files'][0]).write_text('partial from owned process')
time.sleep(30)
''');exe.chmod(0o755)
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        errors=[]
        def run():
            try:self.work.run(self.goal['id'],self.work.goal(self.goal['id'])['version'])
            except Exception as error:errors.append(error)
        with patch.dict(os.environ,{'PATH':str(fake)+os.pathsep+os.environ['PATH']}):
            thread=threading.Thread(target=run);thread.start()
            try:
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    with self.store.connect() as db:count=db.execute("SELECT COUNT(*) FROM executions WHERE json_extract(data,'$.status')='running'").fetchone()[0]
                    if count==2:break
                    time.sleep(.05)
                self.assertEqual(count,2)
                current=self.work.goal(self.goal['id']);self.work.orch.pause(current['id'],current['version'])
                thread.join(12);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertIsNone(unrelated.poll())
                snap=self.work.snapshot(self.goal['id']);self.assertEqual(snap['goal']['status'],'paused')
                self.assertTrue(all(j['status']=='paused' for j in snap['jobs'] if j['kind']=='task'))
                self.assertEqual((self.root/'a.py').read_text(),'base');self.assertEqual((self.root/'b.py').read_text(),'base')
                self.assertTrue(all(j.get('staged') for j in snap['jobs'] if j['kind']=='task'))
            finally:
                current=self.work.goal(self.goal['id'])
                if thread.is_alive():self.work.orch.pause(current['id'],current['version']);thread.join(12)
                unrelated.terminate();unrelated.wait()


    def test_implementation_cannot_integrate_files_using_a_review_verdict(self):
        self.add('implement','approved.py');self.approve()
        def invalid(goal,job,worker):
            (Path(job['execution_workspace'])/'approved.py').write_text('invalid verdict')
            return result('review',verdict='approved')
        snap=self.run_team(invalid)
        self.assertEqual((self.root/'approved.py').read_text(),'base')
        self.assertEqual(next(j for j in snap['jobs'] if j['kind']=='task')['status'],'failed')


class WorkspaceTests(unittest.TestCase):
    def test_backup_and_conflict_guard_and_resume_never_need_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'code.py').write_text('old');db=root/'db.sqlite3';Store(db);spaces=Workspaces(root,db)
            attempt=spaces.capture('G','J');(Path(attempt['root'])/'code.py').write_text('new');record=spaces.archive(attempt)
            self.assertEqual((root/'code.py').read_text(),'old');spaces.integrate(attempt,record,['code.py'],True)
            self.assertEqual((root/'code.py').read_text(),'new');self.assertEqual((Path(record['artifact'])/'before/code.py').read_text(),'old')
            attempt=spaces.capture('G','J');(Path(attempt['root'])/'code.py').write_text('pending');record=spaces.archive(attempt);(root/'code.py').write_text('external')
            with self.assertRaises(Conflict):spaces.integrate(attempt,record,['code.py'],True)
            self.assertEqual((root/'code.py').read_text(),'external');self.assertEqual((Path(record['artifact'])/'after/code.py').read_text(),'pending')
