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
from .installation import package_version, runtime, launcher, inventory, register, activate, LEGACY_SKILL_SHA256
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

def _harness(source,entry,data):
    root=Path(entry['workspace']).resolve();marker=root/'.tack-orchestration/install.json'
    if not root.is_dir() or not marker.is_file():return {'workspace':str(root),'status':'skipped','reason':'설치 폴더가 없거나 하네스가 설치되지 않았습니다.'}
    installed=json.loads(marker.read_text())
    if installed.get('format')!=1 or installed.get('workspace')!=str(root) or installed.get('project_id')!=entry['project_id']:raise BoardError('하네스 설치 ID/작업 폴더가 변경됐습니다.')
    db_path=Path(entry['db_path']).resolve()
    if db_path.parent not in {(data/'projects'/entry['project_id']).resolve(),(root/'.agent-work').resolve()}:raise BoardError('프로젝트 관리 DB 경로가 아닙니다.')
    if not db_path.is_file():raise BoardError('프로젝트 DB가 없습니다.')
    if not marker.resolve().is_relative_to(root):raise BoardError('프로젝트 밖 설치 설정은 변경하지 않습니다.')
    with access(db_path,exclusive=True),closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True)) as db:
        if state(db)['status']=='deleted':return {'workspace':str(root),'status':'skipped','reason':'삭제한 프로젝트입니다.'}
        try:idle(db)
        except BoardError as error:return {'workspace':str(root),'status':'skipped','reason':str(error)}
        binary=root/'.tack-orchestration/bin/tack-orchestration'
        if not binary.resolve().is_relative_to(root):raise BoardError('프로젝트 밖 실행기는 변경하지 않습니다.')
        if not binary.is_file() or 'from agent_work.harness_cli import main' not in binary.read_text():raise BoardError('사용자가 변경한 하네스 실행기를 보존합니다.')
        target=root/'.agents/skills/tack-orchestration/SKILL.md'
        stock=(source/'templates/tack-orchestration.md').read_bytes();stock_hash=hashlib.sha256(stock).hexdigest()
        previous=installed.get('skill_sha256') or LEGACY_SKILL_SHA256
        current=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        custom=(current is not None and current not in {previous,stock_hash}) or not target.resolve().is_relative_to(root)
        new_runtime=runtime(source,root/'.tack-orchestration/releases')
        backups=[];command=launcher(new_runtime,root);changed=binary.read_text()!=command
        updated={**installed,'runtime':str(new_runtime),'version':package_version(source),'data':str(db_path)}
        if not custom:updated['skill_sha256']=stock_hash
        register(data,{'workspace':str(root),'id':entry['project_id'],'db_path':str(db_path)},backups)
        changes=[] if custom else [(target,stock.decode(),False)]
        changes.extend([(marker,json.dumps(updated,indent=2)+'\n',False),(binary,command,True)])
        activate(changes,backups)
        return {'workspace':str(root),'version':updated['version'],'runtime':str(new_runtime),'status':'updated' if changed else 'current','skill':'preserved_custom' if custom else 'managed','backups':backups}

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
    init=(source/'agent_work/__init__.py').read_text()
    if not (source/'agent_work/update.py').is_file() or not re.search(r'__version__\s*=\s*[\"\x27]'+re.escape(version)+r'[\"\x27]',init):raise BoardError('이 배포본은 통합 업데이트를 지원하지 않거나 버전이 일치하지 않습니다.')
    if not (source/'templates/tack-orchestration.md').is_file():raise BoardError('프로젝트 스킬이 없는 배포본입니다.')
    data=Path(environ.get('AGENT_WORK_HOME',str(home/'.local/share/agent-work'))).expanduser().resolve()
    folder=home/'.local/lib/tack';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'.update.lock').open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise BoardError('다른 tack update가 실행 중입니다.',409)
        entries=_targets(data);new_runtime=runtime(source,folder/'releases',core=True)
        projects=[];errors=[]
        for entry in entries:
            try:projects.append(_harness(source,entry,data))
            except (BoardError,OSError,ValueError,sqlite3.Error) as error:
                skipped=isinstance(error,BoardError) and error.status==409
                item={'workspace':entry['workspace'],'status':'skipped' if skipped else 'failed','reason':str(error)};projects.append(item)
                if not skipped:errors.append(item)
        backups=[];command=launcher(new_runtime);changed=binary.read_text()!=command
        changes=[]
        legacy=home/'.local/bin/work'
        if legacy.is_file() and 'from agent_work.cli import main' in legacy.read_text():changes.append((legacy,command,True))
        changes.extend([(metadata,json.dumps({'format':1,'version':version,'runtime':str(new_runtime)},indent=2)+'\n',False),(binary,command,True)])
        activate(changes,backups)
        return {'version':version,'previous_version':old_version,'status':'updated' if changed else 'current','runtime':str(new_runtime),'backups':backups,'projects':projects,'errors':errors,
                'note':'초기 setup·권한·전역 지침·Hook·셸 설정과 DB는 변경하지 않았습니다. 실행 중인 Dashboard는 기존 버전을 사용하므로 종료 후 다시 실행하세요. 건너뛴 프로젝트는 종료 후 tack update를 다시 실행하세요.'}

def update(source=None):
    if source is not None:return apply_update(source,Path.home(),os.environ)
    try:
        with tempfile.TemporaryDirectory(prefix='tack-update-') as folder:
            downloaded,origin=_download(Path(folder))
            return {**apply_update(downloaded,Path.home(),os.environ),'source':origin}
    except (urllib.error.URLError,tarfile.TarError,KeyError,subprocess.TimeoutExpired) as error:
        if isinstance(error,urllib.error.HTTPError):error.close()
        raise BoardError('배포본 조회/다운로드에 실패했습니다. 기존 설치본은 유지됩니다: '+str(error),503)
