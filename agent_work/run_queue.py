"""Persistent FIFO goal requests; detached dispatch does not need a Dashboard."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from .store import BoardError,timestamp


def exists(db):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='team_run_queue'").fetchone())


def ensure(store):
    with store.connect() as db:
        if exists(db):return
    store.backup()
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS team_run_queue (goal_id TEXT PRIMARY KEY, goal_version INTEGER NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL, workspace TEXT NOT NULL, sequence INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')


def overview(store,key):
    with store.connect() as db:
        if not exists(db):return None
        row=db.execute('SELECT * FROM team_run_queue WHERE goal_id=?',(key,)).fetchone()
        if not row:return None
        data=dict(row);data['position']=0
        if row['status']=='queued':
            ids=[r[0] for r in db.execute("SELECT goal_id FROM team_run_queue WHERE status='queued' AND workspace=? ORDER BY sequence,goal_id",(row['workspace'],))]
            data['position']=ids.index(key)+1
            owners=[json.loads(r[0]) for r in db.execute('SELECT data FROM goals') if json.loads(r[0]).get('worker') and json.loads(r[0]).get('workspace')==row['workspace']]
            data['reason']='기존 실행 종료 확인·복구가 필요합니다.' if any(time.time()-g.get('heartbeat',0)>30 for g in owners) else '현재 프로젝트의 실행 슬롯을 기다립니다.' if owners else '실행 요청을 준비 중입니다.'
        return data


def wake(project):
    env={**os.environ,'TACK_HARNESS':'1','PYTHONPATH':str(Path(__file__).resolve().parent.parent)+os.pathsep+os.environ.get('PYTHONPATH','')}
    proc=subprocess.Popen([sys.executable,'-m','agent_work','--workspace',project['workspace'],'team','drain'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env=env)
    threading.Thread(target=proc.wait,daemon=True).start()
    return proc.pid


def enqueue(project,key,version):
    from .workforce import Workforce
    from .store import Store
    store=Store(project['db_path']);work=Workforce(store,project['workspace']);goal=work.goal(key)
    if type(version) is not int or version!=goal['version'] or goal.get('worker') or goal['status'] not in {'ready','queued','paused','failed','needs_recheck','awaiting_approval','waiting_answer'}:raise BoardError('최신 목표 상태에서 실행을 요청해주세요.',409)
    ensure(store);now=timestamp()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');work.lock_goal(db,key,version)
        previous=db.execute('SELECT * FROM team_run_queue WHERE goal_id=?',(key,)).fetchone()
        if not (previous and previous['status']=='queued' and previous['goal_version']==version):
            work.set_goal(db,key,status='queued',pause_requested=False,error='')
            current=db.execute('SELECT version FROM goals WHERE id=?',(key,)).fetchone()[0]
            created=previous['created_at'] if previous and previous['status']=='queued' else now
            sequence=previous['sequence'] if previous and previous['status']=='queued' else db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM team_run_queue').fetchone()[0]
            db.execute('INSERT INTO team_run_queue VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET goal_version=excluded.goal_version,status=excluded.status,error=excluded.error,workspace=excluded.workspace,sequence=excluded.sequence,created_at=excluded.created_at,updated_at=excluded.updated_at',(key,current,'queued','',work.workspace,sequence,created,now))
            work.team.insert(db,key,'user','실행 큐에 요청을 넣었습니다. 선행 업무와 빈 직원 슬롯에 맞춰 진행합니다.','queued',data={'goal_id':key})
    pid=wake(project)
    return {'accepted':True,'goal_id':key,'queue':overview(store,key),'worker_launcher_pid':pid,'note':'큐 수락은 실행 시작이나 완료가 아닙니다.'}


def cancel(work,key,version):
    goal=work.goal(key)
    if goal.get('worker'):return work.orch.pause(key,version)
    with work.store.connect() as db:
        db.execute('BEGIN IMMEDIATE');work.lock_goal(db,key,version)
        if exists(db):db.execute("UPDATE team_run_queue SET status='cancelled',updated_at=? WHERE goal_id=? AND status IN ('queued','running')",(timestamp(),key))
        work.set_goal(db,key,status='paused',pause_requested=True,error='실행 대기를 취소했습니다.')
    return work.goal(key)


def recovered(db,key):
    if exists(db):db.execute("UPDATE team_run_queue SET status='cancelled',error='기존 실행 종료 확인 후 복구됨',updated_at=? WHERE goal_id=?",(timestamp(),key))


def drain(store,workspace):
    from .organization import Organization
    if len(Organization(store,workspace).settings()['teams'])>1:return drain_teams(store,workspace)
    from .workforce import Workforce
    work=Workforce(store,workspace);ensure(store)
    folder=Path(workspace)/'.tack-orchestration';folder.mkdir(exist_ok=True)
    with (folder/'dispatch.lock').open('a+b') as lock:
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                with store.connect() as db:
                    pending=db.execute("SELECT 1 FROM team_run_queue WHERE status='queued' AND workspace=?",(work.workspace,)).fetchone()
                    active=any(json.loads(r[0]).get('worker') and json.loads(r[0]).get('workspace')==work.workspace for r in db.execute('SELECT data FROM goals'))
                if not pending or active:return {'already_running':True}
                time.sleep(.1)
        try:
            # A stale claimed request never implies that an agent stopped or its work completed.
            with store.connect() as db:
                for row in db.execute("SELECT goal_id FROM team_run_queue WHERE status='running' AND workspace=?",(work.workspace,)).fetchall():
                    root=db.execute('SELECT data FROM goals WHERE id=?',(row[0],)).fetchone()
                    if not root or not json.loads(root[0]).get('worker'):db.execute("UPDATE team_run_queue SET status='needs_recheck',error='이전 큐 처리 상태를 확인하고 재요청해주세요.',updated_at=? WHERE goal_id=?",(timestamp(),row[0]))
            while True:
                with store.connect() as db:
                    pending=db.execute("SELECT * FROM team_run_queue WHERE status='queued' AND workspace=? ORDER BY sequence,goal_id LIMIT 1",(work.workspace,)).fetchone()
                    owners=[json.loads(r[0]) for r in db.execute('SELECT data FROM goals') if json.loads(r[0]).get('worker') and json.loads(r[0]).get('workspace')==work.workspace]
                if not pending:return {'drained':True}
                if owners:
                    if any(time.time()-g.get('heartbeat',0)>30 for g in owners):return {'waiting_recovery':True}
                    time.sleep(.2);continue
                key=pending['goal_id'];claimed=False
                with store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=db.execute("SELECT * FROM team_run_queue WHERE goal_id=? AND status='queued'",(key,)).fetchone()
                    if not row:continue
                    root=db.execute('SELECT version,data FROM goals WHERE id=?',(key,)).fetchone()
                    if not root or root['version']!=row['goal_version']:
                        db.execute("UPDATE team_run_queue SET status='needs_recheck',error='대기 중 목표가 변경됐습니다. 최신 상태에서 다시 요청해주세요.',updated_at=? WHERE goal_id=?",(timestamp(),key));continue
                    if json.loads(root['data']).get('worker'):continue
                    db.execute("UPDATE team_run_queue SET status='running',updated_at=? WHERE goal_id=?",(timestamp(),key));claimed=True
                if not claimed:continue
                try:
                    work.run(key,row['goal_version'])
                    with store.connect() as db:
                        db.execute("UPDATE team_run_queue SET status='finished',error='',updated_at=? WHERE goal_id=? AND status='running'",(timestamp(),key))
                    goal=work.goal(key);jobs=work.jobs(key);done={j['id'] for j in jobs if j['status']=='done'}
                    ready=any(j['status']=='queued' and set(j['depends_on'])<=done for j in jobs)
                    if ready and not goal.get('pause_requested') and goal['status'] not in {'done','paused','failed','needs_recheck'}:
                        with store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            current=db.execute('SELECT version,data FROM goals WHERE id=?',(key,)).fetchone()
                            state=json.loads(current['data'])
                            if not state.get('worker') and not state.get('pause_requested'):
                                work.set_goal(db,key,status='queued');version=db.execute('SELECT version FROM goals WHERE id=?',(key,)).fetchone()[0]
                                sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM team_run_queue').fetchone()[0]
                                db.execute("UPDATE team_run_queue SET goal_version=?,status='queued',sequence=?,created_at=?,updated_at=? WHERE goal_id=?",(version,sequence,timestamp(),timestamp(),key))
                except Exception as error:
                    with store.connect() as db:db.execute("UPDATE team_run_queue SET status='needs_recheck',error=?,updated_at=? WHERE goal_id=? AND status='running'",(str(error)[:1000],timestamp(),key))
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def drain_teams(store,workspace):
    """One durable dispatcher, parallel disjoint teams, one active goal per team."""
    from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
    from .workforce import Workforce
    from .organization import Organization
    work=Workforce(store,workspace);org=Organization(store,workspace);ensure(store)
    folder=Path(workspace)/'.tack-orchestration';folder.mkdir(exist_ok=True)
    with (folder/'dispatch.lock').open('a+b') as lock:
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                with store.connect() as db:
                    pending=db.execute("SELECT 1 FROM team_run_queue WHERE status='queued' AND workspace=?",(work.workspace,)).fetchone()
                    owners=any(json.loads(row[0]).get('worker') and json.loads(row[0]).get('workspace')==work.workspace for row in db.execute('SELECT data FROM goals'))
                if not pending or owners:return {'already_running':True}
                time.sleep(.1)
        def execute(key,version):
            instance=Workforce(store,workspace)
            try:
                instance.run(key,version)
                with store.connect() as db:db.execute("UPDATE team_run_queue SET status='finished',error='',updated_at=? WHERE goal_id=? AND status='running'",(timestamp(),key))
                goal=instance.goal(key);jobs=instance.jobs(key);done={j['id'] for j in jobs if j['status']=='done'}
                if any(j['status']=='queued' and set(j['depends_on'])<=done for j in jobs) and not goal.get('pause_requested') and goal['status'] not in {'done','paused','failed','needs_recheck'}:
                    with store.connect() as db:
                        db.execute('BEGIN IMMEDIATE');current=instance.lock_goal(db,key,goal['version'])
                        if current.get('worker') or current.get('pause_requested'):return
                        instance.set_goal(db,key,status='queued');version=db.execute('SELECT version FROM goals WHERE id=?',(key,)).fetchone()[0]
                        sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM team_run_queue').fetchone()[0]
                        db.execute("UPDATE team_run_queue SET goal_version=?,status='queued',sequence=?,updated_at=? WHERE goal_id=?",(version,sequence,timestamp(),key))
            except Exception as error:
                with store.connect() as db:db.execute("UPDATE team_run_queue SET status='needs_recheck',error=?,updated_at=? WHERE goal_id=? AND status='running'",(str(error)[:1000],timestamp(),key))
        active={}
        with ThreadPoolExecutor(max_workers=8,thread_name_prefix='tack-team') as pool:
            while True:
                with store.connect() as db:
                    pending=[dict(r) for r in db.execute("SELECT * FROM team_run_queue WHERE status='queued' AND workspace=? ORDER BY sequence,goal_id",(work.workspace,))]
                    owners=[work.orch.decode(r) for r in db.execute('SELECT * FROM goals') if json.loads(r['data']).get('worker') and json.loads(r['data']).get('workspace')==work.workspace]
                    for row in db.execute("SELECT goal_id FROM team_run_queue WHERE status='running' AND workspace=?",(work.workspace,)).fetchall():
                        if row[0] not in active and not any(g['id']==row[0] for g in owners):db.execute("UPDATE team_run_queue SET status='needs_recheck',error='이전 실행 종료를 확인하고 재요청해주세요.',updated_at=? WHERE goal_id=?",(timestamp(),row[0]))
                if any(time.time()-g.get('heartbeat',0)>30 for g in owners):
                    if not active:return {'waiting_recovery':True}
                    pending=[]
                claimed=[work.goal(key) for key in active]
                capacity=work.team.settings()['max_parallel']
                waiting_teams=set()
                for request in pending:
                    if len(active)>=capacity:break
                    goal=work.goal(request['goal_id'])
                    team_id=goal.get('team_id') or 'default'
                    if team_id in waiting_teams:continue
                    if goal['version']!=request['goal_version']:
                        with store.connect() as db:db.execute("UPDATE team_run_queue SET status='needs_recheck',error='대기 중 목표가 변경됐습니다.',updated_at=? WHERE goal_id=? AND status='queued' AND goal_version=?",(timestamp(),goal['id'],request['goal_version']))
                        continue
                    if goal.get('worker') or any(not org.parallel(goal,other) for other in [*owners,*claimed] if other['id']!=goal['id']):
                        waiting_teams.add(team_id)
                        continue
                    with store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        queued=db.execute("SELECT goal_version FROM team_run_queue WHERE goal_id=? AND status='queued'",(goal['id'],)).fetchone()
                        current=db.execute('SELECT version,data FROM goals WHERE id=?',(goal['id'],)).fetchone()
                        if not queued or queued['goal_version']!=request['goal_version']:continue
                        if not current or current['version']!=queued['goal_version']:
                            db.execute("UPDATE team_run_queue SET status='needs_recheck',error='대기 중 목표가 변경됐습니다.',updated_at=? WHERE goal_id=? AND status='queued'",(timestamp(),goal['id']))
                            continue
                        if json.loads(current['data']).get('worker'):
                            waiting_teams.add(team_id)
                            continue
                        updated=db.execute("UPDATE team_run_queue SET status='running',updated_at=? WHERE goal_id=? AND status='queued'",(timestamp(),goal['id'])).rowcount
                    if updated:active[goal['id']]=pool.submit(execute,goal['id'],request['goal_version']);claimed.append(goal)
                finished=[key for key,future in active.items() if future.done()]
                for key in finished:active.pop(key).result()
                # A completed future may have queued its next round after our snapshot.
                if finished:continue
                if not active:
                    if pending and owners:time.sleep(.2);continue
                    return {'drained':True}
                wait(active.values(),timeout=.15,return_when=FIRST_COMPLETED)
