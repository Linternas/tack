from contextlib import closing
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from agent_work.setup import setup
from agent_work.update import apply_update,update,_download
from agent_work.projects import resolve
from agent_work.store import Store,BoardError

SOURCE=Path(__file__).resolve().parents[1]
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.home=self.root/'home';self.home.mkdir();self.data=self.home/'state'
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'CODEX_HOME':str(self.home/'.codex'),'AGENT_WORK_HOME':str(self.data),'SHELL':'/bin/zsh'});self.env.start()
        self.core=setup(SOURCE,self.home,dict(os.environ));self.workspace=self.root/'project';self.workspace.mkdir()
        self.project=resolve(self.workspace,create=True);self.store=Store(self.project['db_path']);self.task=self.store.write({'title':'Existing task'})['task']
        self.store.checkpoint({'task_id':self.task['id'],'goal':'Existing session','next_action':'later','status':'active'})
        self.marker,self.binary,self.skill,self.runtime=self.legacy_install(self.project)
        self.source=self.root/'release';self.source.mkdir();shutil.copytree(SOURCE/'agent_work',self.source/'agent_work');shutil.copytree(SOURCE/'templates',self.source/'templates');shutil.copy2(SOURCE/'package.json',self.source/'package.json');shutil.copytree(SOURCE/'bin',self.source/'bin')
        package=json.loads((self.source/'package.json').read_text());package['version']='0.3.0';(self.source/'package.json').write_text(json.dumps(package));(self.source/'agent_work/__init__.py').write_text('__version__ = "0.3.0"\n')
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def run_update(self):return apply_update(self.source,self.home,dict(os.environ))
    def protected(self):
        paths=[self.home/'.zshrc',self.home/'.codex/AGENTS.md',self.home/'.codex/config.toml',self.home/'.codex/hooks.json',self.home/'.codex/skills/handoff-state/SKILL.md']
        return {p:p.read_bytes() for p in paths}

    def legacy_install(self, project):
        root=Path(project['workspace'])
        runtime=root/'.tack-orchestration/releases/old-runtime'
        runtime.mkdir(parents=True)
        (runtime/'old-code').write_text('Retain code for recovery')
        binary=root/'.tack-orchestration/bin/tack-orchestration'
        binary.parent.mkdir()
        binary.write_text(f'#!{sys.executable}\nimport sys,os\nsys.path.insert(0,{str(runtime)!r})\n'
                          f'os.environ["TACK_HARNESS_WORKSPACE"]={str(root)!r}\n'
                          'from agent_work.harness_cli import main\nmain()\n')
        binary.chmod(0o755)
        skill=root/'.agents/skills/tack-orchestration/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('Old stock project skill')
        marker=root/'.tack-orchestration/install.json'
        marker.write_text(json.dumps({'format':1,'workspace':str(root),'project_id':project['id'],
                                      'runtime':str(runtime),'skill_sha256':hashlib.sha256(skill.read_bytes()).hexdigest(),
                                      'version':'0.2.0','data':project['db_path']}))
        inventory=self.data/'harnesses.json'
        saved=json.loads(inventory.read_text()) if inventory.exists() else {'format':1,'projects':[]}
        saved['projects'].append({'workspace':str(root),'project_id':project['id'],'db_path':project['db_path']})
        inventory.write_text(json.dumps(saved))
        return marker,binary,skill,runtime

    def test_core_update_preserves_db_inventory_marker_and_old_runtime(self):
        protected=self.protected()
        with self.store.connect() as db:
            db.executescript("""CREATE TABLE goals (id TEXT PRIMARY KEY,data TEXT);
                    CREATE TABLE executions (id TEXT PRIMARY KEY,data TEXT);
                    CREATE TABLE office_executions (id TEXT PRIMARY KEY,data TEXT);
                    CREATE TABLE team_run_queue (id TEXT PRIMARY KEY,status TEXT);
                    CREATE TABLE orchestration_workspaces (workspace TEXT PRIMARY KEY);""")
            db.execute('INSERT INTO goals VALUES (?,?)',('GOAL-OLD',json.dumps({'status':'done','execution_settings':{'runtime':'herdr-codex-exec'}})))
            db.execute('INSERT INTO executions VALUES (?,?)',('EXEC-OLD',json.dumps({'status':'succeeded','adapter':'herdr-codex-exec'})))
            db.execute('INSERT INTO office_executions VALUES (?,?)',('OFFICE-OLD',json.dumps({'status':'completed'})))
            db.execute('INSERT INTO team_run_queue VALUES (?,?)',('GOAL-OLD','completed'))
            db.execute('INSERT INTO orchestration_workspaces VALUES (?)',(str(self.workspace),))
        db_path=Path(self.project['db_path'])
        preserved=[db_path,self.marker,self.data/'harnesses.json',self.runtime/'old-code']
        before={p:p.read_bytes() for p in preserved}
        archived={self.binary:self.binary.read_bytes(),self.skill:self.skill.read_bytes()}
        with patch('agent_work.setup.setup',side_effect=AssertionError('setup must not run')):
            result=self.run_update()
        self.assertEqual(result['status'],'updated')
        self.assertEqual(result['version'],'0.3.0')
        self.assertEqual(result['errors'],[])
        self.assertEqual(protected,self.protected())
        self.assertEqual(before,{p:p.read_bytes() for p in preserved})
        self.assertTrue(Path(self.core['runtime']).is_dir())
        for name in ('harness.py','orchestration.py','assets/office.js'):
            self.assertFalse((Path(result['runtime'])/'agent_work'/name).exists())
        self.assertEqual(subprocess.check_output([self.core['launcher'],'--version'],text=True).strip(),'Tack 0.3.0')
        self.assertEqual(result['projects'][0]['status'],'retired')
        backup_paths=[Path(p) for p in result['projects'][0]['backups']]
        self.assertCountEqual([p.read_bytes() for p in backup_paths],archived.values())
        self.assertTrue(all(not p.exists() for p in archived))

    def test_same_release_does_not_create_more_backups(self):
        self.run_update();again=self.run_update()
        self.assertEqual(again['status'],'current')
        self.assertEqual(again['backups'],[])
        self.assertEqual(again['projects'][0]['status'],'current')
        self.assertEqual(again['projects'][0]['backups'],[])

    def test_custom_project_skill_and_launcher_are_preserved(self):
        self.skill.write_text('My role instructions')
        self.binary.write_text('#!/bin/sh\n# from agent_work.harness_cli import main\necho custom\n')
        result=self.run_update()
        self.assertEqual(result['projects'][0]['status'],'skipped')
        self.assertIn('echo custom',self.binary.read_text())
        self.assertEqual(self.skill.read_text(),'My role instructions')

    def test_managed_launcher_retires_and_custom_project_skill_stays(self):
        self.skill.write_text('My role instructions')
        result=self.run_update()
        self.assertEqual(result['projects'][0]['status'],'retired')
        self.assertEqual(self.skill.read_text(),'My role instructions')
        self.assertFalse(self.binary.exists())

    def test_running_orchestration_is_skipped_until_stopped(self):
        with self.store.connect() as db:
            db.execute('CREATE TABLE goals (id TEXT PRIMARY KEY,data TEXT)')
            db.execute('INSERT INTO goals VALUES (?,?)',('GOAL-LIVE',json.dumps({'status':'running','worker':'active'})))
        before=self.binary.read_bytes()
        result=self.run_update()
        self.assertEqual(result['projects'][0]['status'],'skipped')
        self.assertEqual(self.binary.read_bytes(),before)
        self.assertEqual(result['status'],'updated')
        with self.store.connect() as db:
            db.execute('UPDATE goals SET data=?',(json.dumps({'status':'paused','worker':''}),))
        again=self.run_update()
        self.assertEqual(again['status'],'current')
        self.assertEqual(again['projects'][0]['status'],'retired')

    def test_symlinked_project_binary_is_preserved(self):
        outside=self.root/'outside-runner'
        self.binary.rename(outside)
        before=outside.read_bytes()
        self.binary.symlink_to(outside)
        result=self.run_update()
        self.assertEqual(result['projects'][0]['status'],'skipped')
        self.assertTrue(self.binary.is_symlink())
        self.assertEqual(before,outside.read_bytes())

    def test_local_storage_inventory_is_unchanged(self):
        local=self.root/'local';local.mkdir()
        project=resolve(local,create=True,local=True)
        Store(project['db_path']);self.legacy_install(project)
        inventory=self.data/'harnesses.json'
        before=inventory.read_bytes()
        result=self.run_update()
        self.assertEqual(len(result['projects']),2)
        self.assertTrue(all(p['status']=='retired' for p in result['projects']))
        self.assertEqual(inventory.read_bytes(),before)
        with closing(sqlite3.connect(self.data/'registry.sqlite3')) as db:
            self.assertFalse(db.execute('SELECT 1 FROM projects WHERE id=?',(project['id'],)).fetchone())

    def test_old_install_without_global_manifest_or_inventory_is_discovered(self):
        (self.home/'.local/lib/tack/install.json').unlink();inventory=self.data/'harnesses.json';inventory.unlink()
        result=self.run_update()
        self.assertEqual(result['projects'][0]['workspace'],str(self.workspace))
        self.assertEqual(result['projects'][0]['status'],'retired')
        self.assertFalse(inventory.exists())

    def test_missing_or_malformed_distribution_does_not_change_launchers(self):
        before=Path(self.core['launcher']).read_bytes();(self.source/'agent_work/update.py').unlink()
        with self.assertRaises(BoardError):self.run_update()
        self.assertEqual(Path(self.core['launcher']).read_bytes(),before)
    def test_downgrade_and_concurrent_update_are_rejected_without_switch(self):
        before=Path(self.core['launcher']).read_bytes();metadata=self.home/'.local/lib/tack/install.json';saved=json.loads(metadata.read_text());metadata.write_text(json.dumps({**saved,'version':'9.0.0'}))
        self.assertEqual(self.run_update()['status'],'newer_installed');self.assertEqual(Path(self.core['launcher']).read_bytes(),before);metadata.write_text(json.dumps(saved))
        with (self.home/'.local/lib/tack/.update.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with self.assertRaises(BoardError):self.run_update()
    def test_one_invalid_project_does_not_prevent_other_installations_from_updating(self):
        self.marker.write_text('invalid json')
        result=self.run_update();self.assertEqual(result['status'],'updated');self.assertEqual(len(result['errors']),1);self.assertEqual(result['projects'][0]['status'],'failed')
    def test_no_workspace_registration_needed_for_cli_update(self):
        outside=self.root/'outside';outside.mkdir()
        r=subprocess.run([self.core['launcher'],'--workspace',str(outside),'update','--source',str(self.source)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['version'],'0.3.0');self.assertFalse((outside/'.agent-work').exists())
    def test_retirement_failure_restores_previous_launcher_and_skill(self):
        from agent_work import setup as setup_module
        actual=setup_module.replace
        before={p:p.read_bytes() for p in [self.binary,self.marker,self.skill]}
        def fail(path,content,backups):
            if Path(path)==self.skill:raise PermissionError('simulated failure')
            return actual(path,content,backups)
        with patch('agent_work.setup.replace',side_effect=fail):result=self.run_update()
        self.assertEqual(len(result['errors']),1)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
    def test_core_activation_failure_preserves_previous_working_cli(self):
        from agent_work import setup as setup_module
        actual=setup_module.replace;binary=Path(self.core['launcher']);metadata=self.home/'.local/lib/tack/install.json';alias=Path(self.core['legacy_launcher']);before={p:p.read_bytes() for p in [binary,metadata,alias,self.binary,self.marker,self.skill]}
        def fail(path,content,backups):
            if Path(path)==binary:raise PermissionError('simulated failure')
            return actual(path,content,backups)
        with patch('agent_work.setup.replace',side_effect=fail),self.assertRaises(PermissionError):self.run_update()
        self.assertEqual(before,{p:p.read_bytes() for p in before})
    def test_inflight_database_transaction_is_skipped_without_failing_core(self):
        with self.store.connect():result=self.run_update()
        self.assertEqual(result['status'],'updated');self.assertEqual(result['projects'][0]['status'],'skipped');self.assertEqual(result['errors'],[])
    def test_default_download_uses_release_tag_and_pinned_sha(self):
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w:gz') as tar:
            content=b'{"version":"0.3.0"}';item=tarfile.TarInfo('tack-sha/package.json');item.size=len(content);tar.addfile(item,io.BytesIO(content))
        folder=self.root/'release-download';folder.mkdir()
        with patch('agent_work.update._json',side_effect=[{'tag_name':'v0.3.0'},{'sha':'b'*40}]) as api,patch('urllib.request.urlopen',return_value=io.BytesIO(archive.getvalue())) as download:
            source,origin=_download(folder)
        self.assertEqual(origin['ref'],'v0.3.0');self.assertTrue(api.call_args_list[-1].args[0].endswith('/commits/v0.3.0'));self.assertTrue(download.call_args.args[0].full_url.endswith('/'+'b'*40));self.assertTrue((source/'package.json').is_file())
    def test_npx_launcher_bootstraps_old_cli_without_running_setup(self):
        binary=Path(self.core['launcher']);binary.write_text('#!'+__import__('sys').executable+'\n# from agent_work.cli import main\nraise SystemExit("old CLI has no update command")\n');binary.chmod(0o755)
        protected=self.protected()
        r=subprocess.run(['node',str(self.source/'bin/tack.cjs'),'update'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['version'],'0.3.0');self.assertEqual(protected,self.protected())
        self.assertFalse((Path(json.loads(r.stdout)['runtime'])/'agent_work/harness.py').exists())
        self.assertEqual(subprocess.check_output([str(binary),'--version'],text=True).strip(),'Tack 0.3.0')
    def test_python_ca_failure_uses_verified_system_curl(self):
        import ssl,urllib.error
        from agent_work.update import _read_http
        def curl(args,**kwargs):
            self.assertNotIn('--insecure',args);self.assertNotIn('-k',args);self.assertEqual(args[args.index('--proto')+1],'=https')
            Path(args[args.index('--output')+1]).write_bytes(b'{"version":"0.3.0"}')
            return subprocess.CompletedProcess(args,0,'200','')
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError(ssl.SSLCertVerificationError(1,'missing CA'))),patch('shutil.which',return_value='/usr/bin/curl'),patch('subprocess.run',side_effect=curl):self.assertEqual(_read_http('https://api.github.com/repos/Linternas/tack',100),b'{"version":"0.3.0"}')
    def test_curl_http_404_remains_a_release_lookup_404(self):
        import ssl,urllib.error
        from agent_work.update import _read_http
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError(ssl.SSLCertVerificationError(1,'missing CA'))),patch('shutil.which',return_value='/usr/bin/curl'),patch('subprocess.run',return_value=subprocess.CompletedProcess([],22,'404','not found')):
            with self.assertRaises(urllib.error.HTTPError) as error:_read_http('https://api.github.com/repos/Linternas/tack/releases/latest',100)
        self.assertEqual(error.exception.code,404)
    def test_download_failure_does_not_call_apply(self):
        import urllib.error
        with patch('agent_work.update._download',side_effect=urllib.error.URLError('offline')),patch('agent_work.update.apply_update') as apply,self.assertRaises(BoardError):update()
        apply.assert_not_called()

    def test_remote_update_cannot_reinstall_legacy_runtime_at_the_same_version(self):
        installed=self.run_update()
        binary=Path(self.core['launcher'])
        metadata=self.home/'.local/lib/tack/install.json'
        before=(binary.read_bytes(),metadata.read_bytes())
        (self.source/'agent_work/harness_cli.py').write_text('Legacy runtime')
        with patch('agent_work.update._download',return_value=(self.source,{'ref':'main'})):
            with self.assertRaises(BoardError) as error:
                update()
        self.assertEqual(error.exception.status,409)
        self.assertEqual((binary.read_bytes(),metadata.read_bytes()),before)
        self.assertFalse((Path(installed['runtime'])/'agent_work/harness_cli.py').exists())

    def test_default_download_pins_release_or_main_commit_and_rejects_traversal(self):
        import urllib.error
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w:gz') as tar:
            content=b'{}';item=tarfile.TarInfo('tack-sha/../outside');item.size=len(content);tar.addfile(item,io.BytesIO(content))
        response=io.BytesIO(archive.getvalue());folder=self.root/'download';folder.mkdir()
        with patch('agent_work.update._json',side_effect=[urllib.error.HTTPError('url',404,'not found',{},None),{'sha':'a'*40}]) as api,patch('urllib.request.urlopen',return_value=response),self.assertRaises(BoardError):_download(folder)
        self.assertTrue(api.call_args_list[-1].args[0].endswith('/commits/main'));self.assertFalse((self.root/'outside').exists())

    def test_update_refreshes_stock_policy_and_preserves_user_instructions(self):
        agents=self.home/'.codex/AGENTS.md'
        agents.write_text('# My rules\n'+agents.read_text()+'\n## Custom\nKeep this.\n')
        policy=self.source/'templates/work-policy.md';policy.write_text(policy.read_text()+'\n- Added retrieval rule.\n')
        skill=self.source/'templates/handoff-state.md';skill.write_text(skill.read_text()+'\nUpdated checkpoint guidance.\n')
        before_config=(self.home/'.codex/config.toml').read_bytes()
        result=self.run_update()
        self.assertIn('Added retrieval rule.',agents.read_text())
        self.assertTrue(agents.read_text().startswith('# My rules\n'))
        self.assertIn('## Custom\nKeep this.',agents.read_text())
        self.assertIn('Updated checkpoint guidance.',(self.home/'.codex/skills/handoff-state/SKILL.md').read_text())
        self.assertEqual(before_config,(self.home/'.codex/config.toml').read_bytes())
        self.assertTrue(result['instructions'])

    def test_update_preserves_custom_managed_block_and_skill(self):
        agents=self.home/'.codex/AGENTS.md';agents.write_text(agents.read_text().replace('## Unified task and session records','## My custom task rules'))
        skill=self.home/'.codex/skills/handoff-state/SKILL.md';skill.write_text('My custom skill')
        before=(agents.read_bytes(),skill.read_bytes())
        result=self.run_update()
        self.assertEqual(before,(agents.read_bytes(),skill.read_bytes()))
        self.assertTrue(any(r['status']=='preserved_custom' for r in result['instructions']))

if __name__=='__main__':unittest.main()
