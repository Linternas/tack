import json
from pathlib import Path
import tempfile
import unittest
from agent_work.store import Store, BoardError
from agent_work.records import Records


class TaskRelationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'work.sqlite3'
        self.store = Store(self.path)
        self.parent = self.store.write({'title':'협업자 관리 개선','category':'task'})['task']
        self.other = self.store.write({'title':'API 계약','category':'api'})['task']

    def tearDown(self):
        self.temp.cleanup()

    def link(self, source, target, kind, remove=False):
        current = self.store.detail(source)['task']
        return self.store.link(source,target,kind,current['version'],remove)

    def test_parent_child_completion_and_legacy_data(self):
        child = self.store.write({'title':'FE 구현','parent_id':self.parent['id']})['task']
        links = self.store.relationships(self.parent['id'])
        self.assertEqual(links['items'][0]['type'],'child')
        with self.assertRaises(BoardError):
            self.store.write({'version':1,'status':'done','resolution':'상위 조건 검증'},self.parent['id'])
        self.store.write({'version':1,'status':'done','resolution':'FE 검증 통과'},child['id'])
        self.assertEqual(self.store.detail(self.parent['id'])['task']['status'],'todo')
        self.store.write({'version':1,'status':'done','resolution':'상위 조건 검증'},self.parent['id'])
        with self.assertRaises(BoardError):self.store.write({'version':2,'status':'todo'},child['id'])
        with self.store.connect() as db:
            row=db.execute('SELECT data FROM tasks WHERE id=?',(self.other['id'],)).fetchone()
            data=json.loads(row[0]);data.pop('relations');data.pop('parent_id')
            db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data),self.other['id']))
        old=Store(self.path,readonly=True).detail(self.other['id'])['task']
        self.assertEqual(old['parent_id'],'');self.assertEqual(old['relations'],[])

    def test_cycle_self_reference_missing_target_and_closed_parent_are_rejected(self):
        child=self.store.write({'title':'하위 작업','parent_id':self.parent['id']})['task']
        for source,target,kind in [(self.parent['id'],child['id'],'parent'),(child['id'],self.parent['id'],'blocked_by'),(child['id'],child['id'],'relates_to'),(child['id'],'LOCAL-9999','duplicate_of')]:
            with self.assertRaises(BoardError):self.link(source,target,kind)
        self.link(self.parent['id'],self.other['id'],'blocked_by')
        with self.assertRaises(BoardError):self.link(self.other['id'],self.parent['id'],'blocked_by')
        done=self.store.write({'title':'종료','status':'done','resolution':'검증'})['task']
        with self.assertRaises(BoardError):self.store.write({'title':'새 하위','parent_id':done['id']})

    def test_links_are_bidirectional_visible_versioned_and_do_not_change_status(self):
        for kind,reverse in [('duplicate_of','duplicated_by'),('superseded_by','supersedes'),('relates_to','relates_to')]:
            result=self.link(self.parent['id'],self.other['id'],kind)
            self.assertEqual(result['task']['status'],'todo')
            self.assertTrue(any(item['type']==reverse for item in self.store.relationships(self.other['id'])['items']))
            with self.assertRaises(BoardError):self.store.link(self.parent['id'],self.other['id'],kind,1)
            self.link(self.parent['id'],self.other['id'],kind,remove=True)
            self.assertFalse(any(item['type']==reverse for item in self.store.relationships(self.other['id'])['items']))
        self.assertEqual(len(self.store.detail(self.parent['id'])['history']),7)

    def test_replacement_cycles_and_duplicate_related_edges_are_rejected_atomically(self):
        self.link(self.parent['id'],self.other['id'],'superseded_by')
        before=self.store.detail(self.other['id'])
        with self.assertRaises(BoardError):self.link(self.other['id'],self.parent['id'],'duplicate_of')
        self.assertEqual(self.store.detail(self.other['id']),before)
        self.link(self.parent['id'],self.other['id'],'relates_to')
        with self.assertRaises(BoardError):self.link(self.other['id'],self.parent['id'],'relates_to')
        with self.assertRaises(BoardError):self.store.write({'title':'오류','relations':[{'type':'unknown','task_id':self.parent['id']}]})

    def test_relation_preview_is_bounded_and_full_detail_preserves_all_children(self):
        for i in range(8):self.store.write({'title':f'작업 {i}','parent_id':self.parent['id']})
        short=Records(self.store).overview(self.parent['id'])['relationships']
        self.assertEqual(len(short['items']),5);self.assertTrue(short['has_more'])
        self.assertEqual(short['counts']['child'],8)
        self.assertEqual(len(self.store.relationships(self.parent['id'],limit=5,offset=5)['items']),3)
        self.assertEqual(len(self.store.detail(self.parent['id'])['relationships']['items']),8)

    def test_concurrent_parent_completion_and_child_creation_cannot_leave_an_open_child_under_done(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        barrier=threading.Barrier(2)
        def write(payload,key=None):
            barrier.wait()
            try:return self.store.write(payload,key)['task']['id']
            except BoardError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            done=pool.submit(write,{'version':1,'status':'done','resolution':'검증'},self.parent['id'])
            child=pool.submit(write,{'title':'동시 하위','parent_id':self.parent['id']})
            outcomes=[done.result(),child.result()]
        self.assertEqual(sum(result is not None for result in outcomes),1)
        parent=self.store.detail(self.parent['id'])['task']
        children=[task for task in self.store.all() if task['parent_id']==parent['id']]
        self.assertFalse(parent['status']=='done' and any(task['status']=='todo' for task in children))


    def test_seed_loading_supports_forward_parent_references_and_rejects_invalid_relations(self):
        seed=Path(self.temp.name)/'seed.json'
        seed.write_text(json.dumps([{'id':'CHILD-001','title':'하위','parent_id':'PARENT-001'},{'id':'PARENT-001','title':'상위'}]))
        store=Store(Path(self.temp.name)/'seed.sqlite3',seed)
        self.assertEqual(store.relationships('PARENT-001')['counts']['child'],1)
        seed.write_text(json.dumps([{'id':'BAD-001','title':'오류','relations':None}]))
        with self.assertRaises(BoardError):Store(Path(self.temp.name)/'bad.sqlite3',seed)
        seed.write_text(json.dumps([{'id':'CHILD-001','title':'하위','parent_id':'MISSING-001'}]))
        with self.assertRaises(BoardError):Store(Path(self.temp.name)/'missing.sqlite3',seed)
