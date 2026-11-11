"""Project-local teams; employee IDs survive role and assignment changes."""
import copy, json, re
from pathlib import Path
from .store import BoardError, timestamp
from .execution_policy import within

class Organization:

    def __init__(self, store, workspace):
        self.store, self.workspace = (store, str(Path(workspace).resolve()))

    def exists(self):
        with self.store.connect() as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='organizations'").fetchone())

    def ensure(self):
        if self.exists():
            return
        self.store.backup()
        with self.store.connect() as db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS organizations '
                '(workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,data TEXT NOT NULL,updated_at TEXT NOT NULL)'
            )

    def settings(self):
        from .team import Team
        if self.exists():
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM organizations WHERE workspace=?', (self.workspace,)).fetchone()
            if row:
                return {'version': row['version'], 'teams': json.loads(row['data'])}
        roles = Team(self.store, self.workspace).settings()['roles']
        return {
            'version': 0,
            'teams': {
                'default': {
                    'id': 'default',
                    'name': '기존 팀',
                    'kind': 'product',
                    'members': list(roles),
                    'paths': ['.'],
                    'support_teams': [],
                    'max_parallel': 3,
                    'max_tokens': 2000000,
                },
            },
        }

    def group(self, key):
        groups = self.settings()['teams']
        if not isinstance(key, str) or key not in groups:
            raise BoardError('프로젝트의 팀을 선택해주세요.')
        return groups[key]

    def scope(self, key):
        groups = self.settings()['teams']
        if not isinstance(key, str) or key not in groups:
            raise BoardError('프로젝트의 팀을 선택해주세요.')
        group = groups[key]
        members = list(group['members'])
        for support in group['support_teams']:
            members += groups[support]['members']
        return {**copy.deepcopy(group), 'members': list(dict.fromkeys(members))}

    def validate(self, groups, roles):
        if not 1 <= len(groups) <= 20:
            raise BoardError('프로젝트에는 최대 20개 팀을 둘 수 있습니다.')
        for key, g in groups.items():
            if (
                set(g) != {
                    'id', 'name', 'kind', 'members', 'paths',
                    'support_teams', 'max_parallel', 'max_tokens',
                }
                or g['id'] != key
            ):
                raise BoardError('팀 필드를 확인해주세요.')
            if (
                not isinstance(g['name'], str)
                or not 1 <= len(g['name'].strip()) <= 40
                or not isinstance(g['kind'], str)
                or g['kind'] not in {'product', 'functional'}
            ):
                raise BoardError('팀 이름과 유형을 확인해주세요.')
            if (
                not isinstance(g['members'], list)
                or not 1 <= len(g['members']) <= 12
                or any(not isinstance(m, str) or m not in roles for m in g['members'])
                or len(set(g['members'])) != len(g['members'])
            ):
                raise BoardError('등록된 직원 1~12명을 배치해주세요.')
            if (
                not isinstance(g['support_teams'], list)
                or any(not isinstance(t, str) or t == key or t not in groups for t in g['support_teams'])
                or len(set(g['support_teams'])) != len(g['support_teams'])
            ):
                raise BoardError('지원 팀을 확인해주세요.')
            if not isinstance(g['paths'], list) or len(g['paths']) > 30:
                raise BoardError('담당 경로는 30개 이하의 상대 경로 목록입니다.')
            for name in g['paths']:
                if (
                    not isinstance(name, str)
                    or not name
                    or '\\' in name
                    or Path(name).is_absolute()
                    or '..' in Path(name).parts
                    or any(c in name for c in '*?[]')
                    or any(
                        p in {'.git', '.tack-orchestration', '.agent-work', '.aws', '.codex', '.agents'}
                        for p in Path(name).parts
                    )
                ):
                    raise BoardError('프로젝트 안의 구체적인 앱/패키지 경로를 지정해주세요.')
                if not (Path(self.workspace) / name).resolve().is_relative_to(self.workspace):
                    raise BoardError('프로젝트 밖 담당 경로는 사용할 수 없습니다.')
            if (
                type(g['max_parallel']) is not int
                or not 1 <= g['max_parallel'] <= 8
                or type(g['max_tokens']) is not int
                or not 1000 <= g['max_tokens'] <= 20000000
            ):
                raise BoardError('팀 실행 수/토큰 한도를 확인해주세요.')
        for key in groups:

            def walk(current, seen):
                if current in seen:
                    raise BoardError('지원 팀의 순환 연결은 허용하지 않습니다.')
                for target in groups[current]['support_teams']:
                    walk(target, seen | {current})
            walk(key, set())
            members = set(groups[key]['members'])
            for target in groups[key]['support_teams']:
                members.update(groups[target]['members'])
            if len(members) > 12:
                raise BoardError('지원 팀을 포함한 실행 직원은 12명 이하여야 합니다.')
        owned = [g for k, g in groups.items() if k != 'default']
        for i, a in enumerate(owned):
            for b in owned[i + 1:]:
                if any((within(x, y) or within(y, x) for x in a['paths'] for y in b['paths'])):
                    raise BoardError('담당 경로가 다른 팀과 겹칩니다. 공유 패키지는 소유 팀 하나에 배정해주세요.')

    def save(self, payload, key=None):
        from .team import Team, DEFAULTS
        from .orchestration import identifier
        fields = {
            'version', 'name', 'kind', 'members', 'paths',
            'support_teams', 'max_parallel', 'max_tokens', 'staffing',
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != fields
            or not isinstance(payload['staffing'], str)
            or payload['staffing'] not in {'existing', 'new'}
        ):
            raise BoardError('팀 설정 필드를 확인해주세요.')
        if key == 'default' and payload['staffing'] == 'new':
            raise BoardError('기존 팀의 직원 기록은 유지해주세요.')
        self.ensure()
        team = Team(self.store, self.workspace)
        team.migrate()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.settings()
            if type(payload['version']) is not int or payload['version'] != old['version']:
                raise BoardError('팀 구성이 변경됐습니다. 최신 구성을 확인해주세요.', 409)
            if key is not None and key not in old['teams']:
                raise BoardError('팀을 찾을 수 없습니다.', 404)
            key = key or identifier('TEAM-')
            settings = team.settings()
            groups = copy.deepcopy(old['teams'])
            members = payload['members']
            if payload['staffing'] == 'new':
                if not isinstance(payload['name'], str):
                    raise BoardError('팀 이름이 필요합니다.')
                members = []
                stem = re.sub('\\s|@', '', payload['name'])[:16]
                for kind, label in [('plan', '기획'), ('implement', '개발'), ('verify', 'QA')]:
                    employee = 'e_' + key[5:13].lower() + '_' + kind
                    name = stem + label
                    if any((r['name'] == name for r in settings['roles'].values())):
                        name += key[-3:]
                    template = {**DEFAULTS['roles'][kind], 'kind': kind, 'skills': []}
                    settings['roles'][employee] = {**copy.deepcopy(template), 'name': name}
                    members.append(employee)
            group = {k: payload[k] for k in fields - {'version', 'staffing'}}
            group.update(id=key, members=members)
            groups[key] = group
            self.validate(groups, settings['roles'])
            if payload['staffing'] == 'new':
                team.save(settings, connection=db)
            db.execute(
                'INSERT INTO organizations VALUES (?,?,?,?) ON CONFLICT(workspace) DO UPDATE '
                'SET version=excluded.version,data=excluded.data,updated_at=excluded.updated_at',
                (self.workspace, old['version'] + 1, json.dumps(groups, ensure_ascii=False), timestamp()),
            )
        return self.settings()

    def constraints(self, goal):
        if not goal.get('team_id'):
            return None
        live = self.scope(goal['team_id'])
        old = goal['team_snapshot']
        return {
            'members': set(live['members']) & set(old['members']),
            'paths': [old['paths'], live['paths']],
            'max_parallel': min(old['max_parallel'], live['max_parallel']),
            'max_tokens': min(old['max_tokens'], live['max_tokens']),
        }

    def roles(self, goal, roles):
        c = self.constraints(goal)
        return {k: v for k, v in roles.items() if c is None or k in c['members']}

    def total_tokens(self, key):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='goals'").fetchone():
                return 0
            return sum(
                g.get('token_used', 0)
                for row in db.execute('SELECT data FROM goals')
                if (g := json.loads(row[0])).get('workspace') == self.workspace
                and g.get('team_id') == key
            )

    def reason(self, goal, employee, files):
        c = self.constraints(goal)
        if not c:
            return ''
        if employee not in c['members']:
            return '팀에 배치되지 않은 직원입니다.'
        if any(
            not any(within(name, parent) for parent in paths)
            for paths in c['paths']
            for name in files
        ):
            return '팀 담당 앱/패키지 경로 밖입니다.'
        return ''

    def parallel(self, a, b):
        if not a.get('team_id') or not b.get('team_id') or a['team_id'] == b['team_id']:
            return False
        ca, cb = (self.constraints(a), self.constraints(b))
        return not any(
            within(x, y) or within(y, x)
            for pa in ca['paths']
            for pb in cb['paths']
            for x in pa
            for y in pb
        )
