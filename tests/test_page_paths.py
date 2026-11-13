import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.records import Records
from agent_work.projects import resolve


class PagePathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,AGENT_WORK_HOME=str(self.root/'state'));self.env.start()
        self.workspace=self.root/'project';self.workspace.mkdir()
        self.store=Store(resolve(self.workspace,create=True)['db_path'])

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def cli(self,*args):
        result=subprocess.run(['python3','-m','agent_work','--workspace',str(self.workspace),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)

    def test_page_route_roundtrip_cli_and_search(self):
        route='/ctms/workspaces/[id]/workplaces?tab=PageMarker'
        task=self.cli('task','add','작업 위치 확인','--queue','issue','--page',route)['task']
        self.assertEqual(self.cli('task','show',task['id'])['task']['page_path'],route)
        self.assertEqual(self.cli('status','--search','/ctms/workspaces')['tasks'][0]['page_path'],route)
        self.assertEqual(Records(self.store).search('PageMarker')['matches'][0]['task_id'],task['id'])
        full='https://example.test/workspaces/'+('x'*300)
        self.store.write({'version':1,'page_path':full},task['id'])
        head=self.cli('task','show',task['id'])['task'];self.assertIn('page_path',head['truncated_fields'])
        index=self.cli('status')['tasks'][0];self.assertIn('page_path',index['truncated_fields'])
        self.assertLessEqual(len(index['page_path']),241)
        self.assertEqual(self.cli('task','show',task['id'],'--section','page_path','--full')['text'],full)
        self.assertEqual(self.store.detail(task['id'])['task']['page_path'],full)

    def test_legacy_data_reads_an_empty_route_without_rewriting(self):
        task=self.store.write({'title':'이전 작업'})['task']
        with self.store.connect() as db:
            data=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]);data.pop('page_path')
            db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data),task['id']))
        before=Path(self.store.path).read_bytes();reader=Store(self.store.path,readonly=True)
        self.assertEqual(reader.detail(task['id'])['task']['page_path'],'')
        self.assertEqual(Records(reader).heads()[0]['page_path'],'')
        self.assertEqual(before,Path(self.store.path).read_bytes())

    def test_invalid_routes_are_rejected_before_persisting(self):
        for route in ['javascript:alert(1)','//external.test/path','설정 화면','http://[','/path\nnext','https://user:secret@example.test/path','/'+('x'*2000)]:
            with self.subTest(route=route[:30]):
                with self.assertRaises(BoardError):self.store.write({'title':'잘못된 경로','page_path':route})
        self.assertEqual(self.store.all(),[])
        for route in ['', '/', '/ctms/workspaces/[id]/scope1','https://example.test/ctms/workspaces/3']:
            self.assertEqual(self.store.write({'title':'유효한 경로','page_path':route})['task']['page_path'],route)
