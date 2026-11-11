import tempfile,unittest
from pathlib import Path
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce

class JobDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'work.sqlite3');self.work=Workforce(self.store,self.root);self.goal=self.work.create('삭제 동작 검증');self.key=self.goal['id'];self.job=self.work.jobs(self.key)[0]
    def tearDown(self):self.tmp.cleanup()
    def payload(self,job=None):
        job=self.work.job(self.key,(job or self.job)['id'])
        return {'version':job['version'],'goal_version':self.work.goal(self.key)['version']}
    def test_delete_archives_core_task_hides_every_active_view_and_retains_record(self):
        result=self.work.delete_job(self.key,self.job['id'],self.payload())
        self.assertEqual(result['jobs'],[]);self.assertEqual(result['deleted_jobs'][0]['status'],'deleted')
        self.assertEqual(result['goal']['status'],'paused');self.assertEqual(result['scheduler']['queued'],0)
        self.assertEqual(self.work.office()['jobs'],[])
        self.assertEqual(self.store.detail(self.job['id'])['task']['status'],'archived')
        self.assertEqual(self.store.detail(self.goal['task_id'])['task']['status'],'blocked')
        self.assertEqual(self.work.job(self.key,self.job['id'])['deleted_status'],'queued')
    def test_restore_is_not_auto_execution_and_preserves_original_instructions(self):
        self.work.delete_job(self.key,self.job['id'],self.payload())
        result=self.work.delete_job(self.key,self.job['id'],self.payload(),restore=True)
        self.assertEqual(result['deleted_jobs'],[]);self.assertEqual(result['jobs'][0]['instructions'],self.job['instructions'])
        self.assertEqual(result['goal']['status'],'paused');self.assertFalse(result['goal'].get('worker'))
        self.assertEqual(result['runs'],[]);self.assertEqual(self.store.detail(self.job['id'])['task']['status'],'todo')
    def test_restored_implementation_requires_fresh_scope_approval(self):
        with self.store.connect() as db:
            key=self.work.insert(db,self.key,title='개발 업무',employee='implement',instructions='구현',files=['calculator.py'],status='queued')
        job=self.work.job(self.key,key);self.work.delete_job(self.key,key,self.payload(job))
        result=self.work.delete_job(self.key,key,self.payload(job),restore=True)
        restored=next(j for j in result['jobs'] if j['id']==key)
        self.assertEqual(restored['status'],'awaiting_approval');self.assertIsNone(restored['approved_hash'])
    def test_stale_goal_or_job_version_does_not_change_anything(self):
        for field in ('version','goal_version'):
            payload=self.payload();payload[field]-=1
            with self.assertRaises(BoardError) as error:self.work.delete_job(self.key,self.job['id'],payload)
            self.assertEqual(error.exception.status,409)
            self.assertEqual(self.work.job(self.key,self.job['id']),self.job)
    def test_running_goal_and_completed_work_are_protected(self):
        self.work.orch.update(self.key,{'worker':'busy','status':'running'},self.goal['version'])
        with self.assertRaises(BoardError):self.work.delete_job(self.key,self.job['id'],self.payload())
        goal=self.work.goal(self.key);self.work.orch.update(self.key,{'worker':'','status':'paused'},goal['version'])
        with self.store.connect() as db:self.work.change(db,self.job['id'],{'status':'done','result':'verified'})
        with self.assertRaises(BoardError):self.work.delete_job(self.key,self.job['id'],self.payload())
    def test_dependencies_and_linked_questions_are_protected(self):
        with self.store.connect() as db:
            dependent=self.work.insert(db,self.key,title='후속',employee='verify',instructions='검토',depends_on=[self.job['id']],status='queued')
        with self.assertRaises(BoardError) as error:self.work.delete_job(self.key,self.job['id'],self.payload())
        self.assertIn(dependent,str(error.exception))
        with self.store.connect() as db:
            question=self.work.insert(db,self.key,title='동료 질문',employee='verify',instructions='질문',kind='question',parent=self.job['id'],status='queued')
        job=self.work.job(self.key,question)
        with self.assertRaises(BoardError):self.work.delete_job(self.key,question,self.payload(job))
    def test_external_core_dependencies_also_prevent_delete(self):
        task=self.store.write({'title':'다른 Core 작업','blocked_by':[self.job['id']]})['task']
        with self.assertRaises(BoardError) as error:self.work.delete_job(self.key,self.job['id'],self.payload())
        self.assertIn(task['id'],str(error.exception))
    def test_queued_goal_is_cancelled_and_old_verification_invalidated(self):
        from agent_work.run_queue import ensure,overview
        ensure(self.store)
        with self.store.connect() as db:
            db.execute('INSERT INTO team_run_queue VALUES (?,?,?,?,?,?,?,?)',(self.key,self.goal['version'],'queued','',str(self.root.resolve()),1,'now','now'))
            self.work.set_goal(db,self.key,verification_results=[{'status':'succeeded'}])
        result=self.work.delete_job(self.key,self.job['id'],self.payload())
        self.assertEqual(result['goal']['verification_results'],[]);self.assertEqual(overview(self.store,self.key)['status'],'cancelled')
    def test_claimed_queue_slot_cannot_be_deleted_even_before_worker_registration(self):
        from agent_work.run_queue import ensure
        ensure(self.store)
        with self.store.connect() as db:db.execute('INSERT INTO team_run_queue VALUES (?,?,?,?,?,?,?,?)',(self.key,self.goal['version'],'running','',str(self.root.resolve()),1,'now','now'))
        with self.assertRaises(BoardError):self.work.delete_job(self.key,self.job['id'],self.payload())
        self.assertEqual(self.work.job(self.key,self.job['id'])['status'],'queued')
    def test_restoring_non_deleted_or_foreign_job_is_rejected(self):
        with self.assertRaises(BoardError):self.work.delete_job(self.key,self.job['id'],self.payload(),restore=True)
        with self.assertRaises(BoardError) as error:self.work.delete_job(self.key,'LOCAL-9999',self.payload())
        self.assertEqual(error.exception.status,404)
