from contextlib import closing
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from agent_work.setup import setup
from agent_work.harness import install
from agent_work.installation import inventory
from agent_work.update import apply_update,update,_download
from agent_work.projects import resolve
from agent_work.store import Store,BoardError
from agent_work.orchestration import Orchestrator

SOURCE=Path(__file__).resolve().parents[1]
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.home=self.root/'home';self.home.mkdir();self.data=self.home/'state'
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'CODEX_HOME':str(self.home/'.codex'),'AGENT_WORK_HOME':str(self.data),'SHELL':'/bin/zsh'});self.env.start()
        self.core=setup(SOURCE,self.home,dict(os.environ));self.workspace=self.root/'project';self.workspace.mkdir();self.harness=install(SOURCE,self.workspace)
        self.project=resolve(self.workspace);self.store=Store(self.project['db_path']);self.task=self.store.write({'title':'Existing task'})['task']
        self.store.checkpoint({'task_id':self.task['id'],'goal':'Existing session','next_action':'later','status':'blocked'})
        self.source=self.root/'release';self.source.mkdir();shutil.copytree(SOURCE/'agent_work',self.source/'agent_work');shutil.copytree(SOURCE/'templates',self.source/'templates');shutil.copy2(SOURCE/'package.json',self.source/'package.json');shutil.copytree(SOURCE/'bin',self.source/'bin')
        package=json.loads((self.source/'package.json').read_text());package['version']='0.3.0';(self.source/'package.json').write_text(json.dumps(package));(self.source/'agent_work/__init__.py').write_text('__version__ = "0.3.0"\n')
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def run_update(self):return apply_update(self.source,self.home,dict(os.environ))
    def protected(self):
        paths=[self.home/'.zshrc',self.home/'.codex/AGENTS.md',self.home/'.codex/config.toml',self.home/'.codex/hooks.json',self.home/'.codex/skills/handoff-state/SKILL.md']
        return {p:p.read_bytes() for p in paths}
    def test_update_never_calls_setup_and_preserves_db_configuration_and_old_runtime(self):
        protected=self.protected();before_tasks=self.store.all();before_sessions=self.store.sessions(True)
        with patch('agent_work.setup.setup',side_effect=AssertionError('setup must not run')),patch('agent_work.harness.install',side_effect=AssertionError('project setup must not run')):result=self.run_update()
        self.assertEqual(result['status'],'updated');self.assertEqual(result['version'],'0.3.0');self.assertEqual(result['errors'],[]);self.assertEqual(result['projects'][0]['status'],'updated')
        self.assertEqual(protected,self.protected());self.assertEqual(before_tasks,self.store.all());self.assertEqual(before_sessions,self.store.sessions(True));self.assertTrue(Path(self.core['runtime']).is_dir());self.assertTrue(Path(self.harness['runtime']).is_dir())
        self.assertFalse((Path(result['runtime'])/'agent_work/orchestration.py').exists());self.assertFalse((self.home/'.codex/skills/tack-orchestration/SKILL.md').exists())
        self.assertEqual(subprocess.check_output([self.core['launcher'],'--version'],text=True).strip(),'Tack 0.3.0')
        marker=json.loads((self.workspace/'.tack-orchestration/install.json').read_text());self.assertEqual(marker['version'],'0.3.0');self.assertTrue((Path(marker['runtime'])/'agent_work/orchestration.py').is_file())
    def test_same_release_does_not_create_more_backups(self):
        self.run_update();again=self.run_update();self.assertEqual(again['status'],'current');self.assertEqual(again['backups'],[]);self.assertEqual(again['projects'][0]['backups'],[])
    def test_custom_project_skill_and_user_team_settings_are_preserved(self):
        skill=self.workspace/'.agents/skills/tack-orchestration/SKILL.md';skill.write_text('My role instructions')
        from agent_work.team import Team
        team=Team(self.store);settings=team.settings();settings['roles']['implement']['model']='my-model';settings['roles']['implement']['instructions']='Keep my policy';team.save(settings);before=team.settings()
        result=self.run_update();self.assertEqual(skill.read_text(),'My role instructions');self.assertEqual(result['projects'][0]['skill'],'preserved_custom');self.assertEqual(team.settings(),before)
        self.assertNotEqual(json.loads((self.workspace/'.tack-orchestration/install.json').read_text())['skill_sha256'],__import__('hashlib').sha256(skill.read_bytes()).hexdigest())
    def test_stock_skill_update_and_legacy_marker_without_hash(self):
        marker=self.workspace/'.tack-orchestration/install.json';data=json.loads(marker.read_text());data.pop('skill_sha256');data.pop('version');marker.write_text(json.dumps(data))
        skill=self.workspace/'.agents/skills/tack-orchestration/SKILL.md'
        from agent_work.installation import LEGACY_SKILL_SHA256
        # The known historical stock template is the prefix before goal-specific guidance.
        skill.write_bytes(skill.read_bytes().split(b'\nGoal-specific role guidance')[0]);self.assertEqual(__import__('hashlib').sha256(skill.read_bytes()).hexdigest(),LEGACY_SKILL_SHA256)
        template=self.source/'templates/tack-orchestration.md';template.write_text(template.read_text()+'\nNew release instructions\n')
        self.run_update();self.assertEqual(skill.read_bytes(),template.read_bytes())
    def test_busy_project_is_skipped_then_updated_by_same_command_after_stop(self):
        orch=Orchestrator(self.store,self.workspace);goal=orch.create('Running',{'acceptance':'pass','steps':[{'title':'work','role':'implement','prompt':'test'}],'verification':[['python3','-m','unittest']]});goal=orch.update(goal['id'],{'worker':'active','status':'running'},goal['version'])
        binary=Path(self.harness['launcher']);before=binary.read_bytes();result=self.run_update();self.assertEqual(result['projects'][0]['status'],'skipped');self.assertEqual(binary.read_bytes(),before);self.assertEqual(result['status'],'updated')
        orch.update(goal['id'],{'worker':'','status':'paused'},goal['version']);again=self.run_update();self.assertEqual(again['status'],'current');self.assertEqual(again['projects'][0]['status'],'updated')
    def test_local_storage_inventory_is_updated_without_registering_its_db_globally(self):
        local=self.root/'local';local.mkdir();project=resolve(local,create=True,local=True);Store(project['db_path']);install(SOURCE,local)
        self.assertEqual(len(inventory(self.data)),2);result=self.run_update();self.assertEqual(len(result['projects']),2);self.assertTrue(all(p['status']=='updated' for p in result['projects']))
        with closing(sqlite3.connect(self.data/'registry.sqlite3')) as db:self.assertFalse(db.execute('SELECT 1 FROM projects WHERE id=?',(project['id'],)).fetchone())
    def test_old_install_without_global_manifest_or_inventory_is_discovered(self):
        (self.home/'.local/lib/tack/install.json').unlink();(self.data/'harnesses.json').unlink()
        result=self.run_update();self.assertEqual(result['projects'][0]['workspace'],str(self.workspace));self.assertEqual(result['status'],'updated');self.assertTrue((self.data/'harnesses.json').exists())
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
        marker=self.workspace/'.tack-orchestration/install.json';marker.write_text('invalid json')
        result=self.run_update();self.assertEqual(result['status'],'updated');self.assertEqual(len(result['errors']),1);self.assertEqual(result['projects'][0]['status'],'failed')
    def test_no_workspace_registration_needed_for_cli_update(self):
        outside=self.root/'outside';outside.mkdir()
        r=subprocess.run([self.core['launcher'],'--workspace',str(outside),'update','--source',str(self.source)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['version'],'0.3.0');self.assertFalse((outside/'.agent-work').exists())
    def test_activation_failure_restores_previous_launcher_marker_and_skill(self):
        from agent_work import setup as setup_module
        actual=setup_module.replace
        binary=Path(self.harness['launcher']);marker=self.workspace/'.tack-orchestration/install.json';skill=self.workspace/'.agents/skills/tack-orchestration/SKILL.md'
        before={p:p.read_bytes() for p in [binary,marker,skill]}
        template=self.source/'templates/tack-orchestration.md';template.write_text(template.read_text()+'\nChanged stock\n')
        def fail(path,content,backups):
            if Path(path)==binary:raise PermissionError('simulated failure')
            return actual(path,content,backups)
        with patch('agent_work.setup.replace',side_effect=fail):result=self.run_update()
        self.assertEqual(len(result['errors']),1);self.assertEqual(before,{p:p.read_bytes() for p in before})
    def test_core_activation_failure_preserves_previous_working_cli(self):
        from agent_work import setup as setup_module
        actual=setup_module.replace;binary=Path(self.core['launcher']);metadata=self.home/'.local/lib/tack/install.json';alias=Path(self.core['legacy_launcher']);before={p:p.read_bytes() for p in [binary,metadata,alias]}
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
    def test_default_download_pins_release_or_main_commit_and_rejects_traversal(self):
        import urllib.error
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w:gz') as tar:
            content=b'{}';item=tarfile.TarInfo('tack-sha/../outside');item.size=len(content);tar.addfile(item,io.BytesIO(content))
        response=io.BytesIO(archive.getvalue());folder=self.root/'download';folder.mkdir()
        with patch('agent_work.update._json',side_effect=[urllib.error.HTTPError('url',404,'not found',{},None),{'sha':'a'*40}]) as api,patch('urllib.request.urlopen',return_value=response),self.assertRaises(BoardError):_download(folder)
        self.assertTrue(api.call_args_list[-1].args[0].endswith('/commits/main'));self.assertFalse((self.root/'outside').exists())

if __name__=='__main__':unittest.main()
