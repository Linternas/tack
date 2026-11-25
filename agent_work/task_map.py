"""Versioned code-impact and screen-flow evidence for a project ticket."""

import json
import re

from .project_lifecycle import state
from .projects import worktree_git
from .store import BoardError, timestamp

REVISION = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
NODE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() <= set(required) | set(optional):
        raise BoardError('작업 지도의 입력 필드를 확인해주세요.')


def text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise BoardError('작업 지도의 텍스트 길이와 형식을 확인해주세요.')
    if '\0' in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise BoardError('작업 지도에 저장할 수 없는 문자가 있습니다.')


def source_ref(value):
    fields(value, ('path', 'line'))
    path, line = value['path'], value['line']
    text(path, 1000)
    if (re.search(r'[\\:?#\x00-\x1f\x7f]', path)
            or any(part in {'', '.', '..'} for part in path.split('/'))):
        raise BoardError('근거 파일은 저장소 안의 상대 경로로 입력해주세요.')
    if type(line) is not int or not 1 <= line <= 2_147_483_647:
        raise BoardError('근거 파일의 줄 번호를 확인해주세요.')


def validate(payload):
    fields(payload, ('version', 'revision', 'nodes', 'edges', 'flows'))
    if type(payload['version']) is not int or payload['version'] < 0:
        raise BoardError('작업 지도의 최신 버전이 필요합니다.')
    revision = payload['revision']
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise BoardError('작업 지도의 기준 Git 커밋을 전체 해시로 입력해주세요.')
    for name, limit in (('nodes', 100), ('edges', 300), ('flows', 100)):
        if not isinstance(payload[name], list) or len(payload[name]) > limit:
            raise BoardError(f'{name} 항목은 최대 {limit}개입니다.')
    if not payload['nodes'] and not payload['flows']:
        raise BoardError('코드 항목이나 화면 흐름을 하나 이상 입력해주세요.')

    ids = set()
    for node in payload['nodes']:
        fields(node, ('id', 'kind', 'label'), ('ref',))
        key = node['id']
        if not isinstance(key, str) or not NODE_ID.fullmatch(key) or key in ids:
            raise BoardError('코드 항목에는 중복되지 않는 ID가 필요합니다.')
        ids.add(key)
        text(node['kind'], 60)
        text(node['label'], 250)
        if 'ref' in node:
            source_ref(node['ref'])
    for edge in payload['edges']:
        fields(edge, ('from', 'to', 'label'))
        for key in ('from', 'to'):
            if not isinstance(edge[key], str) or edge[key] not in ids:
                raise BoardError('코드 관계는 지도에 있는 항목만 연결할 수 있습니다.')
        text(edge['label'], 200)
    for flow in payload['flows']:
        fields(flow, ('step', 'condition', 'outcome', 'kind'), ('ref', 'evidence', 'node_ids'))
        if flow['kind'] not in ('spec', 'code', 'browser'):
            raise BoardError('화면 흐름의 확인 방식은 spec, code, browser 중 하나여야 합니다.')
        for key, limit in (('step', 200), ('condition', 600), ('outcome', 600)):
            text(flow[key], limit, empty=key == 'condition')
        if 'node_ids' in flow:
            links = flow['node_ids']
            if (not isinstance(links, list) or len(links) > 100
                    or any(not isinstance(link, str) or link not in ids for link in links)
                    or len(set(links)) != len(links)):
                raise BoardError('화면 분기는 지도에 있는 코드 항목에 중복 없이 연결해주세요.')
        if 'ref' in flow:
            source_ref(flow['ref'])
        if 'evidence' in flow:
            text(flow['evidence'], 2000)
        elif flow['kind'] == 'browser' or 'ref' not in flow:
            raise BoardError('화면 흐름에 확인 근거가 필요합니다. 브라우저 확인에는 실제 관측 근거를 적어주세요.')


def require_task(db, task_id):
    if not db.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone():
        raise BoardError('항목을 찾을 수 없습니다.', 404)


def stale(workspace, data):
    try:
        head = worktree_git(workspace, 'rev-parse', '--verify', 'HEAD')
        if head.returncode:
            return None
        if head.stdout.decode('ascii').strip() != data['revision']:
            return True
        refs = {
            row['ref']['path']
            for row in [*data['nodes'], *data['flows']]
            if 'ref' in row
        }
        if refs:
            # Treat user-supplied references as literal filenames, not Git patterns.
            status = worktree_git(workspace, '--literal-pathspecs', 'status', '--porcelain', '-z',
                                  '--untracked-files=all', '--no-renames', '--', *sorted(refs))
            return None if status.returncode else bool(status.stdout)
        return False
    except (BoardError, OSError, UnicodeError):
        return None


def output(workspace, task_id, version, data, updated_at):
    return {'map': {**data, 'task_id': task_id, 'version': version, 'updated_at': updated_at,
                    'stale': stale(workspace, data)}}


def get_map(store, workspace, task_id):
    with store.connect() as db:
        require_task(db, task_id)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_maps'").fetchone():
            return {'map': None}
        row = db.execute('SELECT version,data,updated_at FROM task_maps WHERE task_id=? ORDER BY version DESC LIMIT 1',
                         (task_id,)).fetchone()
    return output(workspace, task_id, row['version'], json.loads(row['data']), row['updated_at']) if row else {'map': None}


def set_map(store, workspace, task_id, payload):
    validate(payload)
    data = {key: value for key, value in payload.items() if key != 'version'}
    body = json.dumps(data, ensure_ascii=False)
    if len(body.encode()) > 256 * 1024:
        raise BoardError('작업 지도는 256 KiB 이하로 저장해주세요.', 413)
    with store.connect() as db:
        if state(db)['status'] == 'archived':
            raise BoardError('보관 프로젝트는 읽기 전용입니다.', 409)
        db.execute('BEGIN IMMEDIATE')
        require_task(db, task_id)
        db.execute("""CREATE TABLE IF NOT EXISTS task_maps (
            task_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(task_id,version))""")
        version = db.execute('SELECT max(version) FROM task_maps WHERE task_id=?', (task_id,)).fetchone()[0] or 0
        if payload['version'] != version:
            raise BoardError('작업 지도가 변경됐습니다. 최신 버전을 확인해주세요.', 409)
        updated_at = timestamp()
        db.execute('INSERT INTO task_maps VALUES (?,?,?,?)', (task_id, version+1, body, updated_at))
    return output(workspace, task_id, version+1, data, updated_at)
