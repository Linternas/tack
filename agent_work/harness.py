"""Explicit project-local installation; core Tack remains passive."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from .store import BoardError, Store

MARKER='.tack-orchestration/install.json'
# Shipped before installers recorded the stock skill hash.
LEGACY_SKILL_SHA256='db2ba8d5a8b5b196bd3e126528f6d14a0a18855a01343b1c25c1cce8bb69663f'
EXTENSION_MODULES={'harness.py','harness_cli.py','orchestration.py','orchestration_cli.py','team.py','crewset.py'}
EXTENSION_ASSETS={'goals.html','goals.js','goals.css','plan.schema.json','verdict.schema.json','brief.schema.json','crewset.roles.json','crewset.LICENSE'}

def enabled(workspace):
    root=Path(workspace).resolve();path=root/MARKER
    if not path.is_file():return False
    try:data=json.loads(path.read_text())
    except (ValueError,OSError):return False
    return data.get('format')==1 and data.get('workspace')==str(root)

def require_enabled(workspace):
    if not enabled(workspace):raise BoardError('이 프로젝트에는 오케스트레이션이 설치되지 않았습니다. tack-orchestration setup PROJECT_FOLDER로 명시적으로 설치하세요.',403)

def install(source,workspace):
    from .setup import replace
    from .projects import resolve
    source=Path(source).resolve();root=Path(workspace).expanduser().resolve()
    if not root.is_dir():raise BoardError('설치할 프로젝트 폴더가 필요합니다.')
    project=resolve(str(root),create=True)
    if Path(project['workspace']).resolve()!=root:raise BoardError('프로젝트 루트 폴더에 설치해주세요.')
    folder=root/'.tack-orchestration'
    marker=folder/'install.json'
    if marker.exists() and not enabled(root):raise BoardError('기존 설치 설정을 보존합니다. 설치 경로를 확인해주세요.')
    skill=source/'templates/tack-orchestration.md';target=root/'.agents/skills/tack-orchestration/SKILL.md'
    installed=json.loads(marker.read_text()) if marker.exists() else {}
    skill_hash=hashlib.sha256(skill.read_bytes()).hexdigest()
    if target.exists():
        current_hash=hashlib.sha256(target.read_bytes()).hexdigest()
        previous_hash=(installed.get('skill_sha256') or LEGACY_SKILL_SHA256) if marker.exists() else skill_hash
        if current_hash not in {previous_hash,skill_hash}:raise BoardError('사용자가 수정한 로컬 오케스트레이션 스킬을 보존합니다.')
    files=sorted(p for p in (source/'agent_work').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    digest=hashlib.sha256()
    for p in files:digest.update(str(p.relative_to(source)).encode());digest.update(p.read_bytes())
    runtime=folder/'releases'/digest.hexdigest()[:20]
    if runtime.exists():
        for p in files:
            dest=runtime/p.relative_to(source)
            if not dest.exists() or dest.read_bytes()!=p.read_bytes():raise BoardError('기존 로컬 런타임이 변경됐습니다. 덮어쓰지 않습니다.')
    else:
        runtime.parent.mkdir(parents=True,exist_ok=True)
        temp=Path(tempfile.mkdtemp(prefix='.install-',dir=runtime.parent))
        try:shutil.copytree(source/'agent_work',temp/'agent_work',ignore=shutil.ignore_patterns('__pycache__','*.pyc'));temp.rename(runtime)
        finally:
            if temp.exists():shutil.rmtree(temp)
    backups=[];launcher=folder/'bin/tack-orchestration'
    if launcher.exists() and 'from agent_work.harness_cli import main' not in launcher.read_text():raise BoardError('다른 로컬 실행기를 보존합니다.')
    command=f'#!{sys.executable}\nimport sys,os\nsys.path.insert(0,{str(runtime)!r})\nos.environ["TACK_HARNESS_WORKSPACE"]={str(root)!r}\nfrom agent_work.harness_cli import main\nmain()\n'
    Store(project['db_path'])
    replace(launcher,command,backups);launcher.chmod(0o755)
    replace(marker,json.dumps({'format':1,'workspace':str(root),'project_id':project['id'],'runtime':str(runtime),'skill_sha256':skill_hash},indent=2)+'\n',backups)
    replace(folder/'.gitignore','*\n',backups)
    replace(target,skill.read_text(),backups)
    return {'workspace':str(root),'project_id':project['id'],'launcher':str(launcher),'runtime':str(runtime),'data':project['db_path'],'backups':backups,'scope':'project only; global agent instructions unchanged'}
