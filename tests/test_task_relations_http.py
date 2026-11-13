import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from agent_work.http import BoardServer
from agent_work.store import Store


class TaskRelationsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'work.sqlite3')
        self.parent=self.store.write({'title':'상위 작업'})['task']
        self.target=self.store.write({'title':'통합 작업'})['task']
        project={'id':'test','name':'테스트','workspace':str(self.root),'db_path':str(self.store.path)}
        self.server=BoardServer(('127.0.0.1',0),self.store,project)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,method='GET',data=None):
        req=Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
        try:
            with urlopen(req) as response:return response.status,json.load(response)
        except HTTPError as response:return response.code,json.load(response)

    def test_core_http_creates_subtasks_links_and_inverse_views_with_conflict_protection(self):
        status,result=self.request('/api/tasks','POST',{'title':'FE 구현','parent_id':self.parent['id']})
        self.assertEqual(status,201);child=result['task']
        path='/api/tasks/'+child['id']+'/relations'
        status,result=self.request(path,'POST',{'type':'superseded_by','target':self.target['id'],'version':1})
        self.assertEqual(status,200);self.assertEqual(result['task']['version'],2)
        _,reverse=self.request('/api/tasks/'+self.target['id']+'/relations')
        self.assertEqual(reverse['items'][0]['type'],'supersedes')
        status,_=self.request(path,'POST',{'type':'superseded_by','target':self.target['id'],'version':1,'remove':True})
        self.assertEqual(status,409)
        status,_=self.request('/api/tasks/'+self.parent['id'],'PATCH',{'version':1,'status':'done','resolution':'검증'})
        self.assertEqual(status,400)
        _,parent=self.request('/api/tasks/'+self.parent['id'])
        self.assertEqual(parent['relationships']['counts']['child'],1)
        self.assertEqual(parent['task']['status'],'todo')
        status,_=self.request(path,'POST',{'type':'superseded_by','target':self.target['id'],'version':2,'remove':'yes'})
        self.assertEqual(status,400)
        status,_=self.request(path,'POST',{'type':'superseded_by','target':self.target['id'],'version':2,'remove':True})
        self.assertEqual(status,200)
        _,child=self.request('/api/tasks/'+child['id'])
        self.assertEqual(child['task']['relations'],[])
