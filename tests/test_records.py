import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from agent_work.records import Records
from agent_work.store import BoardError, Store


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'work.sqlite3')
        self.task=self.store.write({'title':'로그인 재시도 MixedCase','detail':'결산 요청을 다시 확인합니다.','acceptance':'실패 후 재시도 가능'})['task']
        self.records=Records(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_korean_partial_short_keywords_case_and_literal_operators(self):
        for query in ('로그인','재시','결산','mixedcase',self.task['id']):
            with self.subTest(query=query):
                self.assertEqual(self.records.search(query)['matches'][0]['task_id'],self.task['id'])
        for query in ('" OR *','%_','없는검색어'):
            self.assertEqual(self.records.search(query)['matches'],[])

    def test_chunks_are_lossless_and_bounded_with_navigation(self):
        text=('한글 본문 검증\n'*900)+'마지막 조건 유지'
        receipt=self.records.add(self.task['id'],'verification',text)
        key=receipt['record_ids'][0]
        shown=self.records.show(key)
        self.assertLessEqual(len(shown['text']),2000)
        self.assertIsNotNone(shown['next_record'])
        self.assertNotIn('other_parts',shown)
        self.assertEqual(self.records.show(key,True)['text'],text)
        self.assertEqual(self.records.add(self.task['id'],'verification',text),receipt)

    def test_long_fields_have_explicit_previews_and_complete_records(self):
        content='확정된 검증 조건 '+('필수 조건 유지\n'*1200)
        self.store.write({'version':self.task['version'],'verification':content,'summary':'요약'*500,'next_action':'다음'*500},self.task['id'])
        overview=self.records.overview(self.task['id'])
        self.assertLess(len(json.dumps(overview,ensure_ascii=False).encode()),5000)
        self.assertEqual(set(overview['task']['truncated_fields']),{'summary','next_action'})
        rows=self.records.list(self.task['id'],section='verification')['records']
        self.assertEqual(self.records.show(rows[0]['id'],True)['text'],content)

    def test_updates_hide_stale_current_text_and_keep_old_records_readable(self):
        old=self.records.search('결산')['matches'][0]['id']
        self.store.write({'version':1,'detail':'새로운 수자원 계약','title':'새 작업'},self.task['id'])
        self.assertEqual(self.records.search('결산')['matches'],[])
        self.assertEqual(self.records.search('수자원')['matches'][0]['version'],2)
        self.assertFalse(self.records.show(old)['current'])
        self.assertIn('결산',self.records.show(old,True)['text'])

    def test_rejected_write_rolls_back_index_and_head(self):
        before=self.records.search('결산')
        with self.assertRaises(BoardError):
            self.store.write({'version':0,'detail':'유실시키면 안됨'},self.task['id'])
        self.assertEqual(self.records.search('결산'),before)
        self.assertEqual(self.records.search('유실시키면')['matches'],[])

    def test_legacy_writer_is_detected_without_read_side_writes(self):
        self.records.add(self.task['id'],'decision','사용자가 승인한 정책')
        with self.store.connect() as db:
            body=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(self.task['id'],)).fetchone()[0])
            body['detail']='기존 실행기에서 새 검색어 추가'
            db.execute('UPDATE tasks SET data=?,version=version+1 WHERE id=?',(json.dumps(body,ensure_ascii=False),self.task['id']))
        reader=Records(Store(self.store.path,readonly=True))
        before=self.store.path.read_bytes()
        result=reader.search('검색어')
        self.assertFalse(result['indexed'])
        self.assertEqual(result['matches'][0]['version'],2)
        self.assertEqual(reader.heads()[0]['version'],2)
        self.assertEqual(reader.search('승인한 정책')['matches'][0]['kind'],'decision')
        self.assertEqual(before,self.store.path.read_bytes())
        fresh=Store(self.store.path)
        self.assertTrue(Records(fresh).search('검색어')['indexed'])

    def test_checkpoint_document_and_history_search(self):
        self.store.checkpoint({'task_id':self.task['id'],'goal':'복구 시나리오','decisions':'사용자 음수 허용 결정','next_action':'테스트 실행'})
        doc=self.root/'old.md';doc.write_text('기존 문서의 고유 계약 보존')
        self.store.archive_document(doc)
        self.store.write({'version':1,'note':'반려된 이전 판단 근거'},self.task['id'])
        self.assertEqual(self.records.search('음수 허용',kind='checkpoint')['matches'][0]['task_id'],self.task['id'])
        self.assertEqual(self.records.search('고유 계약',kind='document')['matches'][0]['kind'],'document')
        self.assertEqual(self.records.search('이전 판단',kind='history')['matches'][0]['kind'],'history')

    def test_old_database_migration_preserves_canonical_data_and_backup(self):
        with self.store.connect() as db:
            before=self.records.fingerprint(db)
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '%record%'").fetchall():
                db.execute('DROP TRIGGER '+row[0])
            db.execute('DROP TABLE record_fts')
            db.execute('DROP TABLE work_records');db.execute('DROP TABLE task_heads');db.execute('DROP TABLE record_dirty')
        migrated=Store(self.store.path)
        with migrated.connect() as db:
            self.assertEqual(Records.fingerprint(db),before)
            audit=json.loads(db.execute("SELECT value FROM metadata WHERE key='record_migration'").fetchone()[0])
        self.assertTrue(audit['verified']);self.assertTrue(Path(audit['backup']).exists())
        self.assertEqual(Records(migrated).search('결산')['matches'][0]['task_id'],self.task['id'])

    def test_rebuild_preserves_explicit_records_and_deduplication(self):
        receipt=self.records.add(self.task['id'],'decision','사람이 결정한 최종 정책')
        self.records.ensure(rebuild=True)
        self.assertEqual(self.records.show(receipt['record_ids'][0],True)['text'],'사람이 결정한 최종 정책')
        self.assertEqual(self.records.add(self.task['id'],'decision','사람이 결정한 최종 정책'),receipt)

    def test_filters_and_archive_rules(self):
        self.assertEqual(self.records.search('로그인',statuses={'done'})['matches'],[])
        self.store.write({'version':1,'status':'archived'},self.task['id'])
        self.assertEqual(self.records.search('로그인')['matches'],[])
        self.assertEqual(len(self.records.search('로그인',include_archived=True)['matches']),1)
        with self.assertRaises(BoardError):self.records.add('MISSING','note','내용')
        with self.assertRaises(BoardError):self.records.search('')
