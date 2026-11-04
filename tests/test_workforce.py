import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce,validate_result,paths


def answer(action='complete',**changes):
    return dict(action=action,summary='실제 업무 결과',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None,**changes) if not changes else {**dict(action=action,summary='실제 업무 결과',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None),**changes}


class WorkforceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'db.sqlite3');self.work=Workforce(self.store,self.root)
        (self.root/'tests').mkdir();(self.root/'tests/test_demo.py').write_text('import unittest\nclass Demo(unittest.TestCase):\n def test_ok(self):self.assertEqual(1+1,2)\n')
        self.checks=[[sys.executable,'-m','unittest','discover','-s','tests']]
        self.goal=self.work.create('변형 체스 앱')
    def tearDown(self):self.tmp.cleanup()
    def run_team(self,fn):
        goal=self.work.goal(self.goal['id'])
        def run(goal,job,worker):return {'status':'succeeded','exit_code':0,'team_result':fn(job)}
        with patch.object(self.work,'employee_run',side_effect=run):self.work.run(goal['id'],goal['version'])
        return self.work.snapshot(goal['id'])
    def propose(self,job):
        return answer('propose',acceptance='테스트 통과',verification=self.checks,tasks=[{'title':'룰 구현','employee':'implement','instructions':'engine.py를 구현','files':['engine.py'],'depends_on':[]}])
    def approved(self):
        snap=self.run_team(self.propose);self.work.approve(self.goal['id'],snap['goal']['version'])

    def test_approval_questions_independent_review_and_user_completion(self):
        snap=self.run_team(lambda j:answer('ask',target='user',body='체크메이트 대신 왕 포획으로 할까요?'))
        job=snap['jobs'][0];self.assertEqual(snap['goal']['status'],'waiting_answer');self.assertEqual(job['status'],'waiting_user')
        self.work.reply(self.goal['id'],job['id'],{'version':job['version'],'body':'왕 포획으로 해주세요.'})
        snap=self.run_team(self.propose);self.assertEqual(snap['goal']['status'],'awaiting_approval')
        self.assertEqual(snap['jobs'][1]['status'],'awaiting_approval')
        self.work.approve(self.goal['id'],snap['goal']['version'])
        def collaborate(job):
            if job['kind']=='question':return answer(summary='엔진은 순수 함수로 구현하세요.')
            if job['kind']=='review':return answer('review',verdict='approved',summary='실제 파일과 검증 통과 확인')
            if not job['replies']:return answer('ask',target='plan',body='룰 엔진은 어떤 경계를 가지나요?')
            (Path(job['execution_workspace'])/'engine.py').write_text('def legal(): return True\n')
            return answer(summary='engine.py 구현 및 검증')
        snap=self.run_team(collaborate)
        self.assertEqual(snap['goal']['status'],'awaiting_acceptance')
        self.assertTrue(all(j['status']=='done' for j in snap['jobs']))
        self.assertTrue(any(j['kind']=='question' for j in snap['jobs']))
        self.assertTrue(any(j['kind']=='review' for j in snap['jobs']))
        self.assertNotEqual(self.store.detail(self.goal['task_id'])['task']['status'],'done')
        result=self.work.accept(self.goal['id'],snap['goal']['version'])
        self.assertEqual(result['goal']['status'],'done')
        self.assertEqual(self.store.detail(self.goal['task_id'])['task']['status'],'done')

    def test_manual_revision_requires_approval_and_autopilot_is_bounded(self):
        self.approved();reviews=0
        def result(job):
            nonlocal reviews
            if job['kind']=='review':reviews+=1;return answer('review',verdict='changes_requested',summary='예외 처리 보완')
            return answer()
        snap=self.run_team(result)
        self.assertEqual(reviews,1);self.assertEqual(snap['goal']['status'],'awaiting_approval')
        self.assertEqual(next(j for j in snap['jobs'] if j['kind']=='task')['status'],'awaiting_approval')
        settings=self.work.team.settings();settings['autopilot']=True;settings['max_rounds']=1;self.work.team.save(settings)
        self.work.approve(self.goal['id'],snap['goal']['version'])
        snap=self.run_team(result)
        self.assertEqual(snap['goal']['status'],'awaiting_approval');self.assertLessEqual(reviews,3)

    def test_current_role_configuration_invalidates_approval(self):
        self.approved();settings=self.work.team.settings();settings['roles']['implement']['instructions']='새 지침';self.work.team.save(settings)
        with patch.object(self.work,'employee_run',side_effect=AssertionError('must not launch')):
            goal=self.work.goal(self.goal['id']);self.work.run(goal['id'],goal['version'])
        self.assertEqual(self.work.goal(self.goal['id'])['status'],'awaiting_approval')

    def test_bad_scope_dependency_and_stale_version_do_not_create_work(self):
        with self.assertRaises(BoardError):paths(['../outside.py'],self.root)
        (self.root/'link').symlink_to(self.root.parent)
        with self.assertRaises(BoardError):paths(['link/outside.py'],self.root)
        with self.assertRaises(BoardError):paths(['*.py'],self.root)
        before=len(self.store.all());job=self.work.jobs(self.goal['id'])[0]
        goal=self.work.orch.update(self.goal['id'],{'worker':'owner','execution_settings':self.work.team.settings()},self.work.goal(self.goal['id'])['version'])
        bad=self.propose(job);bad['tasks'][0]['depends_on']=[0]
        with self.assertRaises(BoardError):self.work.apply(goal['id'],job['id'],bad,'owner')
        self.assertEqual(before,len(self.store.all()))
        with self.assertRaises(BoardError):self.work.apply(goal['id'],job['id'],answer('review',verdict='approved'),'owner')
        with self.assertRaises(BoardError):self.work.approve(goal['id'],1)

    def test_user_questions_can_be_queued_and_do_not_edit(self):
        goal=self.work.goal(self.goal['id'])
        snap=self.work.message(goal['id'],{'version':goal['version'],'kind':'question','employee':'implement','body':'현재 상태는?','client_id':'question-1'})
        question=next(j for j in snap['jobs'] if j['kind']=='question')
        self.assertEqual(question['files'],[])
        goal=self.work.goal(self.goal['id'])
        # Run only a question through the result handler; no dependency on a sequential plan slot.
        goal=self.work.orch.update(goal['id'],{'worker':'owner','execution_settings':self.work.team.settings()},goal['version'])
        with self.store.connect() as db:self.work.change(db,question['id'],{'status':'working'})
        self.work.apply(goal['id'],question['id'],answer(summary='읽기 전용 답변'),'owner')
        self.assertEqual(self.work.job(goal['id'],question['id'])['status'],'done')

    def test_staff_thread_rebinding_is_explicit_and_goal_scoped(self):
        self.approved();job=next(j for j in self.work.jobs(self.goal['id']) if j['kind']=='task')
        first=self.work.jobs(self.goal['id'])[0]
        self.work.orch.bind('employee-thread',first['id'])
        with self.assertRaises(BoardError):self.work.bind_thread(self.goal['id'],job['id'],'implement','employee-thread','employee-thread')
        self.work.orch.bind('owned-thread',job['id'])
        settings=self.work.team.settings()
        with self.store.connect() as db:
            next_job=self.work.insert(db,self.goal['id'],title='다음 업무',employee='implement',instructions='다음 구현',files=['next.py'])
        self.work.bind_thread(self.goal['id'],next_job,'implement','owned-thread','owned-thread')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',('owned-thread',)).fetchone()[0],next_job)

    def test_real_employee_json_process_collects_usage_and_duration(self):
        binary=self.root/'bin';binary.mkdir();fake=binary/'codex'
        payload=answer('ask',target='user',body='중요한 요구사항을 정해주세요.')
        fake.write_text('#!'+sys.executable+'\nimport json,sys\nsys.stdin.read()\nprint(json.dumps({"type":"thread.started","thread_id":"test-thread"}))\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'+repr(json.dumps(payload))+'}}))\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":60,"output_tokens":10}}))\n');fake.chmod(0o755)
        with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):
            self.work.run(self.goal['id'],self.work.goal(self.goal['id'])['version'])
        snap=self.work.snapshot(self.goal['id']);self.assertEqual(snap['goal']['status'],'waiting_answer')
        self.assertEqual(snap['runs'][0]['status'],'succeeded')
        from agent_work.usage import Usage
        usage=Usage(self.store).overview();self.assertEqual(usage['summary']['total_tokens'],110);self.assertEqual(usage['summary']['cached_input_tokens'],60);self.assertIsNotNone(usage['runs'][0]['duration_ms'])

    def test_recovery_requeues_only_after_confirmed_stop_and_explicit_retry(self):
        goal=self.work.orch.update(self.goal['id'],{'worker':'gone','status':'running','heartbeat':0},self.goal['version'])
        job=self.work.jobs(goal['id'])[0]
        with self.store.connect() as db:self.work.change(db,job['id'],{'status':'working'})
        with self.assertRaises(BoardError):self.work.orch.recover(goal['id'],goal['version'],False)
        self.work.orch.recover(goal['id'],goal['version'],True)
        current=self.work.job(goal['id'],job['id']);self.assertEqual(current['status'],'paused')
        self.work.retry(goal['id'],job['id'],{'version':current['version']})
        self.assertEqual(self.work.job(goal['id'],job['id'])['status'],'queued')

    def test_question_cannot_hide_in_completion_or_bypass_independent_review(self):
        with self.assertRaises(BoardError):validate_result(answer(target='user',body='중요 질문'))
        with self.assertRaises(BoardError):validate_result(answer(action=['complete']))
        with self.assertRaises(BoardError):self.work.accept(self.goal['id'],self.goal['version'])
        with self.assertRaises(BoardError):self.work.message(self.goal['id'],{'version':self.goal['version'],'kind':[],'employee':'plan','body':'invalid','client_id':'bad'})

    def test_api_project_harness_permission_and_legacy_preservation(self):
        import http.client
        import threading
        from agent_work.http import BoardServer
        folder=self.root/'.tack-orchestration';folder.mkdir();(folder/'install.json').write_text(json.dumps({'format':1,'workspace':str(self.root.resolve())}))
        project={'id':'test','name':'UI test','workspace':str(self.root),'db_path':str(self.store.path),'source_roots':[]}
        def request(server,path,body=None):
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET' if body is None else 'POST',path,body=json.dumps(body) if body is not None else None,headers={'Content-Type':'application/json'})
            response=client.getresponse();raw=response.read();client.close();return response.status,raw
        for execution in (False,True):
            server=BoardServer(('127.0.0.1',0),self.store,project,allow_execution=execution,harness=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                self.assertEqual(request(server,'/team')[0],200)
                status,raw=request(server,'/api/workforce/'+self.goal['id']);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['execution_enabled'],execution)
                status,_=request(server,'/api/workforce/'+self.goal['id']+'/accept',{'version':self.goal['version']})
                self.assertEqual(status,409 if execution else 403)
                if execution:
                    with patch('agent_work.orchestration_cli.start',return_value={'accepted':True}):
                        status,raw=request(server,'/api/workforce',{'title':'API team','acceptance':'','verification_text':''})
                    self.assertEqual(status,201);self.assertEqual(json.loads(raw)['mode'],'team')
                status,_=request(server,'/goals');self.assertEqual(status,200)
            finally:server.shutdown();server.server_close();thread.join()
        server=BoardServer(('127.0.0.1',0),self.store,project,harness=False);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:self.assertEqual(request(server,'/api/workforce')[0],404)
        finally:server.shutdown();server.server_close();thread.join()

    def test_employee_resume_binds_current_job_before_start_hooks(self):
        import hashlib
        self.approved();job=next(j for j in self.work.jobs(self.goal['id']) if j['kind']=='task')
        policy=self.work.team.settings();goal=self.work.orch.update(self.goal['id'],{'worker':'owner','execution_settings':policy},self.work.goal(self.goal['id'])['version'])
        with self.store.connect() as db:
            old=self.work.insert(db,goal['id'],title='이전 개발 업무',employee='implement',instructions='이전 업무',files=['old.py'])
            signature=hashlib.sha256(json.dumps([policy['roles']['implement'],[]],sort_keys=True).encode()).hexdigest()
            db.execute('INSERT INTO employee_sessions VALUES (?,?,?,?,?)',(goal['id'],'implement','write:'+job['id'],signature,'resume-thread'))
        self.work.orch.bind('resume-thread',old)
        def check(argv,*args):
            self.assertIn('resume',argv)
            with self.store.connect() as db:self.assertEqual(db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',('resume-thread',)).fetchone()[0],job['id'])
            return {'status':'succeeded','exit_code':0,'team_result':answer()}
        with patch.object(self.work.orch,'process',side_effect=check):self.work.employee_run(goal,job,'owner')

    def test_direct_question_keeps_user_and_employee_results_in_same_conversation(self):
        seed=self.work.jobs(self.goal['id'])[0]
        with self.store.connect() as db:self.work.change(db,seed['id'],{'status':'done','result':'fixture'})
        g=self.work.goal(self.goal['id'])
        snap=self.work.message(g['id'],{'version':g['version'],'kind':'question','employee':'implement','channel':'dm:implement','body':'설계를 설명해줘','client_id':'dm-1'})
        job=next(j for j in snap['jobs'] if j['kind']=='question');self.assertEqual(job['channel'],'dm:implement')
        snap=self.run_team(lambda j:answer(summary='실제 직원 답변'))
        messages=[m for m in snap['messages'] if m['data'].get('channel')=='dm:implement']
        self.assertEqual([m['sender'] for m in messages],['user','implement']);self.assertEqual(messages[-1]['body'],'실제 직원 답변')
        self.assertEqual(len([j for j in snap['jobs'] if j['kind']=='question']),1)
    def test_conversation_validation_mentions_and_idempotency(self):
        g=self.work.goal(self.goal['id']);payload={'version':g['version'],'kind':'question','employee':'','channel':'team','body':'@implement 현재 상태는?','client_id':'mention-1'}
        first=self.work.message(g['id'],payload);job=next(j for j in first['jobs'] if j['kind']=='question');self.assertEqual(job['employee'],'implement')
        self.work.message(g['id'],payload)
        self.assertEqual(len([j for j in self.work.jobs(g['id']) if j['kind']=='question']),1)
        with self.assertRaises(BoardError):self.work.message(g['id'],{**payload,'body':'different'})
        for bad in ({'channel':'dm:unknown'},{'channel':'dm:verify','employee':'implement'},{'body':'@unknown hello'},{'channel':[]}):
            with self.assertRaises(BoardError):self.work.message(g['id'],{**payload,**bad,'client_id':'invalid'})
    def test_private_user_decision_and_reply_remain_in_original_channel(self):
        g=self.work.goal(self.goal['id']);self.work.message(g['id'],{'version':g['version'],'kind':'question','employee':'implement','channel':'dm:implement','body':'입력 정책은?','client_id':'dm-decision'})
        def ask(job):return answer('ask',target='user',body='음수를 허용할까요?')
        snap=self.run_team(ask);job=next(j for j in snap['jobs'] if j['kind']=='question')
        question=next(m for m in snap['messages'] if m['kind']=='question' and m['sender']=='implement')
        self.assertEqual(question['data']['channel'],'dm:implement')
        snap=self.work.reply(g['id'],job['id'],{'version':job['version'],'body':'네'})
        reply=next(m for m in snap['messages'] if m['kind']=='answer' and m['sender']=='user')
        self.assertEqual(reply['data']['channel'],'dm:implement')

    def test_followup_reuses_only_same_goal_employee_and_conversation(self):
        g=self.work.goal(self.goal['id'])
        def ask(channel,client):
            g=self.work.goal(self.goal['id'])
            snap=self.work.message(g['id'],{'version':g['version'],'kind':'question','employee':'implement','channel':channel,'body':'설계 질문 '+client,'client_id':client})
            return next(j for j in snap['jobs'] if j['instructions']=='설계 질문 '+client)
        first=ask('dm:implement','first');policy=self.work.team.settings()
        self.work.orch.update(g['id'],{'worker':'owner','execution_settings':policy},self.work.goal(g['id'])['version'])
        def initial(argv,cwd,key,worker,run_id,prompt):
            self.assertNotIn('resume',argv);self.work.orch.bind('dm-thread',first['id']);self.work.orch.run_update(run_id,thread_id='dm-thread')
            return {'status':'succeeded','exit_code':0,'team_result':answer()}
        with patch.object(self.work.orch,'process',side_effect=initial):self.work.employee_run(self.work.goal(g['id']),first,'owner')
        second=ask('dm:implement','second')
        def resumed(argv,*args):
            self.assertIn('resume',argv);self.assertIn('dm-thread',argv)
            with self.store.connect() as db:self.assertEqual(db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',('dm-thread',)).fetchone()[0],second['id'])
            return {'status':'succeeded','exit_code':0,'team_result':answer()}
        with patch.object(self.work.orch,'process',side_effect=resumed):self.work.employee_run(self.work.goal(g['id']),second,'owner')
        public=ask('team','public')
        def fresh(argv,*args):
            self.assertNotIn('resume',argv)
            return {'status':'succeeded','exit_code':0,'team_result':answer()}
        with patch.object(self.work.orch,'process',side_effect=fresh):self.work.employee_run(self.work.goal(g['id']),public,'owner')
