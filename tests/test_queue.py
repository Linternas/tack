import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_work.store import Store
from agent_work.workforce import Workforce
from agent_work import run_queue

class QueueTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'db.sqlite3');self.work=Workforce(self.store,self.root)
  self.project={'db_path':str(self.store.path),'workspace':str(self.root)}
  self.a=self.work.create('first');self.b=self.work.create('second')
 def tearDown(self):self.temp.cleanup()
 def enqueue(self,g):
  with patch.object(run_queue,'wake',return_value=123):return run_queue.enqueue(self.project,g['id'],self.work.goal(g['id'])['version'])
 def stop_for_answer(self,key,version):
  job=self.work.jobs(key)[0]
  with self.store.connect() as db:
   self.work.change(db,job['id'],{'status':'waiting_user','question':'need decision'})
   self.work.set_goal(db,key,status='waiting_answer',worker='')
 def test_fifo_survives_new_objects_and_waiting_goal_releases_slot(self):
  self.enqueue(self.a);self.enqueue(self.b)
  self.assertEqual(run_queue.overview(self.store,self.b['id'])['position'],2)
  order=[]
  def run(_self,key,version):order.append(key);self.stop_for_answer(key,version)
  with patch.object(Workforce,'run',run):self.assertTrue(run_queue.drain(Store(self.store.path),self.root)['drained'])
  self.assertEqual(order,[self.a['id'],self.b['id']]);self.assertEqual(self.work.goal(self.a['id'])['status'],'waiting_answer')
 def test_cancelled_request_never_launches(self):
  self.enqueue(self.a);run_queue.cancel(self.work,self.a['id'],self.work.goal(self.a['id'])['version'])
  with patch.object(Workforce,'run',side_effect=AssertionError('cancelled')):run_queue.drain(self.store,self.root)
  self.assertEqual(run_queue.overview(self.store,self.a['id'])['status'],'cancelled')
 def test_stale_version_requires_new_request(self):
  self.enqueue(self.a);g=self.work.goal(self.a['id']);self.work.orch.update(g['id'],{'acceptance':'changed'},g['version'])
  with patch.object(Workforce,'run',side_effect=AssertionError('stale')):run_queue.drain(self.store,self.root)
  self.assertEqual(run_queue.overview(self.store,self.a['id'])['status'],'needs_recheck')
 def test_dead_coordinator_never_automatically_replays(self):
  self.enqueue(self.a);self.enqueue(self.b);g=self.work.goal(self.a['id']);self.work.orch.update(g['id'],{'worker':'old','heartbeat':0},g['version'])
  with patch.object(Workforce,'run',side_effect=AssertionError('unsafe replay')):self.assertTrue(run_queue.drain(self.store,self.root)['waiting_recovery'])
  self.assertEqual(run_queue.overview(self.store,self.b['id'])['status'],'queued')
