#!/usr/bin/env python3
"""Shared transactional task and session storage."""
import argparse
from contextlib import closing, contextmanager, nullcontext
import json
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
CATEGORIES = {"issue", "decision", "api", "qa", "task"}
STATUSES = {"todo", "in_progress", "waiting_decision", "waiting_api", "needs_recheck", "blocked", "done", "archived"}
PRIORITIES = {"P1", "P2", "P3"}
QA_RESULTS = {"not_run", "passed", "failed", "blocked", "partial", "not_applicable"}
TEXT_FIELDS = {"title", "summary", "detail", "next_action", "acceptance", "owner", "verification", "last_checked", "resolution"}
LIST_FIELDS = {"areas", "blocked_by", "sources"}
QA_FIELDS = {"preconditions", "steps", "expected", "actual", "evidence"}
TASK_FIELDS = TEXT_FIELDS | LIST_FIELDS | {"category", "status", "priority", "qa_result", "qa"}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class BoardError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate(task):
    if not isinstance(task.get("category"),str) or not isinstance(task.get("status"),str) or task["category"] not in CATEGORIES or task["status"] not in STATUSES:
        raise BoardError("분류 또는 상태를 확인해주세요.")
    if not isinstance(task.get("priority"),str) or not isinstance(task.get("qa_result"),str) or task["priority"] not in PRIORITIES or task["qa_result"] not in QA_RESULTS:
        raise BoardError("우선순위 또는 QA 판정을 확인해주세요.")
    for key in TEXT_FIELDS:
        if not isinstance(task.get(key), str) or len(task[key]) > 30000:
            raise BoardError(f"{key}: 텍스트 길이 또는 형식을 확인해주세요.")
    if not task["title"].strip() or len(task["title"]) > 200:
        raise BoardError("제목은 1~200자로 입력해주세요.")
    for key in ("areas", "blocked_by"):
        if not isinstance(task.get(key), list) or len(task[key]) > 50 or any(not isinstance(item, str) or len(item) > 200 for item in task[key]):
            raise BoardError(f"{key}: 목록 형식을 확인해주세요.")
    if not isinstance(task.get("sources"), list) or len(task["sources"]) > 30:
        raise BoardError("근거 목록 형식을 확인해주세요.")
    for source in task["sources"]:
        if not isinstance(source, dict) or set(source) - {"label", "path", "url"} or not source.get("label") or any(not isinstance(value, str) or len(value) > 3000 for value in source.values()):
            raise BoardError("근거는 이름과 문서 경로 또는 URL을 입력해주세요.")
        if source.get("url") and urlsplit(source["url"]).scheme not in {"http", "https"}:
            raise BoardError("근거 URL은 http 또는 https만 사용할 수 있습니다.")
    if not isinstance(task.get("qa"), dict) or set(task["qa"]) != QA_FIELDS or any(not isinstance(value, str) or len(value) > 30000 for value in task["qa"].values()):
        raise BoardError("QA 기록 형식을 확인해주세요.")
    if task["last_checked"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", task["last_checked"]):
        raise BoardError("확인일은 YYYY-MM-DD 형식으로 입력해주세요.")
    if task["status"] == "done":
        if not task["resolution"].strip():
            raise BoardError("완료로 바꾸려면 처리·결정 내용을 기록해주세요.")
        if task["category"] == "qa" and (task["qa_result"] not in {"passed", "not_applicable"} or not task["qa"]["actual"].strip()):
            raise BoardError("QA 완료에는 통과 또는 해당 없음 판정과 실제 결과가 필요합니다.")


def defaults():
    return {**{key: "" for key in TEXT_FIELDS}, "category": "issue", "status": "todo", "priority": "P2", "qa_result": "not_run", "areas": [], "blocked_by": [], "sources": [], "qa": {key: "" for key in QA_FIELDS}}


class Store:
    def __init__(self, path, seed_path=None, readonly=False):
        self.path = Path(path).resolve()
        self.readonly = readonly
        from .project_lifecycle import read
        lifecycle=read(self.path)
        if lifecycle['status']=='deleted':raise BoardError('삭제한 프로젝트의 기록에는 접근할 수 없습니다.',410)
        if readonly or lifecycle['status']=='archived':
            if not self.path.is_file():
                raise BoardError("작업 DB가 없습니다. tack init으로 초기화하세요.",404)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL, before_data TEXT, after_data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS history_task ON history(task_id, id);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, version INTEGER NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
                    kind TEXT NOT NULL, digest TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(source,digest));
                CREATE INDEX IF NOT EXISTS documents_source ON documents(source,created_at);
                CREATE TABLE IF NOT EXISTS imports (
                    source TEXT PRIMARY KEY, digest TEXT NOT NULL, session_id TEXT, imported_at TEXT NOT NULL);

            """)
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM metadata WHERE key='seed_loaded'").fetchone():
                for source in (json.loads(Path(seed_path).read_text(encoding="utf-8")) if seed_path else []):
                    task = {**defaults(), **source}
                    task["qa"] = {**defaults()["qa"], **source.get("qa", {})}
                    validate(task)
                    now = timestamp()
                    data = json.dumps({key: task[key] for key in TASK_FIELDS}, ensure_ascii=False)
                    db.execute("INSERT INTO tasks VALUES (?,1,?,?,?)", (task["id"], data, now, now))
                    db.execute("INSERT INTO history(task_id,action,note,after_data,created_at) VALUES (?,?,?,?,?)", (task["id"], "created", "기존 기록에서 초기 등록", data, now))
                db.execute("INSERT INTO metadata VALUES ('seed_loaded', ?)", (timestamp(),))

    @contextmanager
    def connect(self):
        from .project_lifecycle import access, state
        with access(self.path,create=not self.readonly), closing(sqlite3.connect(self.path.as_uri()+"?mode=ro" if self.readonly else self.path, timeout=10, uri=True)) as db:
            db.row_factory = sqlite3.Row
            lifecycle=state(db)
            if lifecycle['status']=='deleted':raise BoardError('삭제한 프로젝트의 기록에는 접근할 수 없습니다.',410)
            if lifecycle['status']=='archived':db.execute('PRAGMA query_only=ON')
            elif not self.readonly:db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db

    @staticmethod
    def decode(row):
        return {**json.loads(row["data"]), **{key: row[key] for key in ("id", "version", "created_at", "updated_at")}}

    def all(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM tasks ORDER BY id")]

    def detail(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise BoardError("항목을 찾을 수 없습니다.", 404)
            history = [dict(item) for item in db.execute("SELECT id,action,note,created_at FROM history WHERE task_id=? ORDER BY id DESC", (task_id,))]
            return {"task": self.decode(row), "history": history}

    def write(self, payload, task_id=None, *, connection=None):
        if not isinstance(payload, dict) or set(payload) - TASK_FIELDS - {"version", "note"}:
            raise BoardError("저장 요청의 필드를 확인해주세요.")
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 10000:
            raise BoardError("변경 메모를 확인해주세요.")
        now = timestamp()
        with (nullcontext(connection) if connection is not None else self.connect()) as db:
            if connection is None:
                db.execute("BEGIN IMMEDIATE")
            previous = None
            if task_id:
                row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not row:
                    raise BoardError("항목을 찾을 수 없습니다.", 404)
                previous = self.decode(row)
                if type(payload.get("version")) is not int or payload["version"] != row["version"]:
                    raise BoardError("다른 창이나 작업에서 이미 변경됐습니다. 입력을 보관한 뒤 최신 내용을 다시 열어주세요.", 409)
                task = {**previous, **{key: value for key, value in payload.items() if key in TASK_FIELDS}}
                version = row["version"] + 1
            else:
                task = {**defaults(), **{key: value for key, value in payload.items() if key in TASK_FIELDS}}
                task["qa"] = {**defaults()["qa"], **task["qa"]} if isinstance(task["qa"], dict) else task["qa"]
                number = db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id,7) AS INTEGER)),0)+1 FROM tasks WHERE id GLOB 'LOCAL-[0-9]*'").fetchone()[0]
                task_id, version = f"LOCAL-{number:04d}", 1
            validate(task)
            for dependency in task["blocked_by"]:
                if dependency == task_id or not db.execute("SELECT 1 FROM tasks WHERE id=?", (dependency,)).fetchone():
                    raise BoardError(f"선행 항목을 확인해주세요: {dependency}")
            data = json.dumps({key: task[key] for key in TASK_FIELDS}, ensure_ascii=False)
            if previous:
                db.execute("UPDATE tasks SET version=?,data=?,updated_at=? WHERE id=?", (version, data, now, task_id))
            else:
                db.execute("INSERT INTO tasks VALUES (?,?,?,?,?)", (task_id, version, data, now, now))
            db.execute("INSERT INTO history(task_id,action,note,before_data,after_data,created_at) VALUES (?,?,?,?,?,?)", (task_id, "updated" if previous else "created", note, json.dumps(previous, ensure_ascii=False) if previous else None, data, now))
        if connection is not None:
            return {"task": {**task, "id":task_id, "version":version, "created_at":previous["created_at"] if previous else now, "updated_at":now}}
        return self.detail(task_id)

    def backup(self):
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        destination = folder / f"dashboard-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
        with self.connect() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        return destination


    def sessions(self, include_completed=False):
        with self.connect() as db:
            rows = [self.decode(row) for row in db.execute('SELECT * FROM sessions ORDER BY updated_at DESC')]
        return rows if include_completed else [row for row in rows if row['status'] != 'completed']

    def session(self, session_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            if not row:
                raise BoardError('세션을 찾을 수 없습니다.', 404)
            return {'session': self.decode(row), 'checkpoints': [dict(r) for r in db.execute(
                'SELECT id,data,created_at FROM checkpoints WHERE session_id=? ORDER BY id DESC', (session_id,))]}

    def checkpoint(self, payload, session_id=None):
        import uuid
        fields = {'goal', 'decisions', 'changed_files', 'verification', 'blockers', 'next_action',
                  'workspace', 'branch', 'agent', 'thread_id', 'status'}
        if not isinstance(payload, dict) or set(payload) - fields - {'version', 'task_id'}:
            raise BoardError('체크포인트 필드를 확인해주세요.')
        if any(not isinstance(v, str) or len(v) > 30000 for k,v in payload.items() if k in fields):
            raise BoardError('체크포인트는 30000자 이하 문자열이어야 합니다.')
        now = timestamp()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone() if session_id else None
            if session_id and not previous:
                raise BoardError('세션을 찾을 수 없습니다.', 404)
            if previous and (type(payload.get('version')) is not int or previous['version'] != payload['version']):
                raise BoardError('세션이 변경됐습니다. 입력을 보관하고 최신 버전을 확인해주세요.', 409)
            task_id = previous['task_id'] if previous else payload.get('task_id')
            if previous and 'task_id' in payload and payload['task_id'] != task_id:
                raise BoardError('기존 세션의 작업을 변경할 수 없습니다.')
            if not isinstance(task_id, str) or not db.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone():
                raise BoardError('연결할 작업을 확인해주세요.')
            data = json.loads(previous['data']) if previous else {**{key:'' for key in fields}, 'status':'active'}
            data.update({k:v for k,v in payload.items() if k in fields})
            data['task_id'] = task_id
            if data['status'] not in {'active','blocked','completed'} or not data['goal'].strip():
                raise BoardError('세션 상태와 목표를 확인해주세요.')
            if data['status'] != 'completed' and not data['next_action'].strip():
                raise BoardError('진행 중인 세션에는 다음 행동이 필요합니다.')
            if data['status'] == 'completed':
                data['next_action'] = ''
            encoded = json.dumps(data, ensure_ascii=False)
            if previous:
                db.execute('UPDATE sessions SET data=?,version=version+1,updated_at=? WHERE id=?', (encoded,now,session_id))
            else:
                session_id = 'RUN-' + uuid.uuid4().hex[:16].upper()
                db.execute('INSERT INTO sessions VALUES (?,?,1,?,?,?)', (session_id,task_id,encoded,now,now))
            db.execute('INSERT INTO checkpoints(session_id,data,created_at) VALUES (?,?,?)', (session_id,encoded,now))
        return self.session(session_id)

    def resume(self, task_id, session_id=None):
        task = self.detail(task_id)['task']
        sessions = [s for s in self.sessions() if s['task_id'] == task_id]
        if session_id:
            selected = self.session(session_id)['session']
            if selected['task_id'] != task_id:
                raise BoardError('작업에 연결되지 않은 세션입니다.')
            sessions = [selected]
        dependencies = [self.detail(key)['task'] for key in task['blocked_by']]
        return {'task':task, 'sessions':sessions, 'dependencies':dependencies,
                'warning':'기록은 재개 근거이며 현재 Git 상태와 실제 서비스 상태는 다시 확인하세요.'}

    def archive_document(self, source, kind='work', original_source=None):
        import hashlib
        if kind not in {'work','session','legacy_global'}:
            raise BoardError('문서 종류를 확인해주세요.')
        source=Path(source).resolve()
        raw=source.read_bytes()
        if len(raw)>2*1024*1024:
            raise BoardError('보관 문서는 2MB 이하만 지원합니다.')
        content=raw.decode('utf-8')
        digest=hashlib.sha256(raw).hexdigest()
        doc_id='DOC-'+hashlib.sha256((str(source)+'\0'+digest).encode()).hexdigest()[:24].upper()
        source=Path(original_source).resolve() if original_source else source
        title=next((line.lstrip('# ').strip() for line in content.splitlines() if line.startswith('# ')),source.name)[:300]
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?)',
                (doc_id,str(source),title,kind,digest,content,timestamp()))
        return self.document(doc_id)

    def document(self, key):
        with self.connect() as db:
            row=db.execute('SELECT * FROM documents WHERE id=? OR source=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(key,key)).fetchone()
            if not row:
                raise BoardError('보관 문서를 찾을 수 없습니다.',404)
            return dict(row)

    def documents(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,source,title,kind,digest,created_at FROM documents ORDER BY source,created_at')]
