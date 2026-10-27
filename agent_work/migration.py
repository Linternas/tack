"""Non-destructive, verified import of a board and selected handoffs."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from .store import BoardError, Store, timestamp


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(connection,table):
    return connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()


def read_snapshot(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        return {table:rows(db,table) for table in ('tasks','history')}


def migrate(project,board,notes=(),active_notes=()):
    board=Path(board).resolve()
    destination=Path(project['db_path'])
    if board==destination.resolve(): raise BoardError('원본과 대상 DB가 같습니다.')
    expected=read_snapshot(board)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if destination.exists():
        with sqlite3.connect(destination) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'metadata' in tables and db.execute("SELECT 1 FROM metadata WHERE key='board_import' AND value=?",(str(board),)).fetchone():
                raise BoardError('이미 이관된 DB입니다. 재실행으로 현재 데이터를 덮어쓰지 않습니다.')
            if 'tasks' in tables and db.execute('SELECT count(*) FROM tasks').fetchone()[0]:
                raise BoardError('비어 있지 않은 대상 DB에는 이관할 수 없습니다.')
        # Empty destination may have been initialized; preserve it too.
    archive=destination.parent/('migration-'+uuid.uuid4().hex[:12])
    archive.mkdir(mode=0o700)
    snapshot=archive/'original-board.sqlite3'
    with sqlite3.connect(board.as_uri()+'?mode=ro',uri=True) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    if read_snapshot(snapshot)!=expected:
        raise BoardError('원본이 백업 도중 변경됐습니다. 원본 서버를 중지하고 다시 이관하세요.')
    staged=archive/'candidate.sqlite3'
    shutil.copy2(snapshot,staged)
    store=Store(staged)
    if read_snapshot(staged)!=expected: raise BoardError('작업·이력 대조가 일치하지 않습니다.')
    manifest={'format':1,'created_at':timestamp(),'source_db':str(board),'snapshot':str(snapshot),
              'tasks':len(expected['tasks']),'history':len(expected['history']),'files':[],'imported_sessions':[]}
    seen=set()
    all_notes=[]
    for item in [*notes,*active_notes]:
        item=Path(item).resolve()
        if item.is_dir(): all_notes.extend(sorted(item.rglob('*.md')))
        elif item.is_file(): all_notes.append(item)
        else: raise BoardError(f'기록 경로가 없습니다: {item}')
    for note in all_notes:
        if note in seen: continue
        seen.add(note)
        sha=digest(note)
        archived=archive/'notes'/f'{len(manifest["files"]):04d}-{note.name}'
        archived.parent.mkdir(exist_ok=True)
        shutil.copy2(note,archived)
        manifest['files'].append({'source':str(note),'archive':str(archived),'sha256':sha})
        if note not in {Path(p).resolve() for p in active_notes}: continue
        text=note.read_text()
        sections={}
        for match in re.finditer(r'^##[^\S\n]+([^\n]+)\n(.*?)(?=^##[^\S\n]|\Z)',text,re.M|re.S):
            sections[match[1].strip().lower()]=match[2].strip()
        def section(*keys): return next((sections[k] for k in keys if k in sections),'')[:28000]
        title=next((line.lstrip('# ').strip() for line in text.splitlines() if line.startswith('# ')),note.stem)
        task=store.write({'title':f'이관 재개 확인: {title}'[:200],'category':'task','status':'needs_recheck',
            'summary':'이전 세션의 재개 맥락입니다. 기존 Dashboard 항목의 현재 상태를 변경하지 않았습니다.',
            'detail':f'원본: {note}\nSHA256: {sha}',
            'next_action':'원본 기록과 현재 코드·연결 작업을 대조한 뒤 재개 여부를 결정하세요.',
            'verification':'기록 이관만 확인. 과거 테스트·배포·수동 QA를 현재 검증으로 간주하지 않음.'})['task']
        session=store.checkpoint({'task_id':task['id'],'goal':section('goal','1. 목적') or title,
            'decisions':section('decisions','4. 결정 사항','3. 결정'),'changed_files':section('changed files','changed code','5. 변경 파일'),
            'verification':section('verification','6. 검증'),'blockers':section('blockers','7. blocker / risk','7. blocker'),
            'next_action':section('next step','8. 다음 작업자 액션','8. 다음 단계') or task['next_action'],
            'workspace':project['workspace'],'branch':next((m[1] for m in re.finditer(r'^- Branch:\s*(.*)$',text,re.M)),''),
            'agent':'legacy-import','status':'blocked' if re.search(r'(?im)^-?\s*(?:status|상태):\s*blocked\b',text) else 'active'})['session']
        with store.connect() as db:
            db.execute('INSERT INTO imports VALUES (?,?,?,?)',(str(note),sha,session['id'],timestamp()))
        manifest['imported_sessions'].append({'source':str(note),'task_id':task['id'],'session_id':session['id']})
    evidence=board.parent/'evidence'
    if evidence.is_dir():
        shutil.copytree(evidence,archive/'evidence')
        for p in evidence.rglob('*'):
            if p.is_file():
                copied=archive/'evidence'/p.relative_to(evidence)
                if digest(p)!=digest(copied): raise BoardError('증거 파일 대조 실패')
                manifest['files'].append({'source':str(p),'archive':str(copied),'sha256':digest(p)})
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('board_import',?)",(str(board),))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('migration_manifest',?)",(str(archive/'manifest.json'),))
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise BoardError('SQLite 무결성 검사 실패')
    # No writes are allowed to source or destination during promotion.
    if read_snapshot(board)!=expected: raise BoardError('이관 중 원본 변경 감지. 전환하지 않았습니다.')
    with sqlite3.connect(staged) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    if destination.exists():
        with sqlite3.connect(destination) as db, sqlite3.connect(archive/'previous-destination.sqlite3') as previous:
            db.backup(previous)
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    for suffix in ('-wal','-shm'):
        side=Path(str(destination)+suffix)
        if side.exists(): side.unlink()
    os.replace(staged,destination)
    manifest['destination']=str(destination)
    manifest['verified_original_rows']=True
    (archive/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest
