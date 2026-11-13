"""Task hierarchy and directed links share the task's versioned write transaction."""
from collections import Counter

KINDS = {'parent', 'blocked_by', 'duplicate_of', 'superseded_by', 'relates_to'}
LINK_TYPES = {'duplicate_of', 'superseded_by', 'relates_to'}


def validate_shape(task):
    from .store import BoardError
    links = task['relations']
    if not isinstance(task['parent_id'], str) or len(task['parent_id']) > 200:
        raise BoardError('상위 작업 ID를 확인해주세요.')
    if not isinstance(links, list) or len(links) > 50:
        raise BoardError('작업 관계는 50개 이하 목록이어야 합니다.')
    seen = set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {'type', 'task_id'}:
            raise BoardError('관계에는 type과 task_id가 필요합니다.')
        if not isinstance(link['type'], str) or link['type'] not in LINK_TYPES or not isinstance(link['task_id'], str):
            raise BoardError('작업 관계 유형과 대상 ID를 확인해주세요.')
        pair = (link['type'], link['task_id'])
        if pair in seen:
            raise BoardError('같은 작업 관계를 중복 등록할 수 없습니다.')
        seen.add(pair)


def validate(tasks, task_id):
    from .store import BoardError
    task = tasks[task_id]
    validate_shape(task)
    links = task['relations']
    for link in links:
        if link['type'] == 'relates_to' and any(other['type'] == 'relates_to' and other['task_id'] == task_id for other in tasks.get(link['task_id'],{}).get('relations',[])):
            raise BoardError('상대 작업에 이미 관련 관계가 있습니다. 해당 작업에서 관계를 확인해주세요.')
    targets = [*task['blocked_by'], *[link['task_id'] for link in links]]
    if task['parent_id']:
        targets.append(task['parent_id'])
    for target in targets:
        if target == task_id or target not in tasks:
            raise BoardError('같은 프로젝트의 다른 작업을 연결해주세요: '+target)
    parent = tasks.get(task['parent_id'])
    if parent and parent['status'] == 'done' and task['status'] not in {'done', 'archived'}:
        raise BoardError('완료된 상위 작업을 먼저 재개한 뒤 열린 하위 작업을 연결해주세요.')
    children = [t for t in tasks.values() if t['parent_id'] == task_id]
    if task['status'] == 'done' and any(t['status'] not in {'done', 'archived'} for t in children):
        raise BoardError('미완료 하위 작업이 있습니다. 하위 결과와 상위 완료 조건을 확인해주세요.')

    def cyclic(neighbors):
        pending = list(neighbors(task_id))
        visited = set()
        while pending:
            key = pending.pop()
            if key == task_id:
                return True
            if key not in visited and key in tasks:
                visited.add(key)
                pending.extend(neighbors(key))
        return False

    if cyclic(lambda key: [tasks[key]['parent_id']] if tasks[key]['parent_id'] else []):
        raise BoardError('상위·하위 작업 관계가 순환합니다.')
    child_ids = {}
    for item in tasks.values():
        child_ids.setdefault(item['parent_id'], []).append(item['id'])
    if cyclic(lambda key: [*tasks[key]['blocked_by'], *child_ids.get(key, [])]):
        raise BoardError('선행·하위 작업의 완료 대기 관계가 순환합니다.')
    if cyclic(lambda key: [link['task_id'] for link in tasks[key]['relations'] if link['type'] in {'duplicate_of', 'superseded_by'}]):
        raise BoardError('중복·통합 대상 관계가 순환합니다.')


def overview(tasks, task_id, limit=5, offset=0):
    from .store import BoardError
    if task_id not in tasks:
        raise BoardError('작업을 찾을 수 없습니다.', 404)
    items = []
    seen = set()

    def add(kind, target, outgoing):
        if target not in tasks or (kind, target) in seen:
            return
        seen.add((kind, target))
        task = tasks[target]
        items.append({'type':kind, 'outgoing':outgoing, **{k:task[k] for k in ('id','title','queue','status','version')}})

    task = tasks[task_id]
    if task['parent_id']:
        add('parent', task['parent_id'], True)
    for target in task['blocked_by']:
        add('blocked_by', target, True)
    for link in task['relations']:
        add(link['type'], link['task_id'], True)
    reverse = {'duplicate_of':'duplicated_by', 'superseded_by':'supersedes', 'relates_to':'relates_to'}
    for other in tasks.values():
        if other['parent_id'] == task_id:
            add('child', other['id'], False)
        if task_id in other['blocked_by']:
            add('blocks', other['id'], False)
        for link in other['relations']:
            if link['task_id'] == task_id:
                add(reverse[link['type']], other['id'], False)
    page = items[offset:] if limit is None else items[offset:offset+limit]
    return {'items':page, 'counts':dict(Counter(item['type'] for item in items)),
            'total':len(items), 'limit':limit, 'offset':offset, 'has_more':offset+len(page) < len(items)}
