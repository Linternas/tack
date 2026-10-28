"""Pinned Crewset role definitions adapted to Tack's project-local team policy."""
import copy
import json
from pathlib import Path, PurePosixPath
import re
from .store import BoardError

ASSETS=Path(__file__).parent/'assets'
KINDS={'cto':'plan','designer':'plan','planner':'plan','frontend-app':'implement','frontend-web':'implement','backend':'implement','qa':'verify'}
NAMES={'cto':'CTO','designer':'디자이너','planner':'기획자','frontend-app':'프론트엔드앱','frontend-web':'프론트엔드웹','backend':'백엔드','qa':'QA'}

def catalog():
    data=json.loads((ASSETS/'crewset.roles.json').read_text())
    roles=[]
    for source_id,definition in data['roles'].items():
        instructions=f"Crewset {data['source']['template_version']} · {source_id}\n\n{definition['mission']}"
        for field,title in [('responsibilities','책임'),('principles','일하는 방식'),('deliverables','결과에 포함할 것')]:
            instructions+='\n\n## '+title+'\n'+'\n'.join('- '+value for value in definition[field])
        if source_id=='designer':
            instructions+='\n\n기본 실행은 읽기 전용입니다. 기본 산출물은 화면·상태·반응형·접근성 명세와 기존 시안 참조입니다. 시안·프로토타입 파일 제작은 별도 쓰기 권한 및 범위 승인 후에만 수행합니다.'
        if source_id=='designer':
            instructions=instructions.replace('추상적인 설명만 남기지 않고 확인 가능한 시안이나 프로토타입을 제공합니다.','추상적인 설명만 남기지 않고 화면·상태·인터랙션 명세와 기존 시안 참조를 제공합니다.').replace('변경한 화면, 컴포넌트, 상태, 인터랙션','화면, 컴포넌트, 상태, 인터랙션의 설계 명세')
        instructions+='\n\n작업·세션·결정·검증·인계 기록은 Tack을 사용합니다. 별도 Markdown 세션 원장이나 plans/sessions 파일을 만들지 않습니다. 역할 지침은 승인 범위와 실행 권한을 확대하지 않습니다.'
        roles.append({'id':source_id.replace('-','_'),'source_id':source_id,'label':definition['label'],'mission':definition['mission'],
                      'config':{'name':NAMES[source_id],'kind':KINDS[source_id],'model':'','skills':[],'instructions':instructions}})
    return {'preset':data['preset'],'source':data['source'],'roles':roles}

def merge_roles(existing,requested):
    """Never replace a configured role, model or skill during recommendation/import."""
    result=copy.deepcopy(existing);profiles={r['id']:r['config'] for r in catalog()['roles']}
    for role,config in requested.items():
        if role in result:continue
        if role in profiles:result[role]={**copy.deepcopy(profiles[role]),'model':config.get('model','')}
        else:result[role]={**copy.deepcopy(config),'instructions':'','skills':[]}
    return result

def project_roles(workspace):
    root=Path(workspace).resolve();path=root/'.crewset/config.json'
    if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size>64000:raise BoardError('프로젝트 내부의 유효한 .crewset/config.json이 필요합니다.')
    try:data=json.loads(path.read_text())
    except (ValueError,UnicodeError):raise BoardError('Crewset config JSON을 확인해주세요.')
    if not isinstance(data,dict) or data.get('schemaVersion')!=1 or data.get('preset')!='product-team' or data.get('language')!='ko':raise BoardError('지원하는 Crewset product-team schema 1 / ko 설정이 필요합니다.')
    roles=data.get('roles');docs=data.get('docsDir','plans')
    if not isinstance(roles,list) or not roles or any(not isinstance(r,str) or r not in KINDS for r in roles) or len(set(roles))!=len(roles):raise BoardError('Crewset 역할 목록을 확인해주세요.')
    if not isinstance(docs,str) or not docs or len(docs)>240 or '\x00' in docs:raise BoardError('Crewset docsDir를 확인해주세요.')
    normalized=PurePosixPath(docs.replace('\\','/'))
    if normalized.is_absolute() or '..' in normalized.parts or re.match(r'^[A-Za-z]:',docs):raise BoardError('Crewset 문서 경로는 프로젝트 내부 상대 경로여야 합니다.')
    configurations={r['source_id']:copy.deepcopy(r['config']) for r in catalog()['roles']}
    for role in roles:
        doc=root/str(normalized)/'sessions'/f'role-{role}.md'
        if not doc.resolve().is_relative_to(root):raise BoardError('Crewset 역할 문서가 프로젝트 밖을 가리킵니다.')
        if not doc.exists():continue
        if not doc.is_file() or doc.stat().st_size>64000:raise BoardError('Crewset 역할 문서는 64KB 이하 파일이어야 합니다.')
        content=doc.read_text();heading='## 프로젝트별 메모'
        if heading in content:
            begin=f'<!-- crewset:role-{role}:begin schema=1 -->';end=f'<!-- crewset:role-{role}:end -->'
            if content.count(begin)!=1 or content.count(end)!=1 or content.count(heading)!=1 or not content.index(begin)<content.index(end)<content.index(heading):raise BoardError('Crewset 역할 문서의 관리 블록/프로젝트 메모 경계를 확인해주세요.')
            notes=content.split(heading,1)[1].strip()
            if notes:configurations[role]['instructions']+='\n\n프로젝트별 메모:\n'+notes
    return {r.replace('-','_'):configurations[r] for r in roles}

def apply(team,version,roles=None,workspace=None):
    current=team.settings()
    if type(version) is not int or current['version']!=version:raise BoardError('최신 팀 설정 version을 확인해주세요.',409)
    profiles=catalog()
    if workspace is not None:requested=project_roles(workspace)
    else:
        selected=list(KINDS) if roles is None else roles
        if not isinstance(selected,list) or not selected or any(not isinstance(r,str) or r not in KINDS for r in selected) or len(set(selected))!=len(selected):raise BoardError('Crewset 역할 ID를 확인해주세요.')
        requested={r['id']:r['config'] for r in profiles['roles'] if r['source_id'] in selected}
    added=[r for r in requested if r not in current['roles']];retained=[r for r in requested if r in current['roles']]
    if added:
        # Requested project notes are already combined with the canonical definitions.
        merged=copy.deepcopy(current['roles']);merged.update({r:c for r,c in requested.items() if r not in merged})
        settings=team.save({**current,'roles':merged})
    else:settings=current
    return {'settings':settings,'added':added,'retained':retained,'source':profiles['source']}
