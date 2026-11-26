"""Read explicitly archived documents without granting arbitrary file access."""
import html
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit
from .store import BoardError


SOURCE_FORMATS = {'.htm': 'html', '.html': 'html', '.markdown': 'markdown', '.md': 'markdown'}
SOURCE_SUFFIXES = SOURCE_FORMATS.keys() | {'.yaml', '.yml', '.ts', '.tsx', '.json', '.py', '.txt'}


def source_format(document):
    return SOURCE_FORMATS.get(Path(document['source']).suffix.lower(), 'text')


def source_document(store, project, key):
    workspace=Path(project['workspace']).resolve()
    if key.startswith('archive:'):
        return store.document(key.removeprefix('archive:'))
    target=(workspace/key).resolve()
    allowed=[(workspace/root).resolve() for root in project['source_roots']]
    if not target.is_relative_to(workspace) or not any(root.is_relative_to(workspace) and target.is_relative_to(root) for root in allowed):
        reason='이 파일은 프로젝트에 허용된 근거 경로 밖에 있습니다.'
    elif not target.is_file():
        reason='근거 파일을 찾을 수 없습니다.'
    elif target.suffix.lower() not in SOURCE_SUFFIXES and target not in allowed:
        reason='이 형식의 근거 파일은 열 수 없습니다.'
    elif target.stat().st_size>1024*1024:
        reason='1MB를 초과하는 근거 파일은 열 수 없습니다.'
    else:
        return {'id':None,'source':str(target),'title':target.name,'text':target.read_text(encoding='utf-8')}
    try:
        return store.document(str(target))
    except BoardError:
        raise BoardError(reason,404) from None


def reference_links(store, project, document):
    links=[]
    targets=re.findall(r'\[[^\]\n]*\]\(([^)\n]+)\)',document['text'])
    targets.extend(match+'.md' if not match.endswith('.md') else match for match in re.findall(r'\[\[([^\]|#]+)(?:[^\]]*)\]\]',document['text']))
    for raw in dict.fromkeys(targets):
        raw=raw.strip('<>')
        parsed=urlsplit(raw)
        if parsed.scheme in {'http','https'}:
            links.append((raw,raw));continue
        if parsed.scheme or not parsed.path:continue
        target=(Path(document['source']).parent/unquote(parsed.path)).resolve()
        try:
            item=source_document(store,project,str(target))
        except (BoardError,OSError):
            continue
        url=(f'/documents/{item["id"]}' if item['id'] else '/source?path='+quote(str(target.relative_to(Path(project['workspace']).resolve()))))
        url+=('&' if '?' in url else '?')+'project='+quote(project['id'])
        links.append((raw,url))
    return links


def render_document(store,project,document):
    escape=html.escape
    project_id=quote(project['id'])
    links=reference_links(store,project,document)
    related='<h2>참조 문서</h2><ul>'+''.join(f'<li><a href="{escape(url,quote=True)}">{escape(label)}</a></li>' for label,url in links)+'</ul>' if links else ''
    command=f'<p>CLI: <code>tack document show {escape(document["id"])}</code></p>' if document['id'] else ''
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(document["title"])}</title><link rel="icon" href="/favicon.svg" type="image/svg+xml" sizes="any"><script src="/theme.js"></script><link rel="stylesheet" href="/dashboard.css"><script src="/common.js" defer></script></head><body><main class="session-layout">'
        f'<nav><a href="/?project={project_id}">작업 보드</a><a href="/documents?project={project_id}">보관 문서</a></nav>'
        f'<h1>{escape(document["title"])}</h1><p>원래 경로: {escape(document["source"])}</p>{command}'
        f'<p>원문 기록입니다. 과거 진행·검증 표시는 현재 상태를 의미하지 않습니다.</p>{related}<pre>{escape(document["text"])}</pre></main></body></html>').encode()


def render_index(store,project):
    escape=html.escape
    rows=''.join(f'<li><a href="/documents/{d["id"]}?project={quote(project["id"])}">{escape(d["title"])}</a> · {escape(d["kind"])}<p>{escape(d["source"])}</p></li>' for d in store.documents())
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>보관 문서</title><link rel="icon" href="/favicon.svg" type="image/svg+xml" sizes="any"><script src="/theme.js"></script><link rel="stylesheet" href="/dashboard.css"><script src="/common.js" defer></script></head><body><main class="session-layout">'
        f'<a href="/?project={quote(project["id"])}">작업 보드</a><h1>보관 문서</h1><p>이관한 원문과 과거 기록. 현재 작업 상태는 작업 보드에서 확인하세요.</p><ul>{rows}</ul></main></body></html>').encode()


def rewrite_references(text, source, archived, project_id, workspace):
    """Replace references to explicitly selected originals, preserving unrelated text."""
    source=Path(source).resolve()
    workspace=Path(workspace).resolve()
    by_name={}
    for path,doc_id in archived.items():
        by_name.setdefault(Path(path).name,[]).append(doc_id)
    def resolve(raw, wiki=False):
        raw=raw.strip('<>')
        parsed=urlsplit(raw)
        if parsed.scheme not in {'','file'} or not parsed.path:
            return None
        value=unquote(parsed.path)
        if wiki and not value.endswith('.md'):value+='.md'
        for target in ((source.parent/value).resolve(),(workspace/value).resolve()):
            if str(target) in archived:return archived[str(target)]
        candidates=by_name.get(Path(value).name,[])
        return candidates[0] if wiki and len(candidates)==1 else None
    def url(doc_id):return f'http://127.0.0.1:8788/documents/{doc_id}?project={quote(project_id)}'
    def link(match):
        target=match[2]
        doc_id=resolve(target)
        return match[1]+url(doc_id)+match[3] if doc_id else match[0]
    text=re.sub(r'(\[[^\]\n]*\]\()([^\)\n]+)(\))',link,text)
    def definition(match):
        doc_id=resolve(match[2])
        return match[1]+url(doc_id) if doc_id else match[0]
    text=re.sub(r'(?m)^(\s*\[[^\]\n]+\]:\s*)(\S+)',definition,text)
    def wiki(match):
        parts=match[1].split('|',1);doc_id=resolve(parts[0].split('#',1)[0],True)
        return '['+(parts[-1] if len(parts)>1 else parts[0])+']('+url(doc_id)+')' if doc_id else match[0]
    text=re.sub(r'\[\[([^\]\n]+)\]\]',wiki,text)
    def code(match):
        doc_id=resolve(match[1])
        return '`archive:'+doc_id+'`' if doc_id else match[0]
    text=re.sub(r'`([^`\n]+)`',code,text)
    def attribute(match):
        doc_id=resolve(html.unescape(match[2]))
        return match[1]+html.escape(url(doc_id),quote=True)+match[3] if doc_id else match[0]
    return re.sub(r'''(?i)(\b(?:href|src)\s*=\s*["'])([^"']+)(["'])''',attribute,text)
