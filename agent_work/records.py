"""Bounded task heads and lossless, searchable work records in the project database."""
from contextlib import closing, contextmanager
import hashlib
import json
import sqlite3
from .store import BoardError, TEXT_FIELDS, timestamp

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200
KINDS = {'task', 'checkpoint', 'history', 'document', 'decision', 'verification', 'note'}
DERIVED_KINDS = ('task', 'checkpoint', 'history', 'document')
HEAD_KEYS = ('id', 'title', 'category', 'status', 'priority', 'owner', 'version', 'updated_at', 'summary', 'next_action', 'acceptance', 'blocked_by', 'truncated_fields')


def bounded(value, limit):
    return value if len(value) <= limit else value[:limit] + '…'


def present(db, name):
    return bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone())


class Records:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def schema(db):
        statements = [
            'CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS record_dirty (kind TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(kind,source_id))',
            'CREATE TABLE IF NOT EXISTS task_heads (id TEXT PRIMARY KEY,data TEXT NOT NULL)',
            '''CREATE TABLE IF NOT EXISTS work_records (
                id TEXT PRIMARY KEY,task_id TEXT NOT NULL,kind TEXT NOT NULL,section TEXT NOT NULL,
                source_id TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL,
                part INTEGER NOT NULL,parts INTEGER NOT NULL,start_offset INTEGER NOT NULL,
                current INTEGER NOT NULL,created_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS work_records_task ON work_records(task_id,kind,current)',
            'CREATE INDEX IF NOT EXISTS work_records_group ON work_records(task_id,kind,section,digest,part)',
        ]
        for statement in statements:
            db.execute(statement)
        for table,kind,key in (('tasks','task','id'),('history','history','id'),('checkpoints','checkpoint','id'),('documents','document','id')):
            if not present(db,table):continue
            for event in (('INSERT','UPDATE') if table=='tasks' else ('INSERT',)):
                name='record_dirty_'+table+'_'+event.lower()
                db.execute(f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON {table} BEGIN INSERT OR REPLACE INTO record_dirty VALUES ('{kind}',CAST(new.{key} AS TEXT)); END")
        if not present(db, 'record_fts'):
            try:
                db.execute("CREATE VIRTUAL TABLE record_fts USING fts5(title,body,content='work_records',content_rowid='rowid',tokenize='trigram')")
            except sqlite3.OperationalError as error:
                if 'no such module' not in str(error) and 'tokenizer' not in str(error):
                    raise
                return
            db.execute("INSERT INTO record_fts(record_fts) VALUES ('rebuild')")
        db.execute('''CREATE TRIGGER IF NOT EXISTS work_records_insert AFTER INSERT ON work_records BEGIN
            INSERT INTO record_fts(rowid,title,body) VALUES (new.rowid,new.title,new.body); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS work_records_delete AFTER DELETE ON work_records BEGIN
            INSERT INTO record_fts(record_fts,rowid,title,body) VALUES ('delete',old.rowid,old.title,old.body); END''')

    @staticmethod
    def put(db, task_id, kind, section, source_id, title, body, created_at, current=1):
        if not body:
            return []
        digest = hashlib.sha256(body.encode()).hexdigest()
        offsets = list(range(0, len(body), CHUNK_SIZE-CHUNK_OVERLAP))
        if len(offsets)>1 and len(body)-offsets[-1] <= CHUNK_OVERLAP:
            offsets.pop()
        ids = []
        for part, offset in enumerate(offsets, 1):
            identity = [task_id, kind, section, title, digest, part]
            if kind == 'history':
                identity.append(source_id)
            key = 'REC-' + hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24].upper()
            db.execute('''INSERT INTO work_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET current=excluded.current''',
                (key,task_id,kind,section,source_id,(task_id+' '+title).strip(),body[offset:offset+CHUNK_SIZE],digest,part,len(offsets),offset,current,created_at))
            ids.append(key)
        return ids

    @classmethod
    def task(cls, db, task):
        if not present(db, 'work_records'):
            return
        head = {k:task[k] for k in HEAD_KEYS if k not in {'truncated_fields'}}
        head['truncated_fields'] = []
        for key, limit in (('summary',240),('next_action',480),('acceptance',480)):
            if len(head[key])>limit:
                head['truncated_fields'].append(key)
                head[key] = bounded(head[key],limit)
        db.execute('INSERT INTO task_heads VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (task['id'],json.dumps(head,ensure_ascii=False)))
        db.execute("UPDATE work_records SET current=0 WHERE task_id=? AND kind='task'", (task['id'],))
        db.execute("DELETE FROM record_dirty WHERE kind='task' AND source_id=?",(task['id'],))
        sections = {k:task[k] for k in sorted(TEXT_FIELDS)}
        sections.update({'qa.'+k:v for k,v in task['qa'].items()})
        for key in ('areas','sources','blocked_by'):
            if task[key]:
                sections[key] = json.dumps(task[key],ensure_ascii=False)
        for section, body in sections.items():
            cls.put(db,task['id'],'task',section,task['id'],task['title'],body,task['updated_at'])

    @classmethod
    def checkpoint(cls, db, task_id, source_id, data, created_at):
        if not present(db,'work_records'):
            return
        db.execute("DELETE FROM record_dirty WHERE kind='checkpoint' AND source_id=?",(source_id.removeprefix('CHECK-'),))
        for section in ('goal','decisions','changed_files','verification','blockers','next_action'):
            cls.put(db,task_id,'checkpoint',section,source_id,data['goal'][:200],data.get(section,''),created_at)

    @classmethod
    def history(cls, db, task_id, source_id, note, created_at):
        if present(db,'work_records'):
            cls.put(db,task_id,'history','note',source_id,task_id,note,created_at)
            db.execute("DELETE FROM record_dirty WHERE kind='history' AND source_id=?",(source_id.removeprefix('HIST-'),))

    @classmethod
    def document(cls, db, doc):
        if present(db,'work_records'):
            cls.put(db,'','document','text',doc['id'],doc['title'],doc['text'],doc['created_at'])
            db.execute("DELETE FROM record_dirty WHERE kind='document' AND source_id=?",(doc['id'],))

    def populate(self, source, target):
        for row in source.execute('SELECT * FROM tasks'):
            self.task(target,self.store.decode(row))
        for row in source.execute('SELECT * FROM history'):
            self.history(target,row['task_id'],'HIST-'+str(row['id']),row['note'],row['created_at'])
        for row in source.execute('SELECT c.*,s.task_id FROM checkpoints c JOIN sessions s ON s.id=c.session_id'):
            self.checkpoint(target,row['task_id'],'CHECK-'+str(row['id']),json.loads(row['data']),row['created_at'])
        for row in source.execute('SELECT * FROM documents'):
            self.document(target,dict(row))

    @staticmethod
    def fingerprint(db):
        digest=hashlib.sha256()
        for table in ('tasks','history','sessions','checkpoints','documents','imports'):
            digest.update(table.encode())
            for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid'):
                digest.update(json.dumps(list(row),ensure_ascii=False).encode())
        return digest.hexdigest()

    def sync(self,db):
        for item in db.execute('SELECT * FROM record_dirty').fetchall():
            kind,key=item['kind'],item['source_id']
            if kind=='task':
                row=db.execute('SELECT * FROM tasks WHERE id=?',(key,)).fetchone()
                if row:self.task(db,self.store.decode(row))
            elif kind=='history':
                row=db.execute('SELECT * FROM history WHERE id=?',(key,)).fetchone()
                if row:self.history(db,row['task_id'],'HIST-'+key,row['note'],row['created_at'])
            elif kind=='checkpoint':
                row=db.execute('SELECT c.*,s.task_id FROM checkpoints c JOIN sessions s ON s.id=c.session_id WHERE c.id=?',(key,)).fetchone()
                if row:self.checkpoint(db,row['task_id'],'CHECK-'+key,json.loads(row['data']),row['created_at'])
            else:
                row=db.execute('SELECT * FROM documents WHERE id=?',(key,)).fetchone()
                if row:self.document(db,dict(row))
            db.execute('DELETE FROM record_dirty WHERE kind=? AND source_id=?',(kind,key))

    @contextmanager
    def read_index(self):
        with self.store.connect() as source:
            source.execute('BEGIN')
            ready=present(source,'work_records')
            dirty=ready and source.execute('SELECT 1 FROM record_dirty LIMIT 1').fetchone()
            if ready and not dirty:
                yield source,True
                return
            with closing(sqlite3.connect(':memory:')) as temporary:
                temporary.row_factory=sqlite3.Row
                self.schema(temporary)
                self.populate(source,temporary)
                if ready:
                    for row in source.execute("SELECT * FROM work_records WHERE kind IN ('decision','verification','note')"):
                        temporary.execute('INSERT INTO work_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(row))
                yield temporary,False

    def ensure(self, rebuild=False):
        with self.store.connect() as db:
            ready = present(db,'work_records')
        if ready and not rebuild:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.sync(db)
            return {'indexed':True}
        with self.store.connect() as db:
            has_data=any(db.execute('SELECT 1 FROM '+name+' LIMIT 1').fetchone() for name in ('tasks','sessions','documents'))
        backup = str(self.store.backup()) if has_data else None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if present(db,'work_records') and not rebuild:
                return {'indexed':True}
            before=self.fingerprint(db)
            self.schema(db)
            if rebuild:
                db.execute("DELETE FROM work_records WHERE kind IN ('task','checkpoint','history','document')")
                db.execute('DELETE FROM task_heads')
            self.populate(db,db)
            db.execute("INSERT INTO metadata VALUES ('record_schema','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            tasks = db.execute('SELECT count(*) FROM tasks').fetchone()[0]
            if tasks != db.execute('SELECT count(*) FROM task_heads').fetchone()[0]:
                raise BoardError('작업 인덱스 이관 검증에 실패했습니다.',503)
            count = db.execute('SELECT count(*) FROM work_records').fetchone()[0]
            if self.fingerprint(db)!=before:raise BoardError('기존 작업 원문 보존 검증에 실패했습니다.',503)
            db.execute("INSERT INTO metadata VALUES ('record_migration',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps({'digest':before,'backup':backup,'records':count,'verified':True,'created_at':timestamp()}),))
        return {'indexed':True,'tasks':tasks,'records':count,'backup':backup}

    def heads(self):
        with self.read_index() as (db,_):
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM task_heads')]

    def overview(self, task_id):
        head=next((t for t in self.heads() if t['id']==task_id),None)
        if not head:
            raise BoardError('작업을 찾을 수 없습니다.',404)
        with self.read_index() as (db,_):
            counts = {r['kind']:r['count'] for r in db.execute('SELECT kind,count(*) AS count FROM work_records WHERE task_id=? AND current=1 GROUP BY kind',(task_id,))} if present(db,'work_records') else {}
        return {'task':head,'record_counts':counts}

    def add(self, task_id, kind, text):
        if kind not in {'decision','verification','note'} or not isinstance(text,str) or not text.strip() or len(text)>30000:
            raise BoardError('결정·검증·메모 본문을 1~30,000자로 입력해주세요.')
        self.ensure()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not row:raise BoardError('작업을 찾을 수 없습니다.',404)
            task=self.store.decode(row)
            ids=self.put(db,task_id,kind,kind,'manual',task['title'],text,timestamp())
        return {'task_id':task_id,'kind':kind,'record_ids':ids}

    @staticmethod
    def metadata(row):
        return {k:row[k] for k in ('id','task_id','kind','section','source_id','part','parts','created_at')}

    def list(self, task_id, kind=None, section=None, limit=5, offset=0):
        self.store.detail(task_id)
        with self.read_index() as (db,_):
            if not present(db,'work_records'):
                return {'records':[],'indexed':False,'next_action':'tack index'}
            where='task_id=? AND current=1';params=[task_id]
            if kind:where+=' AND kind=?';params.append(kind)
            if section:where+=' AND section=?';params.append(section)
            total=db.execute('SELECT count(*) FROM work_records WHERE '+where,params).fetchone()[0]
            rows=db.execute('SELECT * FROM work_records WHERE '+where+' ORDER BY created_at DESC,kind,section,part LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
        return {'records':[self.metadata(r) for r in rows],'total':total,'offset':offset,'has_more':offset+len(rows)<total}

    def show(self, key, full=False):
        with self.read_index() as (db,_):
            if not present(db,'work_records'):raise BoardError('검색 인덱스를 먼저 생성해주세요: tack index',404)
            row=db.execute('SELECT * FROM work_records WHERE id=?',(key,)).fetchone()
            if not row:raise BoardError('레코드를 찾을 수 없습니다.',404)
            siblings=db.execute('SELECT * FROM work_records WHERE task_id=? AND kind=? AND section=? AND digest=? AND title=? ORDER BY part',(row['task_id'],row['kind'],row['section'],row['digest'],row['title'])).fetchall()
            # Repeated history notes have distinct source IDs.
            if row['kind']=='history':siblings=[r for r in siblings if r['source_id']==row['source_id']]
        result=self.metadata(row)
        result['current']=bool(row['current'])
        if full:
            body=''
            for part in siblings:body+=part['body'][max(0,len(body)-part['start_offset']):]
            if hashlib.sha256(body.encode()).hexdigest()!=row['digest']:
                raise BoardError('레코드 본문 무결성 검증에 실패했습니다.',503)
            result['text']=body
        else:
            result['text']=row['body']
            position=next(i for i,r in enumerate(siblings) if r['id']==key)
            result['previous_record']=siblings[position-1]['id'] if position else None
            result['next_record']=siblings[position+1]['id'] if position+1<len(siblings) else None
        return result

    def search(self, query, limit=5, statuses=None, categories=None, kind=None, include_archived=False):
        if not isinstance(query,str) or not 1<=len(query.strip())<=200:
            raise BoardError('검색어를 1~200자로 입력해주세요.')
        with self.read_index() as (db,indexed):
            return self.query(db,query,limit,statuses,categories,kind,include_archived,indexed)

    @classmethod
    def query(cls, db, query, limit, statuses, categories, kind, include_archived, indexed):
        terms=query.split()
        where=['r.current=1'];params=[]
        if statuses:
            where.append("json_extract(h.data,'$.status') IN ("+','.join('?' for _ in statuses)+')');params+=sorted(statuses)
        elif not include_archived:
            where.append("COALESCE(json_extract(h.data,'$.status'),'')!='archived'")
        if categories:
            where.append("json_extract(h.data,'$.category') IN ("+','.join('?' for _ in categories)+')');params+=sorted(categories)
        if kind:where.append('r.kind=?');params.append(kind)
        fts=present(db,'record_fts') and all(len(t)>=3 for t in terms)
        join=' JOIN record_fts ON record_fts.rowid=r.rowid' if fts else ''
        if fts:
            where.append('record_fts MATCH ?');params.append(' AND '.join('"'+t.replace('"','""')+'"' for t in terms))
        else:
            for term in terms:
                where.append("lower(r.title||' '||r.body) LIKE ? ESCAPE '\\'")
                params.append('%'+term.casefold().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        score='bm25(record_fts,5.0,1.0)' if fts else '0'
        sql='SELECT r.*,h.data AS head,'+score+' AS relevance FROM work_records r LEFT JOIN task_heads h ON h.id=r.task_id'+join+' WHERE '+' AND '.join(where)+' ORDER BY relevance,r.created_at DESC'
        matches=[];seen=set()
        for row in db.execute(sql,params):
            identity=row['task_id'] or row['source_id']
            if identity in seen:continue
            seen.add(identity)
            if len(matches)==limit:break
            head=json.loads(row['head']) if row['head'] else {}
            body=row['body'];positions=[body.casefold().find(t.casefold()) for t in terms];positions=[p for p in positions if p>=0]
            start=max(0,min(positions,default=0)-40)
            match=cls.metadata(row)
            match.update(title=head.get('title',row['title']),status=head.get('status',''),version=head.get('version'),snippet=bounded(body[start:],160))
            matches.append(match)
        return {'query':query,'matches':matches,'has_more':len(seen)>limit,'indexed':indexed,'engine':'fts5-trigram' if fts else 'substring'}
