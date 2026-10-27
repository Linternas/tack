"""One-command user installation. No package downloads or DB migrations."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import tomllib

BEGIN = '<!-- tack:begin -->'
END = '<!-- tack:end -->'
LEGACY_SKILL_SHA256 = '3237b617c3d5e84221ba32e7139b130b8f98b143fc09a9f24a9a117982bd0d8f'
LEGACY_SKILL_HASHES = {LEGACY_SKILL_SHA256, 'dc48eea68390a248cee2ddbaedb66b06f89db7016284662e45fecd723f4a1214'}


def block(original, content):
    original = original.replace('<!-- agent-work:begin -->', BEGIN).replace('<!-- agent-work:end -->', END)
    managed = BEGIN + '\n' + content.strip() + '\n' + END
    legacy = re.search(r'(?ms)^# 11\. Handoff State\n.*?(?=^# |\Z)', original)
    if legacy and 'workspace-scoped state file' in legacy[0] and 'current--<workspace-slug>' in legacy[0]:
        original = original[:legacy.start()] + original[legacy.end():]
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1 or original.index(BEGIN) > original.index(END):
            raise ValueError('Tack 지침 블록의 경계를 확인해주세요.')
        return original[:original.index(BEGIN)] + managed + original[original.index(END) + len(END):]
    # Upgrade only the task-policy section; retain unrelated global instructions.
    if 'Use the globally installed `work` CLI as the current-state source' in original:
        section = re.search(r'(?ms)^## Unified task and session records\n.*?(?=^## |\Z)', original)
        if section:
            return original[:section.start()] + managed + '\n\n' + original[section.end():]
        return original
    if 'Use the globally installed `tack` CLI as the current-state source' in original:
        return original
    return original.rstrip() + ('\n\n' if original.strip() else '') + managed + '\n'


def config_content(original, data_root):
    before = tomllib.loads(original)
    if before.get('default_permissions') and not before.get('sandbox_mode'):
        raise ValueError('named permissions profile 사용 중입니다. 해당 profile에 데이터 경로 write를 추가한 뒤 설치하세요.')
    expected = tomllib.loads(original)
    roots = expected.setdefault('sandbox_workspace_write', {}).setdefault('writable_roots', [])
    if str(data_root) in roots:
        return original
    roots.append(str(data_root))
    value = 'writable_roots = ' + json.dumps(roots, ensure_ascii=False) + '\n'
    section = re.search(r'(?ms)^\[sandbox_workspace_write\][^\S\n]*(?:#[^\n]*)?\n(.*?)(?=^\[|\Z)', original)
    if section:
        body = section[1]
        # Match multiline arrays as well; TOML parse + full comparison rejects unsafe edits.
        entry = re.search(r'(?ms)^writable_roots\s*=\s*\[.*?\][^\n]*(?:\n|$)', body)
        body = body[:entry.start()] + value + body[entry.end():] if entry else body.rstrip() + '\n' + value
        updated = original[:section.start(1)] + body + original[section.end(1):]
    else:
        updated = original.rstrip() + '\n\n[sandbox_workspace_write]\n' + value
    if tomllib.loads(updated) != expected:
        raise ValueError('설정 표현을 안전하게 병합할 수 없습니다. 원본은 수정하지 않았습니다.')
    return updated


def replace(path, content, backups):
    if path.is_symlink():
        path = path.resolve()
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    if path.exists():
        backup = path.with_name(path.name + '.agent-work-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
        shutil.copy2(path, backup)
        backups.append(str(backup))
    fd, temporary = tempfile.mkstemp(prefix='.agent-work-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def setup(source, home, environ):
    source, home = Path(source), Path(home)
    data = Path(environ.get('AGENT_WORK_HOME', str(home / '.local/share/agent-work'))).expanduser().resolve()
    primary = Path(environ.get('CODEX_HOME', str(home / '.codex'))).expanduser()
    homes = {home / '.codex', primary}
    company = home / '.codex-company'
    if company.is_dir():
        homes.add(company)
    homes.update((home / 'Library/Application Support/orca/codex-accounts').glob('*/home'))
    homes = sorted({p.resolve() for p in homes})
    plan = []
    for codex in homes:
        config = codex / 'config.toml'
        plan.append((config, config_content(config.read_text() if config.exists() else '', data)))
    instructions = (source / 'templates/work-policy.md').read_text()
    skill = (source / 'templates/handoff-state.md').read_text()
    for codex in homes:
        agents = codex / 'AGENTS.md'
        plan.append((agents, block(agents.read_text() if agents.exists() else '', instructions)))
        target = codex / 'skills/handoff-state/SKILL.md'
        if target.exists() and target.read_text() != skill and hashlib.sha256(target.read_bytes()).hexdigest() not in LEGACY_SKILL_HASHES:
            raise ValueError(f'기존 handoff-state 스킬이 다릅니다. 보존을 위해 설치 중단: {target}')
        plan.append((target, skill))
    launcher = home / '.local/bin/tack'
    if launcher.exists() and 'from agent_work.cli import main' not in launcher.read_text():
        raise ValueError(f'다른 tack 명령을 덮어쓰지 않습니다: {launcher}')
    legacy = home / '.local/bin/work'
    keep_legacy = not legacy.exists() or 'from agent_work.cli import main' in legacy.read_text()
    shell = Path(environ.get('SHELL', '/bin/zsh')).name
    if shell not in {'zsh', 'bash', 'sh'}:
        raise ValueError(f'자동 PATH 설정을 지원하지 않는 셸입니다: {shell}')
    rc_files = ['.zshrc'] if shell == 'zsh' else (['.bashrc', '.bash_profile'] if shell == 'bash' else ['.profile'])
    for name in rc_files:
        rc = home / name
        raw = rc.read_text() if rc.exists() else ''
        line = 'export PATH="$HOME/.local/bin:$PATH"'
        plan.append((rc, raw if line in raw else raw.rstrip() + '\n\n# Tack\n' + line + '\n'))
    # Content-addressed runtime survives npm cache eviction and preserves prior versions.
    files = sorted(p for p in (source / 'agent_work').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    digest = hashlib.sha256()
    for p in files:
        digest.update(str(p.relative_to(source)).encode()); digest.update(p.read_bytes())
    runtime = home / '.local/lib/tack/releases' / digest.hexdigest()[:20]
    if runtime.exists():
        for p in files:
            target = runtime / p.relative_to(source)
            if not target.exists() or target.read_bytes() != p.read_bytes():
                raise ValueError(f'기존 runtime 내용이 다릅니다: {runtime}')
    else:
        runtime.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.install-', dir=runtime.parent))
        try:
            shutil.copytree(source / 'agent_work', staging / 'agent_work', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            staging.rename(runtime)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    backups = []
    # Probe the actual data directory, preserving all existing DBs and records.
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, probe = tempfile.mkstemp(prefix='.setup-write-', dir=data)
    os.close(fd); Path(probe).unlink()
    for path, content in plan:
        replace(path, content, backups)
    command = f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(runtime)!r})\nfrom agent_work.cli import main\nmain()\n'
    replace(launcher, command, backups)
    launcher.chmod(0o755)
    if keep_legacy:
        replace(legacy, command, backups)
        legacy.chmod(0o755)
    return {'launcher': str(launcher), 'runtime': str(runtime), 'data': str(data),
            'legacy_launcher': str(legacy) if keep_legacy else None,
            'codex_homes': [str(p) for p in homes], 'backups': backups,
            'next': '프로젝트에서 tack task add "작업 이름". 현재 셸에서는 ~/.local/bin/tack 또는 npx 패키지 명령 사용.',
            'active_agents': '이미 실행 중인 에이전트의 권한은 별도입니다. 필요한 유휴 세션만 동일 CODEX_HOME·세션 ID에 --add-dir 데이터경로로 재개하고 실제 저장을 검증하세요.'}


def main():
    try:
        if sys.platform == 'win32':
            raise ValueError('현재 간편 설치는 macOS/Linux/WSL을 지원합니다. Windows에서는 WSL에서 실행하세요.')
        source = Path(__file__).resolve().parents[1]
        print(json.dumps(setup(source, Path.home(), os.environ), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        print('Tack 설치 실패: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
