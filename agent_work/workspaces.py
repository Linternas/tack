"""Owned isolated copies and hash-checked integration. No commits or remote writes."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
from .store import BoardError

SKIP={'.git','.tack-orchestration','.agent-work','__pycache__','.next','.turbo','.pytest_cache','.mypy_cache'}
DEPENDENCIES={'node_modules','.venv','venv'}


class Conflict(BoardError):
    def __init__(self,files):
        self.files=files
        super().__init__('병합 기준이 변경됐습니다: '+', '.join(files[:12])+'. 원래 결과는 보존했습니다. 최신 기준으로 재시도해주세요.',409)


def state(path):
    if path.is_symlink():return {'kind':'link','target':os.readlink(path)}
    if not path.exists():return None
    if not path.is_file():return {'kind':'directory'}
    return {'kind':'file','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':stat.S_IMODE(path.stat().st_mode)&0o777}


def scan(root,extra=()):
    result={};root=Path(root)
    for directory,dirs,files in os.walk(root,followlinks=False):
        base=Path(directory)
        for name in list(dirs):
            p=base/name;rel=p.relative_to(root).as_posix()
            if name in SKIP|DEPENDENCIES or rel in extra:dirs.remove(name);continue
            if p.is_symlink():result[rel]=state(p);dirs.remove(name)
        for name in files:
            p=base/name;rel=p.relative_to(root).as_posix()
            if rel not in extra and not name.endswith(('.pyc','-journal','-wal','-shm')):result[rel]=state(p)
    return result


class Workspaces:
    def __init__(self,root,db_path):
        self.root=Path(root).resolve();self.db_path=Path(db_path).resolve();self.base=self.root/'.tack-orchestration'
        if self.base.is_symlink():raise BoardError('하네스 작업 폴더의 심볼릭 링크를 허용하지 않습니다.')
        self.base.mkdir(exist_ok=True)
        self.extra={self.db_path.relative_to(self.root).as_posix()} if self.db_path.is_relative_to(self.root) else set()
        if self.db_path.parent==self.root:self.extra.add('backups')

    @contextmanager
    def lock(self):
        with (self.base/'integration.lock').open('a+b') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

    def folder(self,goal,job):
        if any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',v) for v in (goal,job)):raise BoardError('격리 작업 식별자를 확인해주세요.')
        folder=self.base/'workspaces'/goal/job
        if not folder.resolve().is_relative_to(self.base.resolve()):raise BoardError('작업 공간 경로를 확인해주세요.')
        return folder

    def pending(self,goal,job):
        folder=self.folder(goal,job);meta=folder/'state.json'
        if not meta.exists():return None
        saved=json.loads(meta.read_text())
        if saved.get('integrated'):return None
        if not (folder/'work').is_dir():raise BoardError('이전 격리 작업 공간이 없습니다. 최신 기준으로 재시도해주세요.')
        return self.archive({'folder':str(folder),'root':str(folder/'work'),'baseline':saved['baseline']})

    def capture(self,goal,job,staged=None):
        folder=self.folder(goal,job);folder.mkdir(parents=True,exist_ok=True);work=folder/'work'
        with self.lock():
            pending=staged or self.pending(goal,job)
            changes=self.read_artifact(pending) if pending else {}
            conflicts=[rel for rel,value in changes.items() if state(self.root/rel)!=value['before']]
            if conflicts:raise Conflict(conflicts)
            if work.exists():shutil.rmtree(work)
            def ignore(directory,names):
                base=Path(directory)
                return [name for name in names if name in SKIP|DEPENDENCIES or (base/name).relative_to(self.root).as_posix() in self.extra or name.endswith(('.pyc','-journal','-wal','-shm'))]
            shutil.copytree(self.root,work,symlinks=True,ignore=ignore)
            # Share only dependency directories; Codex writes are restricted to the isolated work directory.
            for directory,dirs,_ in os.walk(self.root,followlinks=False):
                base=Path(directory)
                for name in list(dirs):
                    if name in SKIP or (base/name).relative_to(self.root).as_posix() in self.extra:dirs.remove(name)
                    elif name in DEPENDENCIES:
                        target=work/(base/name).relative_to(self.root);target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(base/name,target_is_directory=True);dirs.remove(name)
                    elif (base/name).is_symlink():dirs.remove(name)
            baseline=scan(work,self.extra)
            for rel,value in changes.items():
                target=work/rel
                if value['after'] is None:
                    if target.exists() or target.is_symlink():target.unlink()
                else:
                    source=Path(pending['artifact'])/'after'/rel
                    if source.is_symlink() or not source.is_file():raise BoardError('보존된 업무 산출물을 확인해주세요.')
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            metadata={'baseline':baseline,'integrated':False}
            (folder/'state.json').write_text(json.dumps(metadata))
        return {'folder':str(folder),'root':str(work),'baseline':baseline}

    def archive(self,attempt):
        root=Path(attempt['root']);folder=Path(attempt['folder'])
        current=scan(root,self.extra);baseline=attempt['baseline'];changes={rel:{'before':baseline.get(rel),'after':current.get(rel)} for rel in set(baseline)|set(current) if baseline.get(rel)!=current.get(rel)}
        # Metadata and repositories created by an employee are never accepted as ordinary code changes.
        for forbidden in ('.git','.tack-orchestration','.agent-work'):
            if (root/forbidden).exists():changes[forbidden]={'before':None,'after':{'kind':'directory'}}
        artifact=folder/'artifacts'/uuid.uuid4().hex;artifact.mkdir(parents=True)
        for rel,value in changes.items():
            source=root/rel
            if value['after'] and value['after']['kind']=='file':
                if not source.resolve().is_relative_to(root.resolve()):raise BoardError('작업 공간 밖 산출물은 통합할 수 없습니다.')
                target=artifact/'after'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        (artifact/'manifest.json').write_text(json.dumps(changes))
        return {'artifact':str(artifact),'files':sorted(changes)}

    def read_artifact(self,record):
        artifact=Path(record['artifact'])
        if not artifact.resolve().is_relative_to((self.base/'workspaces').resolve()):raise BoardError('산출물 경로를 확인해주세요.')
        return json.loads((artifact/'manifest.json').read_text())

    def integrate(self,attempt,record,approved,write):
        changes=self.read_artifact(record)
        outside=set(changes)-set(approved)
        if outside or not write and changes:
            raise BoardError('승인 파일 밖 수정은 통합하지 않았습니다: '+', '.join(sorted(outside or changes)[:12]),409)
        if any(value['after'] and value['after']['kind']!='file' for value in changes.values()):raise BoardError('심볼릭 링크·디렉터리 산출물은 통합할 수 없습니다.')
        with self.lock():
            conflicts=[rel for rel,value in changes.items() if state(self.root/rel)!=value['before']]
            conflicts.extend(rel for rel in approved if state(self.root/rel)!=attempt['baseline'].get(rel))
            if conflicts:raise Conflict(sorted(set(conflicts)))
            backups={}
            try:
                for rel,value in changes.items():
                    target=self.root/rel
                    if not target.resolve().is_relative_to(self.root):raise BoardError('프로젝트 밖 통합을 차단했습니다.')
                    backups[rel]=(target.read_bytes(),stat.S_IMODE(target.stat().st_mode)) if target.is_file() else None
                    if value['after'] is None:target.unlink()
                    else:
                        source=Path(record['artifact'])/'after'/rel
                        target.parent.mkdir(parents=True,exist_ok=True)
                        fd,temp=tempfile.mkstemp(prefix='.tack-integrate-',dir=target.parent)
                        try:
                            with os.fdopen(fd,'wb') as output:output.write(source.read_bytes())
                            os.chmod(temp,value['after']['mode']);os.replace(temp,target)
                        finally:Path(temp).unlink(missing_ok=True)
                before=Path(record['artifact'])/'before'
                for rel,value in backups.items():
                    if value:
                        saved=before/rel;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(value[0])
            except BaseException:
                for rel,value in backups.items():
                    target=self.root/rel
                    if value is None:target.unlink(missing_ok=True)
                    else:target.write_bytes(value[0]);target.chmod(value[1])
                raise
        self.discard(attempt['folder'])

    @staticmethod
    def discard(folder):
        path=Path(folder)/'state.json'
        if path.exists():
            metadata=json.loads(path.read_text());metadata['integrated']=True;path.write_text(json.dumps(metadata))
