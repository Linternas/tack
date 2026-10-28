from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from agent_work.projects import resolve,projects,by_id
from agent_work.project_lifecycle import change,preview,read
from agent_work.store import Store,BoardError
from agent_work.orchestration import Orchestrator
from agent_work.team import Team
from agent_work.http import BoardServer

class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir()
        self.env=patch.dict(os.environ,{'AGENT_WORK_HOME':str(self.root/'state')});self.env.start()
        self.project=resolve(self.workspace,create=True);self.store=Store(self.project['db_path']);self.task=self.store.write({'title':'Keep history'})['task']
        self.source=self.workspace/'source.txt';self.source.write_text('Keep the source')
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def archive(self):return change(self.project,'archive',{'version':1})
    def delete(self,version=2,include=False):return change(self.project,'delete',{'version':version,'confirmation':self.project['name'],'include_backups':include})
    def test_archive_restore_preserves_all_records_and_existing_store_recovers(self):
        session=self.store.checkpoint({'task_id':self.task['id'],'goal':'test','next_action':'wait','status':'blocked'})['session']
        self.store.archive_document(self.source,'work');before=self.store.detail(self.task['id']);self.archive()
        self.assertEqual(projects(),[]);self.assertEqual(projects(True)[0]['status'],'archived');self.assertEqual(self.store.detail(self.task['id']),before)
        self.assertEqual(Store(self.project['db_path']).session(session['id'])['session']['id'],session['id'])
        with self.assertRaises(sqlite3.OperationalError):self.store.write({'title':'No write while archived'})
        change(self.project,'restore',{'version':2});self.assertEqual(self.store.detail(self.task['id']),before);self.assertEqual(projects()[0]['id'],self.project['id'])
        self.store.write({'title':'Writable after restore'});self.assertEqual(self.source.read_text(),'Keep the source')
    def test_stale_missing_and_repeat_versions_are_rejected(self):
        for payload in [{},{'version':True},{'version':0}]:
            with self.assertRaises(BoardError):change(self.project,'archive',payload)
        self.archive()
        for action in ['archive','restore']:
            with self.assertRaises(BoardError):change(self.project,action,{'version':1})
        self.assertEqual(read(self.store.path)['version'],2)
    def test_running_session_goal_and_stale_worker_block_archive(self):
        session=self.store.checkpoint({'task_id':self.task['id'],'goal':'manual','next_action':'continue'})['session']
        with self.assertRaises(BoardError):self.archive()
        self.store.checkpoint({'version':session['version'],'status':'completed'},session['id'])
        orch=Orchestrator(self.store,self.workspace);goal=orch.create('Goal',{'acceptance':'test','steps':[{'title':'build','role':'implement','prompt':'test'}],'verification':[[sys.executable,'-m','unittest']]})
        goal=orch.update(goal['id'],{'worker':'expired-worker','heartbeat':0,'status':'needs_recheck'},goal['version'])
        with self.assertRaises(BoardError):self.archive()
        orch.update(goal['id'],{'worker':'','status':'paused'},goal['version']);self.archive()
    def test_live_transaction_blocks_lifecycle_change(self):
        with self.store.connect():
            with self.assertRaises(BoardError):self.archive()
        self.assertEqual(read(self.store.path)['status'],'active')
    def test_deletion_requires_archive_exact_name_and_backup_choice(self):
        with self.assertRaises(BoardError):self.delete(version=1)
        self.archive()
        for extra in [{'confirmation':'wrong'},{'include_backups':1}]:
            with self.assertRaises(BoardError):change(self.project,'delete',{'version':2,'confirmation':self.project['name'],'include_backups':False,**extra})
        self.assertEqual(read(self.store.path)['status'],'archived')
    def test_delete_removes_sessions_goals_messages_and_documents_preserves_other_project_and_backups(self):
        session=self.store.checkpoint({'task_id':self.task['id'],'goal':'test','next_action':'next','status':'completed'})['session']
        self.store.archive_document(self.source,'work');orch=Orchestrator(self.store,self.workspace)
        goal=orch.create('Goal',{'acceptance':'test','steps':[{'title':'build','role':'implement','prompt':'test'}],'verification':[[sys.executable,'-m','unittest']]})
        run,_=orch.run(goal,goal['steps'][0]);orch.run_update(run,status='failed');Team(self.store).emit(goal['id'],'user','test note')
        backup=self.store.backup();other=self.root/'other';other.mkdir();q=resolve(other,create=True);other_store=Store(q['db_path']);keep=other_store.write({'title':'Other project'})['task']
        self.archive();detail=preview(by_id(self.project['id']));self.assertEqual(detail['counts']['sessions'],1);self.assertEqual(detail['counts']['documents'],1)
        result=self.delete();self.assertEqual(result['status'],'deleted');self.assertTrue(backup.exists());self.assertTrue(result['remaining_backup_paths'])
        with closing(sqlite3.connect(self.store.path)) as db:
            for table in ['tasks','sessions','checkpoints','goals','executions','team_messages','documents','history']:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(other_store.detail(keep['id'])['task'],keep);self.assertEqual(self.source.read_text(),'Keep the source')
        for call in [lambda:by_id(self.project['id']),lambda:self.store.write({'title':'Cannot recreate'}),lambda:Store(self.store.path),lambda:self.store.all()]:
            with self.assertRaises(BoardError):call()
        self.assertEqual([p['id'] for p in projects(True)],[q['id']])
        new=resolve(self.workspace,create=True);self.assertNotEqual(new['id'],self.project['id']);self.assertEqual(Store(new['db_path']).all(),[])
    def test_optional_managed_backup_cleanup_never_follows_symlinks(self):
        backup=self.store.backup();migration=self.store.path.parent/'migration-test';migration.mkdir();(migration/'evidence.txt').write_text('old')
        outside=self.root/'outside';outside.mkdir();(outside/'keep.txt').write_text('keep');(migration/'linked').symlink_to(outside,target_is_directory=True)
        codex=self.workspace/'.codex/sessions';codex.mkdir(parents=True);(codex/'conversation.jsonl').write_text('not Tack data')
        self.archive();result=self.delete(include=True)
        self.assertFalse(backup.exists());self.assertFalse(migration.exists());self.assertEqual(result['remaining_backup_paths'],[])
        self.assertEqual((outside/'keep.txt').read_text(),'keep');self.assertTrue((codex/'conversation.jsonl').exists())
    def test_backup_failure_does_not_delete_database_records(self):
        self.store.backup();self.archive()
        with patch('agent_work.project_lifecycle.shutil.rmtree',side_effect=PermissionError),self.assertRaises(BoardError):self.delete(include=True)
        self.assertEqual(read(self.store.path)['status'],'archived');self.assertEqual(self.store.detail(self.task['id'])['task'],self.task)
    def test_local_project_lifecycle_and_new_identity_after_delete(self):
        workspace=self.root/'local';workspace.mkdir();project=resolve(workspace,create=True,local=True);store=Store(project['db_path']);store.write({'title':'local'})
        change(project,'archive',{'version':1});self.assertEqual(resolve(workspace)['status'],'archived')
        change(project,'restore',{'version':2});change(project,'archive',{'version':3});change(project,'delete',{'version':4,'confirmation':project['name'],'include_backups':False})
        with self.assertRaises(BoardError):resolve(workspace)
        new=resolve(workspace,create=True);self.assertNotEqual(new['id'],project['id']);self.assertNotEqual(new['db_path'],project['db_path']);self.assertEqual(Store(new['db_path']).all(),[])
        with self.assertRaises(BoardError):store.write({'title':'stale'})
    def test_guard_rejects_legacy_writer_opened_before_archive_and_delete(self):
        with closing(sqlite3.connect(self.store.path)) as legacy:
            self.archive()
            with self.assertRaises(sqlite3.IntegrityError):legacy.execute("UPDATE tasks SET data='{}'")
            legacy.rollback();self.delete()
            with self.assertRaises(sqlite3.IntegrityError):legacy.execute("INSERT INTO tasks VALUES ('LOCAL-9999',1,'{}','now','now')")
        with closing(sqlite3.connect(self.store.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
    def test_readonly_legacy_query_does_not_create_lock_file(self):
        lock=Path(str(self.store.path)+'.lifecycle-lock');lock.unlink()
        reader=Store(self.store.path,readonly=True);self.assertEqual(reader.detail(self.task['id'])['task'],self.task);self.assertFalse(lock.exists())
    def test_local_dashboard_filters_archived_and_deleted_current_project(self):
        workspace=self.root/'local-ui';workspace.mkdir();project=resolve(workspace,create=True,local=True);store=Store(project['db_path'])
        server=BoardServer(('127.0.0.1',0),store,project);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def get(path):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('GET',path);r=c.getresponse();data=json.loads(r.read());c.close();return data
        try:
            change(project,'archive',{'version':1});self.assertEqual(get('/api/projects')['projects'],[]);self.assertEqual(len(get('/api/projects?all=1')['projects']),1)
            change(project,'delete',{'version':2,'confirmation':project['name'],'include_backups':False});self.assertEqual(get('/api/projects?all=1')['projects'],[]);self.assertEqual(get('/api/context')['projects'],[])
        finally:server.shutdown();server.server_close();thread.join()
    def test_unreadable_project_does_not_hide_healthy_projects(self):
        workspace=self.root/'damaged';workspace.mkdir();bad=resolve(workspace,create=True);path=Path(bad['db_path']);path.parent.mkdir(parents=True);path.write_bytes(b'not a sqlite database')
        self.assertEqual(len(projects()),2);self.assertEqual(next(p for p in projects() if p['id']==bad['id'])['status'],'unavailable')
        server=BoardServer(('127.0.0.1',0),self.store,self.project);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('GET','/api/projects');r=c.getresponse();data=json.loads(r.read());c.close()
            self.assertEqual(r.status,200);self.assertEqual(len(data['projects']),2);self.assertTrue(next(p for p in data['projects'] if p['id']==bad['id'])['error'])
        finally:server.shutdown();server.server_close();thread.join()
    def test_cli_workflow(self):
        def run(*args):return subprocess.run([sys.executable,'-m','agent_work','--workspace',str(self.workspace),*args],capture_output=True,text=True)
        self.assertEqual(json.loads(run('project','show').stdout)['version'],1)
        self.assertEqual(run('project','archive','--version','1').returncode,0)
        self.assertEqual(json.loads(run('projects').stdout),[]);self.assertEqual(len(json.loads(run('projects','--all').stdout)),1)
        self.assertEqual(run('task','add','blocked').returncode,1)
        self.assertEqual(run('project','restore',self.project['id'],'--version','2').returncode,0)
        self.assertEqual(run('project','archive','--version','3').returncode,0)
        self.assertEqual(run('project','delete','--version','4','--confirm','wrong').returncode,1)
        self.assertEqual(run('project','delete','--version','4','--confirm',self.project['name']).returncode,0)
    def test_http_lifecycle_and_cross_site_guard_and_cached_default_after_deletion(self):
        server=BoardServer(('127.0.0.1',0),self.store,self.project);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,payload=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port);connection.request(method,path,json.dumps(payload) if payload is not None else None,{'Content-Type':'application/json',**(headers or {})});response=connection.getresponse();body=json.loads(response.read());status=response.status;connection.close();return status,body
        base='/api/projects/'+self.project['id']
        try:
            self.assertEqual(request('POST',base+'/archive',{'version':1},{'Origin':'https://evil.invalid'})[0],403)
            self.assertEqual(request('POST',base+'/archive',{'version':1})[0],200)
            self.assertEqual(request('GET','/api/projects')[1]['projects'],[])
            self.assertEqual(request('GET','/api/projects?all=1')[1]['projects'][0]['status'],'archived')
            self.assertEqual(request('PATCH','/api/tasks/'+self.task['id'],{'version':1,'summary':'blocked'})[0],409)
            self.assertEqual(request('GET','/api/tasks')[0],200)
            self.assertEqual(request('POST',base+'/restore',{'version':1})[0],409)
            self.assertEqual(request('POST',base+'/restore',{'version':2})[0],200)
            self.assertEqual(request('POST',base+'/archive',{'version':3})[0],200)
            self.assertEqual(request('POST',base+'/delete',{'version':4,'confirmation':self.project['name'],'include_backups':False})[0],200)
            self.assertEqual(request('GET','/api/tasks')[0],410)
            self.assertEqual(request('GET','/api/projects')[0],200)
            self.assertEqual(request('GET','/api/context')[0],200)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
