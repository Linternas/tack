"""One public work queue, with compatibility for existing execution metadata."""
QUEUES = {'todo', 'in_progress', 'issue', 'qa', 'waiting', 'done', 'archived'}
LABELS = {'todo':'할 일', 'in_progress':'진행 중', 'issue':'문제', 'qa':'QA',
          'waiting':'대기', 'done':'완료', 'archived':'보관'}


def queue_of(task):
    status = task.get('status', 'todo')
    category = task.get('category', 'task')
    if status in {'done', 'archived'}:
        return status
    if category == 'qa':
        return 'qa'
    if category == 'issue' or status == 'needs_recheck':
        return 'issue'
    if status == 'in_progress':
        return 'in_progress'
    if status in {'blocked', 'waiting_api', 'waiting_decision'} or category in {'api', 'decision'}:
        return 'waiting'
    return 'todo'


def normalize(task, payload, previous=None):
    from .store import BoardError
    if 'is_api_request' in payload:
        task['is_api_request'] = payload['is_api_request']
    elif previous:
        task['is_api_request'] = previous['is_api_request']
        if 'category' in payload and payload['category'] != previous['category']:
            task['is_api_request'] = payload['category'] == 'api'
    else:
        task['is_api_request'] = task.get('category') == 'api'
    requested = payload.get('queue')
    legacy_change = 'category' in payload or 'status' in payload
    explicit = 'queue' in payload and (not previous or requested != previous['queue'] or not legacy_change)
    if explicit and (not isinstance(requested,str) or requested not in QUEUES):
        raise BoardError('업무 목록은 todo, in_progress, issue, qa, waiting, done, archived 중 하나여야 합니다.')
    if explicit and queue_of(task) != requested:
        if requested in {'done', 'archived'}:
            task['status'] = requested
            if previous is None and 'category' not in payload:
                task['category'] = 'task'
        elif requested == 'qa':
            task['status'] = task['status'] if task['category'] == 'qa' and task['status'] not in {'done','archived'} else 'todo'
            task['category'] = 'qa'
        elif requested == 'issue':
            task['category'], task['status'] = 'issue', 'needs_recheck'
        else:
            task['category'] = 'task'
            task['status'] = 'blocked' if requested == 'waiting' else requested
    task['queue'] = queue_of(task)
    return task
