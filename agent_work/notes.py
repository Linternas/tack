"""Project notes stored independently of the task workflow."""
from uuid import uuid4

from .store import BoardError, timestamp


def has_notes_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes'").fetchone() is not None


def list_notes(store, query='', offset=0):
    with store.connect() as db:
        if not has_notes_table(db):
            return {'notes': [], 'has_more': False}
        rows = db.execute(
            """SELECT id,title,updated_at FROM notes
               WHERE instr(lower(title),lower(?)) OR instr(lower(body),lower(?))
               ORDER BY updated_at DESC,id LIMIT 51 OFFSET ?""",
            (query, query, offset),
        ).fetchall()
        return {'notes': [dict(row) for row in rows[:50]], 'has_more': len(rows) > 50}


def export_notes(store):
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM notes ORDER BY id')] if has_notes_table(db) else []


def read_note(db, note_id):
    row = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
    if row is None:
        raise BoardError('노트를 찾을 수 없습니다.', 404)
    return dict(row)


def get_note(store, note_id):
    with store.connect() as db:
        if not has_notes_table(db):
            raise BoardError('노트를 찾을 수 없습니다.', 404)
        return read_note(db, note_id)


def save_note(store, payload, note_id=None):
    expected = {'title', 'body', 'version'} if note_id else {'title', 'body'}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise BoardError('노트 입력을 확인해주세요.')
    title, body = payload['title'], payload['body']
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise BoardError('제목은 1~200자로 입력해주세요.')
    if not isinstance(body, str) or not body.strip() or len(body) > 30000:
        raise BoardError('내용은 1~30,000자로 입력해주세요.')
    with store.connect() as db:
        from .project_lifecycle import state
        db.execute('BEGIN IMMEDIATE')
        if state(db)['status'] == 'archived':
            raise BoardError('보관 프로젝트는 읽기 전용입니다.', 409)
        db.execute("""CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
            version INTEGER NOT NULL, updated_at TEXT NOT NULL)""")
        if note_id:
            version = payload['version']
            if type(version) is not int:
                raise BoardError('노트 버전을 확인해주세요.')
            if read_note(db, note_id)['version'] != version:
                raise BoardError('노트가 변경되었습니다. 현재 내용을 복사한 뒤 다시 열어주세요.', 409)
            db.execute('UPDATE notes SET title=?,body=?,version=version+1,updated_at=? WHERE id=?',
                       (title.strip(), body, timestamp(), note_id))
        else:
            note_id = 'NOTE-' + uuid4().hex.upper()
            db.execute('INSERT INTO notes VALUES (?,?,?,1,?)', (note_id, title.strip(), body, timestamp()))
        return read_note(db, note_id)
