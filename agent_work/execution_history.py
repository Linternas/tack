"""Read the historical goal and execution tables without opening a worker."""

import json

from .store import BoardError


def history_query(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    sources = []
    if 'goals' in tables:
        sources.append("SELECT 'goal' AS kind, id, task_id, NULL AS goal_id, data, created_at, updated_at, "
                       "NULL AS workspace, NULL AS role FROM goals")
    if 'executions' in tables:
        workspace, join = 'NULL', ''
        if 'goals' in tables:
            workspace = "json_extract(CASE WHEN json_valid(g.data) THEN g.data ELSE '{}' END, '$.workspace')"
            join = ' LEFT JOIN goals g ON g.id=e.goal_id'
        sources.append("SELECT 'role' AS kind, e.id, e.task_id, e.goal_id, e.data, e.created_at, e.updated_at, "
                       f"{workspace} AS workspace, NULL AS role FROM executions e{join}")
    if 'office_executions' in tables:
        workspace, role, join = 'NULL', 'NULL', ''
        if 'office_sessions' in tables:
            workspace, role = 's.workspace', 's.role_id'
            join = (' LEFT JOIN office_sessions s ON s.rowid=(SELECT rowid FROM office_sessions '
                    'WHERE id=e.id ORDER BY updated_at DESC, workspace LIMIT 1)')
        sources.append("SELECT 'session' AS kind, e.id, NULL AS task_id, NULL AS goal_id, e.data, "
                       f"NULL AS created_at, e.updated_at, {workspace} AS workspace, {role} AS role "
                       f"FROM office_executions e{join}")
    return ' UNION ALL '.join(sources)


def saved_data(row):
    try:
        data = json.loads(row['data'])
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def summary(row, data):
    kind = row['kind']
    role = row['role'] or data.get('role')
    label = {'goal': data.get('title') or '목표', 'role': role or '역할 실행', 'session': 'Codex 세션'}[kind]
    item = {'id': row['id'], 'kind': kind, 'label': label, 'status': data.get('status') or 'unknown',
            'workspace': row['workspace'] or data.get('workspace', ''), 'updated_at': row['updated_at']}
    item.update({key: row[key] for key in ('task_id', 'goal_id') if row[key]})
    if role:
        item['role'] = role
    return item


def history(store, limit=20, offset=0):
    if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
        raise BoardError('실행 이력의 조회 범위를 확인해주세요.')
    with store.connect() as db:
        query = history_query(db)
        if not query:
            return {'items': [], 'total': 0, 'has_more': False}
        db.execute('BEGIN')
        total = db.execute(f'SELECT COUNT(*) FROM ({query})').fetchone()[0]
        rows = db.execute(f'SELECT * FROM ({query}) ORDER BY updated_at DESC, kind, id LIMIT ? OFFSET ?',
                          (limit, offset)).fetchall()
    return {'items': [summary(row, saved_data(row)) for row in rows], 'total': total,
            'has_more': offset + len(rows) < total}


def detail(store, execution_id):
    with store.connect() as db:
        query = history_query(db)
        row = db.execute(f'SELECT * FROM ({query}) WHERE id=?', (execution_id,)).fetchone() if query else None
    if row is None:
        raise BoardError('실행 이력을 찾을 수 없습니다.', 404)
    data = saved_data(row)
    details = {key: data[key] for key in ('acceptance', 'error', 'model', 'adapter', 'thread_id', 'tokens', 'exit_code')
               if type(data.get(key)) in (str, int) and data[key] != ''}
    result = data.get('review_summary') or data.get('summary')
    if isinstance(result, str) and result:
        details['summary'] = result
    checks = data.get('verification_results')
    if isinstance(checks, list) and checks:
        details['verification_results'] = [{key: check[key] for key in ('status', 'exit_code')
                                            if type(check.get(key)) in (str, int)}
                                           for check in checks if isinstance(check, dict)]
    if row['created_at']:
        details['created_at'] = row['created_at']
    return {'item': summary(row, data), 'details': details}
