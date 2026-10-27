import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from agent_work.store import Store, BoardError
from agent_work.projects import resolve, projects, by_id
from agent_work.migration import migrate, read_snapshot
from agent_work.http import BoardServer
import http.client

class WorkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.workspace=self.root/'project'; self.workspace.mkdir()
        self.env=patch.dict(os.environ,{'AGENT_WORK_HOME':str(self.root/'state')});self.env.start()
    def tearDown(self):
        self.env.stop();self.temp.cleanup()
    def test_readonly_queries_do_not_create_a_registry(self):
        self.assertEqual(projects(),[])
        self.assertFalse((self.root/'state').exists())
    def test_readonly_store_can_resume_but_cannot_write(self):
        store=Store(self.root/'readonly.sqlite3')
        task=store.write({'title':'read only'})['task']
        reader=Store(store.path,readonly=True)
        self.assertEqual(reader.resume(task['id'])['task']['title'],'read only')
        with self.assertRaises(sqlite3.OperationalError):reader.write({'title':'must fail'})
    def test_headless_read_after_backup_in_unwritable_directory(self):
        folder=self.root/'locked';folder.mkdir()
        store=Store(folder/'work.sqlite3')
        task=store.write({'title':'backup then readonly'})['task']
        store.backup()
        folder.chmod(0o555)
        try:
            reader=Store(store.path,readonly=True)
            self.assertEqual(reader.resume(task['id'])['task']['title'],'backup then readonly')
        finally:
            folder.chmod(0o755)
    def test_empty_project_no_seed_and_isolation(self):
        p=resolve(self.workspace,create=True)
        store=Store(p['db_path']);self.assertEqual(store.all(),[])
        store.write({'title':'한 프로젝트','category':'task'})
        other=self.root/'other';other.mkdir()
        q=resolve(other,create=True)
        self.assertNotEqual(p['id'],q['id']);self.assertEqual(Store(q['db_path']).all(),[])
        self.assertEqual(resolve(self.workspace)['id'],p['id'])
    def test_concurrent_registration_has_one_identity(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            ids=list(pool.map(lambda _:resolve(self.workspace,create=True)['id'],range(3)))
        self.assertEqual(len(set(ids)),1);self.assertEqual(len(projects()),1)
    def test_local_storage_without_registry_and_global_conflict(self):
        p=resolve(self.workspace,create=True,local=True)
        self.assertFalse((self.root/'state').exists())
        self.assertEqual(resolve(self.workspace)['db_path'],p['db_path'])
        other=self.root/'global';other.mkdir();resolve(other,create=True)
        with self.assertRaises(BoardError):resolve(other,local=True)
    def test_move_and_worktree_identity(self):
        subprocess.run(['git','init','-q',str(self.workspace)],check=True)
        subprocess.run(['git','-C',str(self.workspace),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','init'],check=True)
        p=resolve(self.workspace,create=True)
        worktree=self.root/'tree'
        subprocess.run(['git','-C',str(self.workspace),'worktree','add','-qb','test',str(worktree)],check=True,capture_output=True)
        self.assertEqual(resolve(worktree)['id'],p['id'])
        moved=self.root/'moved'
        worktree.rename(moved)
        # Explicit link works even when Git still reports the previous worktree path.
        new=self.root/'new';new.mkdir()
        self.assertEqual(resolve(new,link=p['id'])['id'],p['id'])
        self.assertEqual(by_id(p['id'])['workspace'],str(new.resolve()))
    def test_session_conflicts_completion_and_task_independence(self):
        store=Store(self.root/'store.sqlite3');task=store.write({'title':'작업'})['task']
        run=store.checkpoint({'task_id':task['id'],'goal':'구현','next_action':'테스트'})['session']
        store.checkpoint({'version':1,'status':'completed'},run['id'])
        self.assertEqual(store.sessions(),[])
        self.assertEqual(store.detail(task['id'])['task']['status'],'todo')
        with self.assertRaises(BoardError) as error:store.checkpoint({'version':1,'goal':'덮어쓰기'},run['id'])
        self.assertEqual(error.exception.status,409)
        self.assertEqual(len(store.session(run['id'])['checkpoints']),2)
        self.assertEqual(store.sessions(True)[0]['next_action'],'')
    def test_session_validation_no_partial_writes(self):
        store=Store(self.root/'store.sqlite3');task=store.write({'title':'작업'})['task']
        for data in ({'task_id':'MISSING','goal':'goal','next_action':'next'},{'task_id':task['id'],'goal':'goal'}, {'task_id':task['id'],'goal':[]}):
            with self.assertRaises(BoardError):store.checkpoint(data)
        self.assertEqual(store.sessions(True),[])
    def test_migration_preserves_every_original_row_and_archives_notes(self):
        source=Store(self.root/'board.sqlite3');task=source.write({'title':'기존 QA','category':'qa'})['task']
        source.write({'version':1,'status':'in_progress','note':'실제 실행 대기'},task['id'])
        before=read_snapshot(source.path)
        note=self.root/'active.md';note.write_text('# 작업\n- Status: blocked\n## Goal\n- 검증\n## Next Step\n- 사용자 확인\n')
        p=resolve(self.workspace,create=True)
        result=migrate(p,source.path,[note],[note])
        after=read_snapshot(p['db_path'])
        self.assertEqual(after['tasks'][:1],before['tasks'])
        self.assertEqual(after['history'][:2],before['history'])
        self.assertEqual(read_snapshot(source.path),before)
        imported=Store(p['db_path']).sessions()[0]
        self.assertEqual(imported['status'],'blocked');self.assertEqual(imported['next_action'],'- 사용자 확인')
        self.assertEqual(Path(result['files'][0]['archive']).read_bytes(),note.read_bytes())
        with self.assertRaises(BoardError):migrate(p,source.path,[note],[note])
        self.assertEqual(read_snapshot(p['db_path']),after)
    def test_cli_headless_and_stale_version(self):
        def cli(*args,input=None):
            return subprocess.run(['python3','-m','agent_work','--workspace',str(self.workspace),*args],input=input,capture_output=True,text=True)
        r=cli('task','add','CLI 작업');self.assertEqual(r.returncode,0,r.stderr)
        task=json.loads(r.stdout)['task']
        r=cli('task','start',task['id'],'--version','1');self.assertEqual(r.returncode,0,r.stderr)
        r=cli('task','start',task['id'],'--version','1');self.assertEqual(r.returncode,1);self.assertIn('409',r.stderr)
        r=cli('checkpoint',task['id'],'--json','-',input=json.dumps({'goal':'구현','next_action':'검증'}));self.assertEqual(r.returncode,0,r.stderr)
        r=cli('resume',task['id']);self.assertEqual(len(json.loads(r.stdout)['sessions']),1)
    def test_http_project_isolation_and_session_roundtrip(self):
        p=resolve(self.workspace,create=True)
        other=self.root/'other';other.mkdir();q=resolve(other,create=True)
        Store(q['db_path']).write({'title':'다른 프로젝트'})
        store=Store(p['db_path']);task=store.write({'title':'현재 프로젝트'})['task']
        server=BoardServer(('127.0.0.1',0),store,p)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request(method,path,json.dumps(body) if body else None,{'Content-Type':'application/json'})
            r=c.getresponse();data=r.read();status=r.status;c.close();return status,json.loads(data)
        try:
            status,overview=request('GET','/api/projects')
            self.assertEqual(status,200);self.assertEqual(len(overview['projects']),2)
            self.assertTrue(all(item['open_tasks']==1 for item in overview['projects']))
            status,data=request('GET',f'/api/tasks?project={q["id"]}')
            self.assertEqual(status,200);self.assertEqual(data['tasks'][0]['title'],'다른 프로젝트')
            status,data=request('POST','/api/sessions',{'task_id':task['id'],'goal':'goal','next_action':'next'})
            self.assertEqual(status,201);run=data['session']
            status,data=request('PATCH','/api/sessions/'+run['id'],{'version':1,'status':'blocked','blockers':'입력 필요'})
            self.assertEqual(status,200)
            self.assertEqual(request('PATCH','/api/sessions/'+run['id'],{'version':1,'goal':'stale'})[0],409)
            self.assertEqual(request('GET','/api/resume/'+task['id'])[1]['sessions'][0]['blockers'],'입력 필요')
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
