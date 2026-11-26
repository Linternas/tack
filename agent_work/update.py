"""Update installed runtimes without repeating setup or changing agent permissions."""
from contextlib import closing
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from .installation import package_version, runtime, launcher, inventory, activate
from .project_lifecycle import access, idle, state
from .store import BoardError

REPOSITORY='Linternas/tack'
MAX_DOWNLOAD=50*1024*1024

def _read_http(url,limit):
    request=urllib.request.Request(url,headers={'User-Agent':'Tack updater','Accept':'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:raw=response.read(limit+1)
    except urllib.error.URLError as error:
        if not isinstance(error.reason,ssl.SSLCertVerificationError) or not shutil.which('curl'):raise
        # macOS Python may lack a CA bundle; system curl retains normal TLS verification.
        with tempfile.TemporaryDirectory(prefix='tack-tls-') as folder:
            output=Path(folder)/'response'
            result=subprocess.run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--proto-redir','=https','--max-time','30','--max-filesize',str(limit),'--user-agent','Tack updater','--header','Accept: application/vnd.github+json','--output',str(output),'--write-out','%{http_code}',url],capture_output=True,text=True,timeout=35)
            code=int(result.stdout) if result.stdout.isdigit() else 0
            if code>=400:raise urllib.error.HTTPError(url,code,'HTTP request failed',{},io.BytesIO())
            if result.returncode:raise urllib.error.URLError(result.stderr[:500] or 'curl request failed')
            with output.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise BoardError('배포 응답의 크기 제한을 초과했습니다.')
    return raw

def _json(url):
    return json.loads(_read_http(url,2*1024*1024))

def _download(folder):
    api='https://api.github.com/repos/'+REPOSITORY
    try:ref=_json(api+'/releases/latest')['tag_name']
    except urllib.error.HTTPError as error:
        if error.code!=404:raise
        error.close();ref='main'
    from urllib.parse import quote
    sha=_json(api+'/commits/'+quote(ref,safe=''))['sha']
    if not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{40}',sha):raise BoardError('배포 커밋을 확인할 수 없습니다.')
    archive=folder/'release.tar.gz'
    archive.write_bytes(_read_http('https://codeload.github.com/'+REPOSITORY+'/tar.gz/'+sha,MAX_DOWNLOAD))
    source=folder/'source';source.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        total=0;count=0
        for member in tar:
            parts=Path(member.name).parts
            if Path(member.name).is_absolute() or '..' in parts:raise BoardError('안전하지 않은 배포 경로입니다.')
            if len(parts)<2:continue
            relative=Path(*parts[1:])
            if relative.parts[0] not in {'agent_work','templates','package.json'}:continue
            if member.isdir():continue
            if not member.isfile():raise BoardError('배포 링크/특수 파일은 허용하지 않습니다.')
            count+=1;total+=member.size
            if count>5000 or total>MAX_DOWNLOAD:raise BoardError('압축 해제 크기 제한을 초과했습니다.')
            target=source/relative
            if target.exists():raise BoardError('중복된 배포 파일 경로입니다.')
            target.parent.mkdir(parents=True,exist_ok=True)
            with closing(tar.extractfile(member)) as stream,target.open('wb') as output:
                while chunk:=stream.read(65536):output.write(chunk)
    return source,{'repository':REPOSITORY,'ref':ref,'commit':sha}

def _targets(data):
    entries={p['workspace']:p for p in inventory(data)}
    registry=data/'registry.sqlite3'
    if registry.exists():
        with closing(sqlite3.connect(registry.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT a.path AS workspace,p.id AS project_id,p.db_path FROM aliases a JOIN projects p ON p.id=a.project_id UNION SELECT workspace,id AS project_id,db_path FROM projects').fetchall()
        for row in rows:
            if (Path(row['workspace'])/'.tack-orchestration/install.json').is_file():entries.setdefault(row['workspace'],dict(row))
    return sorted(entries.values(),key=lambda p:p['workspace'])

def _retire_project_launcher(entry, data):
    from .setup import ORCHESTRATION_SKILL_HASHES

    root = Path(entry['workspace'])
    result = {'workspace': str(root), 'status': 'current', 'backups': []}
    if not root.is_dir():
        return {**result, 'status': 'skipped', 'reason': '기존 프로젝트 폴더가 없습니다.'}
    marker = root/'.tack-orchestration/install.json'
    binary = root/'.tack-orchestration/bin/tack-orchestration'
    if not marker.is_file():
        return {**result, 'status': 'skipped', 'reason': '기존 오케스트레이션 설치 정보가 없습니다.'}
    if (binary.is_symlink() or binary.parent.is_symlink() or root.resolve() != root
            or marker.resolve() != marker or binary.resolve() != binary):
        raise BoardError('심볼릭 링크가 포함된 프로젝트 설치 경로는 보존합니다.', 409)
    project_id = entry['project_id']
    installed = json.loads(marker.read_text())
    if (not isinstance(installed, dict) or installed.get('format') != 1 or installed.get('workspace') != str(root)
            or installed.get('project_id') != project_id or Path(project_id).name != project_id or project_id in {'.', '..'}):
        raise BoardError('프로젝트 설치 ID와 경로를 확인해주세요.')
    if binary.exists():
        legacy_runtime = installed.get('runtime')
        if not binary.is_file() or not isinstance(legacy_runtime, str):
            raise BoardError('사용자가 변경한 프로젝트 실행기를 보존합니다.', 409)
        shebang, _, command = binary.read_text().partition('\n')
        # Match the old generated launcher exactly except for its Python path.
        expected = (f'import sys,os\nsys.path.insert(0,{legacy_runtime!r})\n'
                    f'os.environ["TACK_HARNESS_WORKSPACE"]={str(root)!r}\n'
                    'from agent_work.harness_cli import main\nmain()\n')
        if not shebang.startswith('#!/') or command != expected:
            raise BoardError('사용자가 변경한 프로젝트 실행기를 보존합니다.', 409)
    skill = root/'.agents/skills/tack-orchestration/SKILL.md'
    changes = [(binary, None, False)] if binary.is_file() else []
    if skill.is_file() and skill.resolve() == skill:
        digest = hashlib.sha256(skill.read_bytes()).hexdigest()
        if digest in ORCHESTRATION_SKILL_HASHES or digest == installed.get('skill_sha256'):
            changes.append((skill, None, False))
    if not changes:
        return result
    db_path = Path(entry['db_path'])
    if db_path.resolve() != db_path or db_path.parent not in {data/'projects'/project_id, root/'.agent-work'}:
        raise BoardError('프로젝트 관리 DB 경로가 아닙니다.')
    if not db_path.is_file():
        raise BoardError('프로젝트 DB가 없습니다.')
    with access(db_path, exclusive=True, create=False), closing(sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True)) as db:
        if state(db)['status'] == 'deleted':
            return {**result, 'status': 'skipped', 'reason': '삭제한 프로젝트입니다.'}
        idle(db, include_sessions=False)
        activate(changes, result['backups'])
    return {**result, 'status': 'retired'}


def managed_instructions(source, home, environ, installed):
    from .setup import block, codex_homes, retired_global_skills, LEGACY_SKILL_HASHES, BEGIN, END
    policy=(source/'templates/work-policy.md').read_text()
    skill=(source/'templates/handoff-state.md').read_text()
    known_policies={'9898a6e5b6d13b1bc69f62c7cde6e407f813d6c59bdcb2786ea002ccfbe34177',installed.get('policy_sha256'),hashlib.sha256(policy.strip().encode()).hexdigest()}
    known_skills=LEGACY_SKILL_HASHES | {installed.get('handoff_state_sha256'),hashlib.sha256(skill.encode()).hexdigest()}
    changes=[];reports=[]
    homes=codex_homes(home,environ)
    for codex in homes:
        agents=codex/'AGENTS.md'
        if agents.is_file():
            original=agents.read_text()
            match=re.search(re.escape(BEGIN)+r'\s*(.*?)\s*'+re.escape(END),original,re.S)
            managed=bool(match and hashlib.sha256(match[1].strip().encode()).hexdigest() in known_policies)
            if managed:changes.append((agents,block(original,policy),False))
            reports.append({'path':str(agents),'status':'managed' if managed else 'preserved_custom'})
        target=codex/'skills/handoff-state/SKILL.md'
        if target.is_file():
            managed=hashlib.sha256(target.read_bytes()).hexdigest() in known_skills
            if managed:changes.append((target,skill,False))
            reports.append({'path':str(target),'status':'managed' if managed else 'preserved_custom'})
    for target in retired_global_skills(homes):
        changes.append((target,None,False))
        reports.append({'path':str(target),'status':'retired'})
    return changes,reports,{'policy_sha256':hashlib.sha256(policy.strip().encode()).hexdigest(),'handoff_state_sha256':hashlib.sha256(skill.encode()).hexdigest()}


def apply_update(source,home,environ):
    source=Path(source).expanduser().resolve();home=Path(home).resolve()
    binary=home/'.local/bin/tack';metadata=home/'.local/lib/tack/install.json'
    if not binary.is_file() or 'from agent_work.cli import main' not in binary.read_text():raise BoardError('전역 Tack을 먼저 한 번 설치해주세요. 다른 실행기는 덮어쓰지 않습니다.')
    installed=json.loads(metadata.read_text()) if metadata.exists() else {}
    if installed and installed.get('format')!=1:raise BoardError('전역 설치 정보를 확인해주세요.')
    version=package_version(source)
    old_version=installed.get('version')
    if old_version and tuple(map(int,version.split('.')))<tuple(map(int,old_version.split('.'))):
        return {'version':old_version,'status':'newer_installed','available_version':version,'projects':[],'errors':[],'note':'설치본이 배포본보다 최신입니다. main/Release 반영 전에는 로컬 소스를 사용하세요.'}
    if any((source/'agent_work'/name).exists() for name in ('harness_cli.py','orchestration.py')):
        raise BoardError('이 배포본에는 이전 오케스트레이션 실행기가 포함되어 있습니다. Tack Core 설치본을 유지합니다.',409)
    init=(source/'agent_work/__init__.py').read_text()
    if not (source/'agent_work/update.py').is_file() or not re.search(r'__version__\s*=\s*[\"\x27]'+re.escape(version)+r'[\"\x27]',init):raise BoardError('이 배포본은 통합 업데이트를 지원하지 않거나 버전이 일치하지 않습니다.')
    data=Path(environ.get('AGENT_WORK_HOME',str(home/'.local/share/agent-work'))).expanduser().resolve()
    folder=home/'.local/lib/tack';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'.update.lock').open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise BoardError('다른 tack update가 실행 중입니다.',409)
        new_runtime=runtime(source,folder/'releases')
        backups=[];command=launcher(new_runtime);changed=binary.read_text()!=command
        changes,instructions,instruction_hashes=managed_instructions(source,home,environ,installed)
        legacy=home/'.local/bin/work'
        if legacy.is_file() and 'from agent_work.cli import main' in legacy.read_text():changes.append((legacy,command,True))
        changes.extend([(metadata,json.dumps({'format':1,'version':version,'runtime':str(new_runtime),**instruction_hashes},indent=2)+'\n',False),(binary,command,True)])
        activate(changes,backups)
        projects=[];errors=[]
        try:
            entries=_targets(data)
        except (BoardError,OSError,ValueError,sqlite3.Error) as error:
            entries=[]
            errors.append({'status':'failed','reason':str(error)})
        for entry in entries:
            try:
                projects.append(_retire_project_launcher(entry,data))
            except (BoardError,OSError,ValueError,sqlite3.Error) as error:
                skipped=isinstance(error,BoardError) and error.status==409
                item={'workspace':entry['workspace'],'status':'skipped' if skipped else 'failed','reason':str(error)}
                projects.append(item)
                if not skipped:errors.append(item)
        return {'version':version,'previous_version':old_version,'status':'updated' if changed else 'current','runtime':str(new_runtime),'backups':backups,'projects':projects,'errors':errors,'instructions':instructions,
                'note':'Tack Core를 적용했습니다. 작업 DB와 기존 실행 이력을 보존했습니다. 이전 실행기·스킬의 백업과 건너뛴 프로젝트는 projects에서 확인할 수 있습니다. 대시보드에 적용하려면 종료 후 다시 시작하세요.'}

def update(source=None):
    if source is not None:return apply_update(source,Path.home(),os.environ)
    try:
        with tempfile.TemporaryDirectory(prefix='tack-update-') as folder:
            downloaded,origin=_download(Path(folder))
            return {**apply_update(downloaded,Path.home(),os.environ),'source':origin}
    except (urllib.error.URLError,tarfile.TarError,KeyError,subprocess.TimeoutExpired) as error:
        if isinstance(error,urllib.error.HTTPError):error.close()
        raise BoardError('배포본 조회/다운로드에 실패했습니다. 기존 설치본은 유지됩니다: '+str(error),503)
