"""Execution automation and project permissions are independent, bounded grants."""
import copy
import sys
from pathlib import Path, PurePosixPath
from .store import BoardError

MODES={'manual','approved','autopilot'}
DEFAULT_PERMISSIONS={'write_paths':['.'],'protected_paths':['.git','.tack-orchestration','.agent-work','.env','.aws','.codex','.agents'],'allow_delete':False,'run_checks':True,'network':False,'check_prefixes':[['python3','-m','unittest'],['python3','-m','pytest'],['pytest'],['node','--test'],['npm','test'],['pnpm','test'],['yarn','test']]}

def mode(settings):
    return settings.get('execution_mode') or ('approved' if settings.get('autopilot') else 'manual')

def permissions(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT_PERMISSIONS):raise BoardError('프로젝트 권한 필드를 확인해주세요.')
    for key in ('allow_delete','run_checks','network'):
        if type(value[key]) is not bool:raise BoardError('프로젝트 권한은 boolean 값이어야 합니다.')
    for key in ('write_paths','protected_paths'):
        if not isinstance(value[key],list) or len(value[key])>50:raise BoardError('권한 경로는 50개 이하의 상대 경로 목록이어야 합니다.')
        for path in value[key]:
            if not isinstance(path,str) or not path or len(path)>1000 or path.startswith('/') or '\\' in path or '..' in PurePosixPath(path).parts or any(c in path for c in '*?[]'):raise BoardError('권한에는 와일드카드 없는 프로젝트 상대 경로가 필요합니다.')
    prefixes=value['check_prefixes']
    if not isinstance(prefixes,list) or len(prefixes)>30 or any(not isinstance(p,list) or not p or len(p)>30 or any(not isinstance(v,str) or not v.strip() or len(v)>1000 for v in p) for p in prefixes):raise BoardError('자동 검증 명령은 argv 접두사 목록이어야 합니다.')
    return copy.deepcopy(value)

def effective(goal,current):
    # Existing goals never gain autonomous authority just by upgrading or changing settings.
    grant=goal.get('execution_policy')
    if not grant:return {'execution_mode':'approved' if mode(current)!='manual' and goal.get('execution_settings',{}).get('autopilot') else 'manual','permissions':copy.deepcopy(current['permissions'])}
    rank={'manual':0,'approved':1,'autopilot':2}
    return {'execution_mode':min((grant['execution_mode'],mode(current)),key=rank.get),'permissions':copy.deepcopy(grant['permissions'])}

def within(path,parent):
    return parent=='.' or PurePosixPath(parent)==PurePosixPath(path) or PurePosixPath(parent) in PurePosixPath(path).parents

def file_reason(files,p,workspace):
    root=Path(workspace).resolve()
    for name in files:
        target=(root/name).resolve()
        if not target.is_relative_to(root):return '프로젝트 밖 경로입니다: '+name
        candidates={str(PurePosixPath(name)),target.relative_to(root).as_posix()}
        if any(not any(within(candidate,prefix) for prefix in p['write_paths']) for candidate in candidates):return '수정 허용 경로 밖입니다: '+name
        if any(within(candidate,prefix) or prefix=='.env' and any(part.startswith('.env') for part in PurePosixPath(candidate).parts) for candidate in candidates for prefix in p['protected_paths']):return '보호 경로는 별도 권한 설정이 필요합니다: '+name
    return ''

def command_reason(argv,p,automatic=False):
    if not p['run_checks']:return '프로젝트에서 검증 명령 실행이 허용되지 않았습니다.'
    # Prefixes govern automatic authorization; a manually approved plan authorizes its exact argv.
    if automatic:
        def matches(prefix):
            first=argv[0]==prefix[0] or prefix[0]=='python3' and Path(argv[0]).name in {'python','python3',Path(sys.executable).name}
            return first and argv[1:len(prefix)]==prefix[1:] and len(argv)>=len(prefix)
        if not any(matches(prefix) for prefix in p['check_prefixes']):return '자동 승인 검증 명령 범위 밖입니다: '+' '.join(argv)
    return ''

def job_reason(goal,job,current,workspace,automatic=False):
    if goal.get('team_id'):
        # Team constraints are attached by the workforce before execution/integration.
        for paths in goal.get('team_path_limits',[goal['team_snapshot']['paths']]):
            reason=file_reason(job['files'],{**current['permissions'],'write_paths':paths},workspace)
            if reason:return '팀 범위: '+reason
        if job['employee'] not in goal.get('team_members',goal['team_snapshot']['members']):return '팀에 배치되지 않은 직원입니다.'
    grant=effective(goal,current);policies=[grant['permissions'],current['permissions']]
    for p in policies:
        reason=file_reason(job['files'],p,workspace)
        if reason:return reason
        for argv in job.get('verification',goal.get('verification',[])):
            reason=command_reason(argv,p,automatic)
            if reason:return reason
    return ''

def changes_reason(changes,goal,current):
    for p in (effective(goal,current)['permissions'],current['permissions']):
        if not p['allow_delete'] and any(v['before'] is not None and v['after'] is None for v in changes.values()):return '파일 삭제 권한이 없어 통합하지 않았습니다.'
    return ''
