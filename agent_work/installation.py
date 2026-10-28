"""Immutable runtimes and installation inventory, without initial configuration."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from .store import BoardError

EXTENSION_MODULES={'harness.py','harness_cli.py','orchestration.py','orchestration_cli.py','team.py','crewset.py'}
EXTENSION_ASSETS={'goals.html','goals.js','goals.css','plan.schema.json','verdict.schema.json','brief.schema.json','crewset.roles.json','crewset.LICENSE'}
LEGACY_SKILL_SHA256='db2ba8d5a8b5b196bd3e126528f6d14a0a18855a01343b1c25c1cce8bb69663f'

def package_version(source):
    value=json.loads((Path(source)/'package.json').read_text()).get('version')
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):raise BoardError('배포본의 version은 major.minor.patch 형식이어야 합니다.')
    return value

def runtime(source,releases,core=False):
    source=Path(source).resolve();releases=Path(releases)
    files=sorted(p for p in (source/'agent_work').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and (not core or p.name not in EXTENSION_MODULES|EXTENSION_ASSETS))
    if not files or not (source/'agent_work/cli.py').is_file():raise BoardError('유효한 Tack 배포본이 아닙니다.')
    digest=hashlib.sha256()
    for p in files:
        if p.is_symlink():raise BoardError('배포본의 심볼릭 링크는 설치하지 않습니다.')
        digest.update(str(p.relative_to(source)).encode());digest.update(p.read_bytes())
    target=releases/digest.hexdigest()[:20]
    if target.exists():
        for p in files:
            dest=target/p.relative_to(source)
            if not dest.is_file() or dest.read_bytes()!=p.read_bytes():raise BoardError('기존 런타임이 변경됐습니다. 덮어쓰지 않습니다.')
        return target
    releases.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.install-',dir=releases))
    try:
        for p in files:
            dest=staging/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        staging.rename(target)
    finally:
        if staging.exists():shutil.rmtree(staging)
    return target

def launcher(runtime,workspace=None):
    command=f'#!{sys.executable}\nimport sys,os\nsys.path.insert(0,{str(runtime)!r})\n'
    if workspace is not None:command+=f'os.environ["TACK_HARNESS_WORKSPACE"]={str(workspace)!r}\nfrom agent_work.harness_cli import main\n'
    else:command+='from agent_work.cli import main\n'
    return command+'main()\n'

def inventory(data):
    path=Path(data)/'harnesses.json'
    if not path.exists():return []
    value=json.loads(path.read_text())
    if not isinstance(value,dict) or value.get('format')!=1 or not isinstance(value.get('projects'),list):raise BoardError('하네스 설치 목록을 확인해주세요.')
    for p in value['projects']:
        if not isinstance(p,dict) or set(p)!={'workspace','project_id','db_path'} or any(not isinstance(v,str) or not v for v in p.values()):raise BoardError('하네스 설치 목록 항목을 확인해주세요.')
    return value['projects']

def register(data,project,backups):
    from .setup import replace
    data=Path(data);data.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (data/'.harnesses.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        entries={p['workspace']:p for p in inventory(data)}
        entry={'workspace':project['workspace'],'project_id':project['id'],'db_path':project['db_path']}
        entries[entry['workspace']]=entry
        replace(data/'harnesses.json',json.dumps({'format':1,'projects':sorted(entries.values(),key=lambda p:p['workspace'])},ensure_ascii=False,indent=2)+'\n',backups)

def activate(changes,backups):
    from .setup import replace
    changes=[(Path(path).resolve(),content,executable) for path,content,executable in changes]
    originals={Path(path):(Path(path).read_bytes(),Path(path).stat().st_mode & 0o777) if Path(path).exists() else None for path,_,_ in changes}
    touched=[]
    try:
        for path,content,executable in changes:
            path=Path(path);touched.append(path);replace(path,content,backups)
            if executable:path.chmod(0o755)
    except BaseException:
        for path in reversed(touched):
            before=originals[path]
            if before is None:path.unlink(missing_ok=True)
            else:
                fd,temp=tempfile.mkstemp(prefix='.rollback-',dir=path.parent)
                try:
                    with os.fdopen(fd,'wb') as output:output.write(before[0])
                    os.chmod(temp,before[1]);os.replace(temp,path)
                finally:Path(temp).unlink(missing_ok=True)
        raise
