import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import tomllib
import unittest
from agent_work.setup import setup, config_content, block

SOURCE = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent work setup ')
        self.home = Path(self.temp.name).resolve()
        self.env = {'SHELL': '/bin/zsh'}

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_install_idempotent_and_persistent_cli(self):
        result = setup(SOURCE, self.home, self.env)
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        again = setup(SOURCE, self.home, self.env)
        self.assertEqual(again['backups'], [])
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})
        self.assertEqual(result['runtime'], again['runtime'])
        self.assertTrue((Path(result['runtime']) / 'agent_work/assets/index.html').exists())
        project = self.home / 'project'; project.mkdir()
        env = {**os.environ, 'HOME': str(self.home), 'AGENT_WORK_HOME': result['data']}
        def run(*args):
            r = subprocess.run([result['launcher'], *args], cwd=project, env=env, capture_output=True, text=True, check=True)
            return json.loads(r.stdout)
        task = run('task', 'add', 'installed cli test')['task']
        self.assertEqual(run('task', 'show', task['id'])['task']['title'], 'installed cli test')
        self.assertEqual(Path(result['launcher']).name, 'tack')
        self.assertEqual(run('status')['project']['db_path'], json.loads(subprocess.run(
            [result['legacy_launcher'], 'status'], cwd=project, env=env,
            capture_output=True, text=True, check=True).stdout)['project']['db_path'])

    def test_all_account_homes_and_existing_data_preserved(self):
        homes = [self.home / '.codex', self.home / '.codex-company',
                 self.home / 'custom home', self.home / 'Library/Application Support/orca/codex-accounts/a/home']
        original = 'model = "keep"\n[sandbox_workspace_write]\nnetwork_access = true\nwritable_roots = [\n  "/existing"\n]\n[other]\nvalue = 2\n'
        for home in homes:
            home.mkdir(parents=True); (home / 'config.toml').write_text(original)
            (home / 'AGENTS.md').write_text('# Keep my instructions\n')
        data = self.home / '.local/share/agent-work'; data.mkdir(parents=True)
        db = data / 'registry.sqlite3'; db.write_bytes(b'existing database fixture')
        result = setup(SOURCE, self.home, {**self.env, 'CODEX_HOME': str(homes[2])})
        self.assertEqual(db.read_bytes(), b'existing database fixture')
        self.assertEqual(len(result['codex_homes']), 4)
        for home in homes:
            config = tomllib.loads((home / 'config.toml').read_text())
            expected = tomllib.loads(original)
            expected['sandbox_workspace_write']['writable_roots'].append(str(data))
            self.assertEqual(config, expected)
            self.assertIn('# Keep my instructions', (home / 'AGENTS.md').read_text())
            self.assertEqual(next(home.glob('config.toml.agent-work-*.bak')).read_text(), original)

    def test_conflicting_command_or_custom_skill_fails_before_changes(self):
        command = self.home / '.local/bin/tack'; command.parent.mkdir(parents=True); command.write_text('unrelated command')
        with self.assertRaisesRegex(ValueError, '다른 tack'):
            setup(SOURCE, self.home, self.env)
        self.assertFalse((self.home / '.codex').exists())
        command.unlink()
        skill = self.home / '.codex/skills/handoff-state/SKILL.md'
        skill.parent.mkdir(parents=True); skill.write_text('custom skill')
        with self.assertRaisesRegex(ValueError, '기존 handoff-state'):
            setup(SOURCE, self.home, self.env)
        self.assertFalse((self.home / '.codex/config.toml').exists())
        self.assertEqual(skill.read_text(), 'custom skill')

    def test_invalid_or_profile_config_does_not_partially_install(self):
        for content in ['[broken', 'default_permissions = "custom"\n']:
            p = self.home / '.codex/config.toml'; p.parent.mkdir(exist_ok=True); p.write_text(content)
            with self.assertRaises(ValueError):
                setup(SOURCE, self.home, self.env)
            self.assertEqual(p.read_text(), content)
            self.assertFalse((self.home / '.local/bin/tack').exists())

    def test_preserves_config_and_shell_symlinks(self):
        actual = self.home / 'shared-config.toml'; actual.write_text('model = "keep"\n')
        config = self.home / '.codex/config.toml'; config.parent.mkdir(); config.symlink_to(actual)
        shell = self.home / '.zshrc'; target = self.home / 'shellrc'; target.write_text('# mine\n'); shell.symlink_to(target)
        setup(SOURCE, self.home, self.env)
        self.assertTrue(config.is_symlink()); self.assertTrue(shell.is_symlink())
        self.assertIn('writable_roots', actual.read_text()); self.assertIn('# mine', target.read_text())

    def test_existing_policy_not_duplicated(self):
        p = self.home / '.codex/AGENTS.md'; p.parent.mkdir()
        original = 'Use the globally installed `work` CLI as the current-state source\n'
        p.write_text(original)
        setup(SOURCE, self.home, self.env)
        self.assertEqual(p.read_text(), original)

    def test_data_root_override_is_same_in_every_config(self):
        custom = self.home / 'custom data'
        result = setup(SOURCE, self.home, {**self.env, 'AGENT_WORK_HOME': str(custom)})
        self.assertEqual(result['data'], str(custom))
        config = tomllib.loads((self.home / '.codex/config.toml').read_text())
        self.assertEqual(config['sandbox_workspace_write']['writable_roots'], [str(custom)])

    def test_legacy_install_upgrade_retains_database_and_unrelated_rules(self):
        # The previous release's stock skill is accepted, custom skills still fail.
        skill = (SOURCE / 'templates/handoff-state.md').read_text().replace('Tack', 'Agent Work').replace('`tack', '`work').replace('tack CLI', 'work CLI')
        import hashlib
        from agent_work.setup import LEGACY_SKILL_SHA256
        self.assertEqual(hashlib.sha256(skill.encode()).hexdigest(), LEGACY_SKILL_SHA256)
        target = self.home / '.codex/skills/handoff-state/SKILL.md'
        target.parent.mkdir(parents=True); target.write_text(skill)
        agents = self.home / '.codex/AGENTS.md'
        agents.write_text('# My global rules\nKeep my policy.\n\n## Unified task and session records\nUse the globally installed `work` CLI as the current-state source\n\n## Other rules\nKeep this too.\n')
        result = setup(SOURCE, self.home, self.env)
        self.assertEqual(target.read_text(), (SOURCE / 'templates/handoff-state.md').read_text())
        self.assertIn('Keep my policy.', agents.read_text())
        self.assertIn('Keep this too.', agents.read_text())
        self.assertIn('`tack` CLI', agents.read_text())
        self.assertNotIn('`work` CLI', agents.read_text())
        self.assertEqual(result['data'], str(self.home / '.local/share/agent-work'))

    def test_unrelated_work_command_is_not_overwritten(self):
        legacy = self.home / '.local/bin/work'; legacy.parent.mkdir(parents=True)
        legacy.write_text('someone else owns this')
        result = setup(SOURCE, self.home, self.env)
        self.assertIsNone(result['legacy_launcher'])
        self.assertEqual(legacy.read_text(), 'someone else owns this')
        self.assertTrue(Path(result['launcher']).is_file())

    def test_legacy_managed_policy_marker_is_upgraded_once(self):
        old = '# Before\n<!-- agent-work:begin -->\nold rules\n<!-- agent-work:end -->\n# After\n'
        result = block(old, 'new Tack policy')
        self.assertIn('# Before', result)
        self.assertIn('# After', result)
        self.assertNotIn('agent-work:begin', result)
        self.assertEqual(block(result, 'new Tack policy'), result)

    def test_old_markdown_handoff_rule_is_replaced_without_changing_other_rules(self):
        old = '# My policy\nKeep my rules.\n\n# 11. Handoff State\nUpdate a workspace-scoped state file\ncurrent--<workspace-slug>--<short-hash>.md\n\n# Other policy\nKeep this too.\n'
        result = block(old, 'Use the Tack database.')
        self.assertIn('Keep my rules.', result)
        self.assertIn('Keep this too.', result)
        self.assertNotIn('current--<workspace-slug>', result)
        self.assertIn('Use the Tack database.', result)


if __name__ == '__main__':
    unittest.main()
