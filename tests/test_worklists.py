import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from agent_work.store import Store,BoardError,STATUSES,CATEGORIES
from agent_work.worklists import queue_of,QUEUES
from agent_work.records import Records
from agent_work.projects import resolve


class WorklistTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.workspace=self.root/'project';self.workspace.mkdir()
        self.env=patch.dict(os.environ,AGENT_WORK_HOME=str(self.root/'state'));self.env.start()
        self.project=resolve(self.workspace,create=True);self.store=Store(self.project['db_path'])

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def cli(self,*args):
        result=subprocess.run(['python3','-m','agent_work','--workspace',str(self.workspace),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)

    def test_every_legacy_combination_maps_to_one_queue_with_qa_and_issues_exclusive(self):
        for category in CATEGORIES:
            for status in STATUSES:
                queue=queue_of({'category':category,'status':status})
                self.assertIn(queue,QUEUES)
                if status in {'done','archived'}:self.assertEqual(queue,status)
                elif category=='qa':self.assertEqual(queue,'qa')
                elif category=='issue':self.assertEqual(queue,'issue')
        self.assertEqual(queue_of({'category':'task','status':'todo'}),'todo')
        self.assertEqual(queue_of({'category':'task','status':'needs_recheck'}),'issue')
        self.assertEqual(queue_of({'category':'api','status':'todo'}),'waiting')
        self.assertEqual(queue_of({'category':'decision','status':'waiting_decision'}),'waiting')

    def test_native_transitions_and_legacy_writers_agree_and_keep_history(self):
        task=self.store.write({'title':'조사할 문제','queue':'issue'})['task']
        self.assertEqual(task['queue'],'issue')
        task=self.store.write({'version':1,'queue':'todo'},task['id'])['task']
        self.assertEqual(task['queue'],'todo')
        self.assertEqual(task['category'],'task')
        task=self.store.write({'version':2,'status':'in_progress'},task['id'])['task']
        self.assertEqual(task['queue'],'in_progress')
        # Full legacy snapshots can carry an unchanged, now-stale queue field.
        payload={k:task[k] for k in ('title','category','status','queue')}
        task=self.store.write({**payload,'version':3,'status':'done','resolution':'실제 검증'},task['id'])['task']
        self.assertEqual(task['queue'],'done')
        self.assertEqual(len(self.store.detail(task['id'])['history']),4)

    def test_qa_execution_stays_qa_and_completion_requires_actual_evidence(self):
        task=self.store.write({'title':'나중 QA','queue':'qa'})['task']
        active=self.cli('task','start',task['id'],'--version','1')['task']
        self.assertEqual(active['queue'],'qa');self.assertNotIn('status',active)
        task=self.store.detail(task['id'])['task']
        self.assertEqual(task['status'],'in_progress')
        with self.assertRaises(BoardError):self.store.write({'version':2,'queue':'done','resolution':'완료 주장'},task['id'])
        task=self.store.write({'version':2,'queue':'done','resolution':'브라우저 확인','qa_result':'passed','qa':{**task['qa'],'actual':'입력·재조회 확인'}},task['id'])['task']
        self.assertEqual(task['queue'],'done');self.assertEqual(task['category'],'qa')

    def test_legacy_records_are_read_without_rewriting_and_indexed_heads_match(self):
        task=self.store.write({'title':'기존 QA','category':'qa','status':'todo'})['task']
        with self.store.connect() as db:
            data=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]);data.pop('queue')
            db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data),task['id']))
        before=Path(self.store.path).read_bytes()
        readonly=Store(self.store.path,readonly=True)
        self.assertEqual(readonly.detail(task['id'])['task']['queue'],'qa')
        self.assertEqual(Records(readonly).heads()[0]['queue'],'qa')
        self.assertEqual(before,Path(self.store.path).read_bytes())

    def test_cli_counts_and_filters_use_queues_and_default_views_hide_legacy_axes(self):
        todo=self.store.write({'title':'구현','category':'task','status':'todo'})['task']
        qa=self.store.write({'title':'후속 QA','category':'qa','status':'todo'})['task']
        self.store.write({'title':'원인 조사','category':'issue','status':'todo'})
        waiting=self.store.write({'title':'계약','category':'api','status':'waiting_api'})['task']
        counts=self.cli('status')['counts'];self.assertEqual(counts,{'issue':1,'qa':1,'todo':1,'waiting':1})
        self.assertEqual(self.cli('status','--queue','todo')['tasks'][0]['id'],todo['id'])
        self.assertEqual(self.cli('task','show',qa['id'])['task']['queue'],'qa')
        for command in [('task','show',qa['id']),('resume',qa['id'])]:
            task=self.cli(*command)['task'];self.assertNotIn('category',task);self.assertNotIn('status',task)
        match=self.cli('search','후속 QA','--queue','qa')['matches'][0]
        self.assertEqual(match['queue'],'qa');self.assertNotIn('status',match)
        self.assertEqual(self.cli('task','show',waiting['id'],'--full')['task']['status'],'waiting_api')

    def test_invalid_queue_and_imported_completed_qa_cannot_bypass_validation(self):
        with self.assertRaises(BoardError):self.store.write({'title':'오류','queue':'qa/todo'})
        seed=self.root/'seed.json';seed.write_text(json.dumps([{'id':'QA-001','title':'잘못된 완료','category':'qa','status':'done','queue':'done','resolution':'미검증'}]))
        with self.assertRaises(BoardError):Store(self.root/'invalid.sqlite3',seed)


    def test_api_marker_is_derived_from_current_legacy_type_and_survives_queue_changes(self):
        task=self.store.write({'title':'API 요청','category':'api','status':'waiting_api'})['task']
        self.assertTrue(task['is_api_request'])
        task=self.store.write({'version':1,'queue':'todo'},task['id'])['task']
        self.assertEqual(task['category'],'task');self.assertTrue(task['is_api_request'])
        task=self.store.write({'version':2,'queue':'in_progress'},task['id'])['task']
        self.assertTrue(Records(self.store).overview(task['id'])['task']['is_api_request'])
        result=self.cli('status','--api-only');self.assertEqual(result['total_tasks'],1)
        task=self.store.write({'version':3,'is_api_request':False},task['id'])['task']
        self.assertFalse(task['is_api_request']);self.assertEqual(self.cli('status','--api-only')['total_tasks'],0)
        with self.assertRaises(BoardError):self.store.write({'version':4,'is_api_request':'true'},task['id'])

    def test_legacy_api_reads_are_nonmutating_and_explicit_unmarking_is_respected(self):
        task=self.store.write({'title':'계약 요청','category':'api','status':'todo'})['task']
        with self.store.connect() as db:
            data=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]);data.pop('is_api_request')
            db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data),task['id']))
        before=Path(self.store.path).read_bytes()
        reader=Store(self.store.path,readonly=True)
        self.assertTrue(reader.detail(task['id'])['task']['is_api_request'])
        self.assertTrue(Records(reader).heads()[0]['is_api_request'])
        self.assertEqual(before,Path(self.store.path).read_bytes())
        task=self.store.write({'version':1,'is_api_request':False},task['id'])['task']
        self.assertEqual(task['queue'],'waiting');self.assertFalse(task['is_api_request'])
        task=self.store.write({'version':2,'category':'api','next_action':'답변 대기'},task['id'])['task']
        self.assertFalse(task['is_api_request'])
        created=self.cli('task','add','새 API 요청','--api-request','--queue','waiting')['task']
        self.assertTrue(self.cli('task','show',created['id'])['task']['is_api_request'])


    def test_native_investigation_preserves_api_marker_and_explicit_legacy_reclassification_can_remove_it(self):
        task=self.store.write({'title':'API 재확인','category':'api','status':'needs_recheck'})['task']
        self.cli('task','start',task['id'],'--version','1')
        self.assertTrue(self.cli('task','show',task['id'])['task']['is_api_request'])
        task=self.store.write({'version':2,'queue':'waiting'},task['id'])['task']
        self.assertTrue(task['is_api_request'])
        task=self.store.write({'version':3,'category':'decision','status':'waiting_decision'},task['id'])['task']
        self.assertFalse(task['is_api_request'])
