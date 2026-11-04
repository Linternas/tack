"""Core usage ledger and read-only aggregation; never launches agents."""
import json
from .store import BoardError, timestamp

TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')


def number(value):
    return value if type(value) is int and value >= 0 else None


def summarize(rows):
    result = {'runs': len(rows)}
    for key in (*TOKEN_FIELDS, 'turns', 'duration_ms'):
        known = [r[key] for r in rows if r.get(key) is not None]
        result[key] = sum(known) if known else None
        result[key + '_measured_runs'] = len(known)
    paired = [r for r in rows if r.get('input_tokens') is not None and r.get('cached_input_tokens') is not None]
    denominator = sum(r['input_tokens'] for r in paired)
    result['cache_ratio'] = sum(r['cached_input_tokens'] for r in paired) / denominator if denominator else None
    result['cache_measured_runs'] = len(paired)
    return result


class Usage:
    def __init__(self, store):
        self.store = store

    def start(self, run_id, *, task_id, goal_id='', role='', model='', adapter='', phase='initial', round_number=0):
        self.store.detail(task_id)
        data = dict(id=run_id, task_id=task_id, goal_id=goal_id, role=role, model=model,
                    adapter=adapter, phase=phase, round_number=round_number, status='running',
                    turns=None, duration_ms=None, **{key: None for key in TOKEN_FIELDS})
        if any(not isinstance(data[k], str) or len(data[k]) > 200 for k in ('id','goal_id','role','model','adapter')) or not run_id:
            raise BoardError('사용량 실행 식별자를 확인해주세요.')
        if phase not in {'initial','revision','retry','question'} or type(round_number) is not int or round_number < 0:
            raise BoardError('사용량 실행 단계를 확인해주세요.')
        now = timestamp()
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage_runs (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
            db.execute('INSERT INTO usage_runs VALUES (?,?,?,?)', (run_id,json.dumps(data),now,now))

    def turn(self, run_id, usage):
        # One completed-turn event per running stream; the collector owns event deduplication.
        values = {key: number(usage.get(key)) if isinstance(usage,dict) else None for key in TOKEN_FIELDS[:3]}
        if values['cached_input_tokens'] is not None and (values['input_tokens'] is None or values['cached_input_tokens'] > values['input_tokens']):
            values['cached_input_tokens'] = None
        values['total_tokens'] = values['input_tokens'] + values['output_tokens'] if values['input_tokens'] is not None and values['output_tokens'] is not None else None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM usage_runs WHERE id=?',(run_id,)).fetchone()
            if not row:
                raise BoardError('사용량 실행을 찾을 수 없습니다.',404)
            data = json.loads(row[0])
            first = data['turns'] is None
            for key, value in values.items():
                data[key] = value if first else data[key] + value if data[key] is not None and value is not None else None
            data['turns'] = (data['turns'] or 0) + 1
            db.execute('UPDATE usage_runs SET data=?,updated_at=? WHERE id=?',(json.dumps(data),timestamp(),run_id))

    def finish(self, run_id, status, duration_ms):
        if status not in {'succeeded','failed','interrupted'} or type(duration_ms) is not int or duration_ms < 0:
            raise BoardError('사용량 실행 결과를 확인해주세요.')
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM usage_runs WHERE id=?',(run_id,)).fetchone()
            if not row:
                raise BoardError('사용량 실행을 찾을 수 없습니다.',404)
            data = json.loads(row[0]); data.update(status=status,duration_ms=duration_ms)
            db.execute('UPDATE usage_runs SET data=?,updated_at=? WHERE id=?',(json.dumps(data),timestamp(),run_id))

    def overview(self, goal=None, role=None):
        with self.store.connect() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows = {r['id']: {**json.loads(r['data']), 'created_at':r['created_at'], 'updated_at':r['updated_at']} for r in db.execute('SELECT * FROM usage_runs')} if 'usage_runs' in tables else {}
            if 'executions' in tables:
                for row in db.execute('SELECT * FROM executions'):
                    old=json.loads(row['data'])
                    if row['id'] in rows:
                        rows[row['id']]['status']=old.get('status','unknown')
                        continue
                    rows[row['id']] = dict(id=row['id'],task_id=row['task_id'],goal_id=row['goal_id'],role=old.get('role',''),model=old.get('model',''),adapter=old.get('adapter',''),phase='legacy',round_number=None,status=old.get('status','unknown'),turns=None,duration_ms=None,input_tokens=None,cached_input_tokens=None,output_tokens=None,total_tokens=number(old.get('tokens')),created_at=row['created_at'],updated_at=row['updated_at'])
            goal_titles = {r['id']:json.loads(r['data']).get('title',r['id']) for r in db.execute('SELECT id,data FROM goals')} if 'goals' in tables else {}
        for row in rows.values():
            row['goal_title']=goal_titles.get(row['goal_id'],row['goal_id'])
        selected = [r for r in rows.values() if (goal is None or r['goal_id']==goal) and (role is None or r['role']==role)]
        groups = {}
        for key in ('role','goal_id','phase'):
            groups[key] = [{'name':value,'title':goal_titles.get(value,value) if key=='goal_id' else value,**summarize([r for r in selected if r[key]==value])} for value in sorted({r[key] for r in selected})]
        return {'summary':summarize(selected),'groups':groups,'runs':sorted(selected,key=lambda r:(r['created_at'],r['id']),reverse=True)[:100],
                'note':'캐시 입력은 전체 입력에 포함됩니다. 합계는 수집된 값만 포함하며 미수집 값은 0이 아닙니다. 완료 턴 수는 모델 호출 수가 아닙니다. 비용은 산정하지 않습니다.'}
