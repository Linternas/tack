"""Evidence-backed demo days. Presentations reuse records and never call a model."""
import hashlib, json, re
from .store import BoardError, timestamp
from .orchestration import identifier, text
from .organization import Organization

def evidence(goal, jobs):
    value = {
        'goal': {
            k: goal.get(k)
            for k in (
                'title', 'acceptance', 'verification', 'verification_results',
                'team_id', 'team_snapshot', 'execution_policy',
            )
        },
        'jobs': [
            {
                k: j.get(k)
                for k in ('id', 'employee', 'status', 'result', 'files', 'checks', 'last_verified_progress')
            }
            for j in jobs
        ],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

class DemoDay:

    def __init__(self, work):
        self.work, self.store = (work, work.store)

    def exists(self):
        with self.store.connect() as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='demo_days'").fetchone())

    def ensure(self):
        if self.exists():
            return
        self.store.backup()
        with self.store.connect() as db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS demo_days '
                '(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,version INTEGER NOT NULL,'
                'data TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)'
            )

    @staticmethod
    def decode(row):
        return {**json.loads(row['data']), **{k: row[k] for k in ('id', 'version', 'created_at', 'updated_at')}}

    def list(self):
        if not self.exists():
            return []
        with self.store.connect() as db:
            return [
                self.decode(r)
                for r in db.execute(
                    'SELECT * FROM demo_days WHERE workspace=? ORDER BY created_at DESC',
                    (self.work.workspace,),
                )
            ]

    def show(self, key):
        result = next((d for d in self.list() if d['id'] == key), None)
        if not result:
            raise BoardError('데모데이를 찾을 수 없습니다.', 404)
        return result

    def ready(self, goal, jobs):
        return (
            not goal.get('worker')
            and goal['status'] in {'awaiting_acceptance', 'done'}
            and bool(jobs)
            and all(j['status'] == 'done' for j in jobs)
            and bool(goal['verification_results'])
            and all(c['status'] == 'succeeded' for c in goal['verification_results'])
        )

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'title', 'team_id'}:
            raise BoardError('발표 제목과 팀을 확인해주세요.')
        title = text(payload['title'], '발표 제목', 120)
        team_id = payload['team_id']
        org = Organization(self.store, self.work.workspace)
        if team_id is not None:
            org.group(team_id)
        known = {(e['goal_id'], e['evidence']) for d in self.list() for e in d['entries']}
        entries = []
        roles = self.work.team.settings()['roles']
        for goal in self.work.orch.overview()['goals']:
            if (
                goal.get('workspace') != self.work.workspace
                or goal.get('mode') != 'team'
                or team_id is not None and (goal.get('team_id') or 'default') != team_id
            ):
                continue
            jobs = self.work.jobs(goal['id'])
            if not self.ready(goal, jobs):
                continue
            stamp = evidence(goal, jobs)
            if (goal['id'], stamp) in known:
                continue
            group = org.group(goal.get('team_id') or 'default')
            reports = [
                {
                    'job_id': j['id'],
                    'employee': j['employee'],
                    'name': roles.get(j['employee'], {}).get('name', j['employee']),
                    'title': j['title'],
                    'body': j['result'],
                    'files': j['files'],
                    'checks': j.get('checks', []),
                }
                for j in jobs
            ]
            links = []
            for report in reports:
                for url in re.findall('https?://[^\\s<>"\\x27`]+', report['body']):
                    url = url.rstrip('.,;:!?)]}')
                    if url not in links:
                        links.append(url)
            entries.append({
                'goal_id': goal['id'],
                'team_id': group['id'],
                'team_name': group['name'],
                'title': goal['title'],
                'acceptance': goal['acceptance'],
                'goal_version': goal['version'],
                'evidence': stamp,
                'reports': reports,
                'verification': goal['verification_results'],
                'links': links[:10],
                'tokens': goal.get('token_used', 0),
                'decision': 'accepted' if goal['status'] == 'done' else 'pending',
                'feedback': '',
                'followup_goal': None,
            })
        if not entries:
            raise BoardError('새롭게 검증을 마친 발표 결과가 없습니다.', 409)
        self.ensure()
        key = identifier('DEMO-')
        now = timestamp()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = {
                (e['goal_id'], e['evidence'])
                for row in db.execute('SELECT data FROM demo_days WHERE workspace=?', (self.work.workspace,))
                for e in json.loads(row[0])['entries']
            }
            if any(((e['goal_id'], e['evidence']) in previous for e in entries)):
                raise BoardError('같은 결과가 이미 발표에 등록됐습니다. 새로고침해주세요.', 409)
            for entry in entries:
                goal = self.work.goal(entry['goal_id'])
                if (
                    goal['version'] != entry['goal_version']
                    or not self.ready(goal, self.work.jobs(goal['id']))
                    or evidence(goal, self.work.jobs(goal['id'])) != entry['evidence']
                ):
                    raise BoardError('발표 준비 중 결과가 바뀌었습니다.', 409)
            db.execute(
                'INSERT INTO demo_days VALUES (?,?,1,?,?,?)',
                (
                    key, self.work.workspace,
                    json.dumps({'title': title, 'entries': entries}, ensure_ascii=False), now, now,
                ),
            )
        return self.show(key)

    def respond(self, key, payload):
        if (
            not isinstance(payload, dict)
            or set(payload) != {'version', 'goal_id', 'decision', 'feedback'}
            or not isinstance(payload['decision'], str)
            or payload['decision'] not in {'accept', 'revise', 'hold'}
        ):
            raise BoardError('수락·수정 요청·보류 중에서 선택해주세요.')
        if not isinstance(payload['feedback'], str) or len(payload['feedback']) > 10000:
            raise BoardError('피드백 내용을 확인해주세요.')
        if not self.exists():
            raise BoardError('데모데이를 찾을 수 없습니다.', 404)
        self.work.migrate()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                'SELECT * FROM demo_days WHERE id=? AND workspace=?',
                (key, self.work.workspace),
            ).fetchone()
            if not row:
                raise BoardError('데모데이를 찾을 수 없습니다.', 404)
            if type(payload['version']) is not int or row['version'] != payload['version']:
                raise BoardError('발표 상태가 변경됐습니다. 최신 결과를 확인해주세요.', 409)
            data = json.loads(row['data'])
            entry = next((e for e in data['entries'] if e['goal_id'] == payload['goal_id']), None)
            if not entry or entry['decision'] in {'accepted', 'changes_requested'}:
                raise BoardError('이미 처리했거나 없는 발표입니다.', 409)
            goal = self.work.goal(entry['goal_id'])
            jobs = self.work.jobs(goal['id'])
            if (
                goal.get('worker')
                or evidence(goal, jobs) != entry['evidence']
                or not self.ready(goal, jobs)
            ):
                raise BoardError('발표 이후 작업 결과가 바뀌었습니다. 다시 검증한 결과를 발표해주세요.', 409)
            feedback = payload['feedback']
            decision = payload['decision']
            if decision == 'accept':
                self.work.lock_goal(db, goal['id'], goal['version'])
                task = self.store.detail(goal['task_id'])['task']
                self.store.write(
                    {
                        'version': task['version'],
                        'status': 'done',
                        'resolution': '사용자가 데모데이의 산출물과 검증을 확인하고 수락',
                        'verification': json.dumps(goal['verification_results'], ensure_ascii=False),
                    },
                    goal['task_id'],
                    connection=db,
                )
                self.work.set_goal(db, goal['id'], status='done')
                entry['decision'] = 'accepted'
            elif decision == 'revise':
                text(feedback, '수정 요청', 10000)
                roles = self.work.scoped_settings(goal)['roles']
                planner = next((k for k, r in roles.items() if r['kind'] == 'plan'), next(iter(roles), None))
                if planner is None:
                    raise BoardError('기존 승인 범위에 남아 있는 직원을 먼저 배치해주세요.', 409)
                new = self.work.create(
                    '수정: ' + goal['title'][:195],
                    acceptance=goal['acceptance'],
                    employee=planner,
                    team_id=goal.get('team_id'),
                    connection=db,
                )
                self.work.set_goal(
                    db,
                    new['id'],
                    execution_policy=goal.get('execution_policy'),
                    team_snapshot=goal.get('team_snapshot'),
                    verification=goal['verification'],
                    supplied_verification=goal.get('supplied_verification', goal['verification']),
                    source_goal=goal['id'],
                )
                first = db.execute(
                    'SELECT * FROM team_jobs WHERE goal_id=? ORDER BY created_at',
                    (new['id'],),
                ).fetchone()
                self.work.change(db, first['id'], {
                    'instructions': (
                        self.work.decode(first)['instructions']
                        + '\n사용자의 데모데이 수정 요청: ' + feedback
                        + '\n기존 목표: ' + goal['id']
                        + '\n기존 검증 결과를 참고하고 새 요구사항 범위를 임의로 늘리지 마세요.'
                    ),
                })
                entry.update(decision='changes_requested', followup_goal=new['id'])
            else:
                entry['decision'] = 'held'
            entry['feedback'] = feedback
            self.work.team.insert(
                db,
                goal['id'],
                'user',
                feedback or {'accept': '발표 결과 수락', 'hold': '발표 결과 보류'}[decision],
                'decision',
                data={'demo_id': key, 'decision': decision, 'followup_goal': entry['followup_goal']},
            )
            db.execute(
                'UPDATE demo_days SET version=version+1,data=?,updated_at=? WHERE id=?',
                (json.dumps(data, ensure_ascii=False), timestamp(), key),
            )
        return self.show(key)
