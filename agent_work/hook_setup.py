"""Merge only Tack-owned hook entries; trust remains a Codex runtime decision."""
import json
import shlex
from .tracking import HOOKS


def merge_hooks(raw,launcher):
    value=json.loads(raw) if raw.strip() else {}
    if not isinstance(value,dict) or not isinstance(value.get('hooks',{}),dict):
        raise ValueError('hooks.json 객체 형식을 확인해주세요.')
    command=shlex.quote(str(launcher))+' hook'
    hooks=value.setdefault('hooks',{})
    for event in HOOKS:
        entries=hooks.setdefault(event,[])
        if not isinstance(entries,list):raise ValueError('Hook 목록 형식을 확인해주세요.')
        retained=[]
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get('hooks'),list):
                raise ValueError('기존 Hook 형식을 확인해주세요.')
            for handler in entry['hooks']:
                if isinstance(handler,dict) and handler.get('statusMessage')=='Tack lifecycle record' and (handler.get('type')!='command' or handler.get('command')!=command):
                    raise ValueError('사용자가 변경한 Tack Hook을 자동으로 덮어쓰지 않습니다.')
            handlers=[h for h in entry['hooks'] if not (isinstance(h,dict) and h.get('statusMessage')=='Tack lifecycle record')]
            if handlers:retained.append({**entry,'hooks':handlers})
        retained.append({'hooks':[{'type':'command','command':command,'timeout':3 if event in {'SessionEnd','Interrupt'} else 10,'statusMessage':'Tack lifecycle record'}]})
        hooks[event]=retained
    return json.dumps(value,ensure_ascii=False,indent=2)+'\n'
