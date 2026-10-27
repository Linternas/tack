import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from agent_work.store import Store,BoardError
from agent_work.documents import source_document,render_document
from agent_work.http import BoardServer

class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.store=Store(self.root/'work.sqlite3')
        self.project={'id':'test','name':'Test','workspace':str(self.root),'source_roots':['docs']}
        (self.root/'docs').mkdir()
    def tearDown(self):self.temp.cleanup()
    def test_original_bytes_survive_deletion_and_alias_resolution(self):
        source=self.root/'docs/old.md';raw='# 원문\r\n내용 <script>bad()</script>\r\n'.encode();source.write_bytes(raw)
        doc=self.store.archive_document(source,'work');source.unlink()
        self.assertEqual(doc['text'].encode(),raw)
        self.assertEqual(doc['digest'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(source_document(self.store,self.project,'docs/old.md')['id'],doc['id'])
        self.assertEqual(source_document(self.store,self.project,'archive:'+doc['id'])['text'].encode(),raw)
        rendered=render_document(self.store,self.project,doc).decode()
        self.assertNotIn('<script>',rendered);self.assertIn('&lt;script&gt;',rendered)
    def test_import_is_idempotent_and_new_version_does_not_overwrite_old(self):
        source=self.root/'docs/repeated.md';source.write_text('first')
        first=self.store.archive_document(source);again=self.store.archive_document(source)
        self.assertEqual(first['id'],again['id']);self.assertEqual(len(self.store.documents()),1)
        source.write_text('second');second=self.store.archive_document(source)
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(self.store.document(first['id'])['text'],'first')
    def test_archive_links_resolve_without_arbitrary_filesystem_access(self):
        first=self.root/'docs/first.md';second=self.root/'docs/second.md'
        first.write_text('# First\n[second](second.md)\n[[second]]\n[javascript](javascript:alert(1))')
        second.write_text('# Second')
        a=self.store.archive_document(first);b=self.store.archive_document(second)
        first.unlink();second.unlink()
        rendered=render_document(self.store,self.project,a).decode()
        self.assertIn('/documents/'+b['id'],rendered)
        self.assertNotIn('href="javascript:',rendered)
        for key in ('../outside.md','.env','archive:DOC-MISSING'):
            with self.assertRaises(BoardError):source_document(self.store,self.project,key)
        other=Store(self.root/'other.sqlite3')
        with self.assertRaises(BoardError):source_document(other,self.project,'archive:'+a['id'])
    def test_http_reads_archived_sources_after_original_deletion(self):
        source=self.root/'docs/old.md';source.write_text('# HTTP <b>escaped</b>')
        doc=self.store.archive_document(source);source.unlink()
        server=BoardServer(('127.0.0.1',0),self.store,self.project)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def get(path):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('GET',path);r=c.getresponse();result=r.status,r.read();c.close();return result
        try:
            self.assertEqual(get('/api/source?path=docs/old.md')[0],200)
            self.assertEqual(json.loads(get('/api/source?path=archive:'+doc['id'])[1])['text'],doc['text'])
            status,body=get('/documents/'+doc['id']);self.assertEqual(status,200);self.assertIn(b'&lt;b&gt;',body)
            self.assertEqual(len(json.loads(get('/api/documents')[1])['documents']),1)
            self.assertEqual(get('/documents')[0],200)
            self.assertEqual(get('/api/source?path=../../etc/passwd')[0],404)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()

class ReferenceRewriteTests(unittest.TestCase):
    def test_only_selected_references_change_and_retained_planning_is_preserved(self):
        from agent_work.documents import rewrite_references
        repo=Path('/tmp/project').resolve()
        old=str(repo/'.vscode/working/work.md')
        text='[old](../working/work.md)\n[spec](../working/spec.md)\n[[work|작업]]\n`.vscode/working/work.md`\n[id]: ../working/work.md\n'
        result=rewrite_references(text,repo/'.vscode/plans/index.md',{old:'DOC-ABC'},'project-id',repo)
        self.assertEqual(result.count('/documents/DOC-ABC'),3)
        self.assertIn('[spec](../working/spec.md)',result)
        self.assertIn('`archive:DOC-ABC`',result)
        self.assertNotIn('[[work',result)


class ExtendedSourceTests(unittest.TestCase):
    def test_html_sources_are_text_and_extensionless_files_need_explicit_permission(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();(root/'docs').mkdir();store=Store(root/'test.sqlite3')
            html=root/'docs/design.html';html.write_text('<script>bad()</script><h1>API</h1>')
            api=root/'docs/api_contract';api.write_text('# API')
            project={'id':'test','workspace':str(root),'source_roots':['docs']}
            view=source_document(store,project,'docs/design.html')
            self.assertNotIn('<script>',render_document(store,project,view).decode())
            with self.assertRaises(BoardError):source_document(store,project,'docs/api_contract')
            project['source_roots'].append('docs/api_contract')
            self.assertEqual(source_document(store,project,'docs/api_contract')['text'],'# API')
    def test_deleted_html_targets_are_rewritten_without_changing_other_assets(self):
        from agent_work.documents import rewrite_references
        root=Path('/tmp/project').resolve();old=str(root/'old.html')
        text='<a href="old.html">old</a><img src="image.png"><a href="#api">API</a>'
        result=rewrite_references(text,root/'index.html',{old:'DOC-ABC'},'test',root)
        self.assertIn('/documents/DOC-ABC',result)
        self.assertIn('src="image.png"',result)
        self.assertIn('href="#api"',result)
