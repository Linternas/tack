import copy,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from agent_work.store import Store,BoardError
from agent_work.workforce import Workforce
from agent_work.organization import Organization
from agent_work.demo_day import DemoDay
from agent_work import run_queue
from agent_work.execution_policy import job_reason

class OrganizationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'state.sqlite3');self.work=Workforce(self.store,self.root);self.org=Organization(self.store,self.root)
 def tearDown(self):self.tmp.cleanup()
 def team(self,name='체스팀',paths=None,staffing='new',members=None,kind='product',support=None):
  old=self.org.settings();p=dict(version=old['version'],name=name,kind=kind,members=members or [],paths=paths if paths is not None else ['apps/chess'],support_teams=support or [],max_parallel=2,max_tokens=100000,staffing=staffing)
  result=self.org.save(p);return next(g for k,g in result['teams'].items() if k not in old['teams'])
 def ready(self,group=None):
  g=self.work.create('검증된 계산기',team_id=group['id'] if group else None)
  with self.store.connect() as db:
   job=self.work.jobs(g['id'])[0];self.work.change(db,job['id'],{'status':'done','result':'CLI 구현 완료 http://localhost:3000','files':[]})
   self.work.set_goal(db,g['id'],status='awaiting_acceptance',verification_results=[{'argv':[sys.executable,'-m','unittest'],'status':'succeeded','exit_code':0}])
  return self.work.goal(g['id'])
 def test_read_only_default_does_not_create_tables_and_new_teams_have_distinct_staff(self):
  self.assertEqual(list(self.org.settings()['teams']),['default']);self.assertFalse(self.org.exists())
  a=self.team();b=self.team('쇼핑팀',['apps/shop'])
  self.assertFalse(set(a['members'])&set(b['members']));roles=self.work.team.settings()['roles']
  self.assertEqual({roles[m]['kind'] for m in a['members']},{'plan','implement','verify'})
  self.assertEqual(self.org.settings()['teams']['default']['members'],['plan','implement','verify'])
 def test_scope_and_live_changes_never_widen_existing_goal(self):
  a=self.team();g=self.work.create('체스',team_id=a['id']);role=a['members'][1]
  self.assertIn('팀',job_reason(self.work.goal(g['id']),{'employee':role,'files':['apps/shop/main.py']},self.work.team.settings(),self.root))
  with self.store.connect() as db:
   with self.assertRaises(BoardError):self.work.insert(db,g['id'],title='침범',employee='implement',instructions='wrong',files=['apps/chess/a.py'])
  p={**a,'version':self.org.settings()['version'],'staffing':'existing'};p.pop('id');p['paths']=['apps/chess/sub'];self.org.save(p,a['id'])
  self.assertTrue(job_reason(self.work.goal(g['id']),{'employee':role,'files':['apps/chess/main.py']},self.work.team.settings(),self.root))
 def test_functional_support_and_ownership_conflicts(self):
  qa=self.team('QA',[],staffing='existing',members=['verify'],kind='functional');a=self.team(support=[qa['id']])
  self.assertIn('verify',self.org.scope(a['id'])['members'])
  with self.assertRaises(BoardError):self.team('겹침',['apps/chess/lib'])
  with self.assertRaises(BoardError):self.team('경로침범',['../outside'])
 def test_scoped_run_only_uses_its_own_planner(self):
  a=self.team();g=self.work.create('앱',team_id=a['id']);seen=[]
  def execute(goal,job,owner):
   seen.append((job['employee'],set(goal['execution_settings']['roles'])));return {'status':'succeeded','exit_code':0,'team_result':dict(action='ask',summary='결정 요청',target='user',body='질문',tasks=[],acceptance='',verification=[],verdict=None)}
  with patch.object(self.work,'employee_run',side_effect=execute):self.work.run(g['id'],g['version'])
  self.assertEqual(seen,[(a['members'][0],set(a['members']))])
 def test_team_queue_runs_disjoint_teams_in_parallel(self):
  a=self.team();b=self.team('쇼핑',['apps/shop']);ga=self.work.create('a',team_id=a['id']);gb=self.work.create('b',team_id=b['id']);project={'workspace':str(self.root),'db_path':str(self.store.path)}
  with patch('agent_work.run_queue.wake',return_value=0):
   for g in (ga,gb):run_queue.enqueue(project,g['id'],g['version'])
  barrier=threading.Barrier(2);entered=[]
  def execute(w,key,version):
   barrier.wait(5);entered.append(key)
   with w.store.connect() as db:
    w.change(db,w.jobs(key)[0]['id'],{'status':'waiting_user','question':'대기'})
    w.set_goal(db,key,status='waiting_answer',worker='')
  with patch.object(Workforce,'run',execute):self.assertTrue(run_queue.drain(self.store,self.root)['drained'])
  self.assertEqual(set(entered),{ga['id'],gb['id']})
 def test_two_apps_execute_verify_and_present_without_crossing_paths(self):
  a=self.team();b=self.team('쇼핑',['apps/shop']);settings=self.work.team.settings();settings.update(execution_mode='autopilot',autopilot=True);self.work.team.save(settings)
  for group in (a,b):
   folder=self.root/group['paths'][0];folder.mkdir(parents=True)
   (folder/'test_main.py').write_text("import unittest,runpy\nclass Test(unittest.TestCase):\n def test_value(self):self.assertEqual(runpy.run_path(__file__.replace('test_main.py','main.py'))['value'],42)\n")
  goals=[self.work.create(g['name']+' 앱',team_id=g['id']) for g in (a,b)];project={'workspace':str(self.root),'db_path':str(self.store.path)}
  with patch('agent_work.run_queue.wake',return_value=0):
   for goal in goals:run_queue.enqueue(project,goal['id'],goal['version'])
  def execute(w,goal,job,worker):
   result=dict(action='complete',summary='구현·검증 결과',target=None,body='',tasks=[],acceptance='',verification=[],verdict=None)
   if job['kind']=='plan':
    developer=next(k for k,v in goal['execution_settings']['roles'].items() if v['kind']=='implement');base=goal['team_snapshot']['paths'][0]
    result.update(action='propose',acceptance='42 반환 및 테스트 통과',verification=[[sys.executable,'-m','unittest','discover','-s',base]],tasks=[dict(title='앱 구현',employee=developer,instructions='value=42 구현',files=[base+'/main.py'],depends_on=[])])
   elif job['kind']=='task':(Path(job['execution_workspace'])/job['files'][0]).write_text('value=42\n')
   elif job['kind']=='review':result.update(action='review',verdict='approved')
   return {'status':'succeeded','exit_code':0,'team_result':result}
  with patch.object(Workforce,'employee_run',execute):run_queue.drain(self.store,self.root)
  for goal,group in zip(goals,(a,b)):
   self.assertEqual(self.work.goal(goal['id'])['status'],'awaiting_acceptance',self.work.goal(goal['id']).get('error'))
   self.assertEqual((self.root/group['paths'][0]/'main.py').read_text(),'value=42\n')
  show=DemoDay(self.work).create({'title':'두 팀 데모','team_id':None});self.assertEqual(len(show['entries']),2)
 def test_demo_publish_and_accept_preserve_frozen_evidence(self):
  a=self.team();g=self.ready(a);demos=DemoDay(self.work);show=demos.create({'title':'금요일 데모','team_id':None})
  self.assertEqual(show['entries'][0]['team_name'],'체스팀')
  with self.assertRaises(BoardError):demos.create({'title':'중복','team_id':None})
  accepted=demos.respond(show['id'],{'version':show['version'],'goal_id':g['id'],'decision':'accept','feedback':'좋아요'})
  self.assertEqual(self.work.goal(g['id'])['status'],'done');self.assertEqual(accepted['entries'][0]['decision'],'accepted')
  self.assertEqual(accepted['entries'][0]['reports'],show['entries'][0]['reports'])
 def test_demo_revisions_create_scoped_followup_once(self):
  a=self.team();g=self.ready(a);demos=DemoDay(self.work);show=demos.create({'title':'데모','team_id':None});request={'version':1,'goal_id':g['id'],'decision':'revise','feedback':'음수 입력도 검증해주세요.'}
  result=demos.respond(show['id'],request);follow=self.work.goal(result['entries'][0]['followup_goal'])
  self.assertEqual(follow['team_id'],a['id']);self.assertEqual(follow['team_snapshot'],g['team_snapshot']);self.assertIn('음수 입력',self.work.jobs(follow['id'])[0]['instructions'])
  with self.assertRaises(BoardError):demos.respond(show['id'],request)
 def test_changed_or_unverified_evidence_cannot_be_accepted(self):
  g=self.ready();demos=DemoDay(self.work);show=demos.create({'title':'데모','team_id':None})
  with self.store.connect() as db:self.work.change(db,self.work.jobs(g['id'])[0]['id'],{'result':'변경된 산출물'})
  with self.assertRaises(BoardError):demos.respond(show['id'],{'version':1,'goal_id':g['id'],'decision':'accept','feedback':''})
  self.assertNotEqual(self.work.goal(g['id'])['status'],'done')

 def test_demo_missing_id_is_a_domain_error_before_first_presentation(self):
  with self.assertRaises(BoardError) as raised:
   DemoDay(self.work).respond('missing',dict(version=1,goal_id='missing',decision='hold',feedback=''))
  self.assertEqual(raised.exception.status,404)

 def test_demo_revision_preserves_verification_and_original_grants(self):
  group=self.team()
  goal=self.ready(group)
  verification=[[sys.executable,'-m','unittest','discover','-s','apps/chess']]
  with self.store.connect() as db:
   self.work.set_goal(db,goal['id'],verification=verification,supplied_verification=verification)
  goal=self.work.goal(goal['id'])
  demos=DemoDay(self.work)
  show=demos.create(dict(title='검증 기준 보존',team_id=None))
  changed=demos.respond(show['id'],dict(version=1,goal_id=goal['id'],decision='revise',feedback='예외 처리 보강'))
  follow=self.work.goal(changed['entries'][0]['followup_goal'])
  self.assertEqual(follow['verification'],verification)
  self.assertEqual(follow['supplied_verification'],verification)
  self.assertEqual(follow['execution_policy'],goal['execution_policy'])

 def test_demo_policy_change_invalidates_published_evidence(self):
  goal=self.ready(self.team())
  demos=DemoDay(self.work)
  show=demos.create(dict(title='권한 변경',team_id=None))
  with self.store.connect() as db:
   self.work.set_goal(db,goal['id'],execution_policy={'execution_mode':'manual','permissions':{}})
  with self.assertRaises(BoardError):
   demos.respond(show['id'],dict(version=1,goal_id=goal['id'],decision='accept',feedback=''))

 def test_demo_hold_then_accept_and_failed_revision_are_atomic(self):
  goal=self.ready(self.team())
  demos=DemoDay(self.work)
  show=demos.create(dict(title='원자적 응답',team_id=None))
  held=demos.respond(show['id'],dict(version=1,goal_id=goal['id'],decision='hold',feedback='확인 중'))
  self.assertEqual(held['entries'][0]['decision'],'held')
  with self.store.connect() as db:
   before=db.execute('SELECT COUNT(*) FROM goals').fetchone()[0]
  with patch.object(self.work.team,'insert',side_effect=RuntimeError('메시지 저장 실패')):
   with self.assertRaises(RuntimeError):
    demos.respond(show['id'],dict(version=2,goal_id=goal['id'],decision='revise',feedback='수정 요청'))
  with self.store.connect() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM goals').fetchone()[0],before)
  self.assertEqual(demos.show(show['id'])['version'],2)
  with patch.object(self.work.team,'insert',side_effect=RuntimeError('수락 메시지 저장 실패')):
   with self.assertRaises(RuntimeError):
    demos.respond(show['id'],dict(version=2,goal_id=goal['id'],decision='accept',feedback='확인'))
  self.assertEqual(self.work.goal(goal['id'])['status'],'awaiting_acceptance')
  self.assertEqual(demos.show(show['id'])['version'],2)
  accepted=demos.respond(show['id'],dict(version=2,goal_id=goal['id'],decision='accept',feedback='확인'))
  self.assertEqual(accepted['entries'][0]['decision'],'accepted')

 def test_team_queue_preserves_fifo_and_drains_requeued_rounds(self):
  group=self.team()
  goals=[self.work.create(title,team_id=group['id']) for title in ('first','second')]
  project=dict(workspace=str(self.root),db_path=str(self.store.path))
  with patch('agent_work.run_queue.wake',return_value=0):
   for goal in goals:run_queue.enqueue(project,goal['id'],goal['version'])
  entered=[]
  def execute(work,key,version):
   entered.append(key)
   with work.store.connect() as db:
    if entered.count(key)==1 and key==goals[0]['id']:
     work.set_goal(db,key,status='ready',worker='')
    else:
     work.change(db,work.jobs(key)[0]['id'],dict(status='waiting_user',question='대기'))
     work.set_goal(db,key,status='waiting_answer',worker='')
  with patch.object(Workforce,'run',execute):
   self.assertTrue(run_queue.drain(self.store,self.root)['drained'])
  self.assertEqual(entered,[goals[0]['id'],goals[1]['id'],goals[0]['id']])

 def test_team_queue_stale_request_can_be_explicitly_retried(self):
  goal=self.work.create('대기 변경',team_id=self.team()['id'])
  project=dict(workspace=str(self.root),db_path=str(self.store.path))
  with patch('agent_work.run_queue.wake',return_value=0):
   run_queue.enqueue(project,goal['id'],goal['version'])
  with self.store.connect() as db:self.work.set_goal(db,goal['id'],error='대기 중 변경')
  self.assertTrue(run_queue.drain(self.store,self.root)['drained'])
  self.assertEqual(run_queue.overview(self.store,goal['id'])['status'],'needs_recheck')
  with patch('agent_work.run_queue.wake',return_value=0):
   run_queue.enqueue(project,goal['id'],self.work.goal(goal['id'])['version'])
  self.assertEqual(run_queue.overview(self.store,goal['id'])['status'],'queued')

 def test_team_queue_cancellation_between_selection_and_claim_does_not_launch(self):
  goal=self.work.create('취소 경쟁',team_id=self.team()['id'])
  project=dict(workspace=str(self.root),db_path=str(self.store.path))
  with patch('agent_work.run_queue.wake',return_value=0):
   run_queue.enqueue(project,goal['id'],goal['version'])
  original=Workforce.goal
  cancelled=False
  def read_and_cancel(work,key):
   nonlocal cancelled
   current=original(work,key)
   if key==goal['id'] and not cancelled:
    cancelled=True
    run_queue.cancel(work,key,current['version'])
   return current
  with patch.object(Workforce,'goal',read_and_cancel),patch.object(Workforce,'run') as execute:
   self.assertTrue(run_queue.drain(self.store,self.root)['drained'])
  execute.assert_not_called()
  self.assertEqual(run_queue.overview(self.store,goal['id'])['status'],'cancelled')

 def test_shared_support_employee_has_one_active_lease_across_teams(self):
  from agent_work.collaboration import Collaboration
  support=self.team('공통 QA',[],staffing='existing',members=['verify'],kind='functional')
  groups=[self.team(support=[support['id']]),self.team('쇼핑',['apps/shop'],support=[support['id']])]
  goals=[self.work.create(group['name'],team_id=group['id']) for group in groups]
  jobs=[]
  with self.store.connect() as db:
   for goal in goals:
    self.work.set_goal(db,goal['id'],worker='owner-'+goal['id'],heartbeat=time.time())
    jobs.append(self.work.insert(db,goal['id'],title='독립 QA',employee='verify',instructions='확인',kind='question',status='queued'))
  collaboration=Collaboration(self.work)
  first=collaboration.claim(goals[0]['id'],jobs[0],'owner-'+goals[0]['id'])
  self.assertEqual(first['status'],'working')
  self.assertIsNone(collaboration.claim(goals[1]['id'],jobs[1],'owner-'+goals[1]['id']))
  with self.store.connect() as db:self.work.change(db,jobs[0],dict(status='done',result='검증 완료'))
  self.assertEqual(collaboration.claim(goals[1]['id'],jobs[1],'owner-'+goals[1]['id'])['status'],'working')

 def test_project_slot_limit_applies_across_distinct_teams_and_employees(self):
  from agent_work.collaboration import Collaboration
  settings=self.work.team.settings()
  settings['max_parallel']=1
  self.work.team.save(settings)
  groups=[self.team(),self.team('쇼핑',['apps/shop'])]
  goals=[self.work.create(group['name'],team_id=group['id']) for group in groups]
  with self.store.connect() as db:
   for goal in goals:self.work.set_goal(db,goal['id'],worker='owner-'+goal['id'],heartbeat=time.time())
  collaboration=Collaboration(self.work)
  first_job=self.work.jobs(goals[0]['id'])[0]
  second_job=self.work.jobs(goals[1]['id'])[0]
  collaboration.claim(goals[0]['id'],first_job['id'],'owner-'+goals[0]['id'])
  self.assertIsNone(collaboration.claim(goals[1]['id'],second_job['id'],'owner-'+goals[1]['id']))

 def test_team_budget_is_cumulative_and_live_edits_never_expand_old_grant(self):
  from agent_work.collaboration import Collaboration
  group=self.team()
  first=self.work.create('첫 목표',team_id=group['id'])
  second=self.work.create('다음 목표',team_id=group['id'])
  collaboration=Collaboration(self.work)
  collaboration.ensure()
  with self.store.connect() as db:self.work.set_goal(db,first['id'],token_used=60000)
  payload={**group,'version':self.org.settings()['version'],'staffing':'existing','max_tokens':50000}
  payload.pop('id')
  self.org.save(payload,group['id'])
  self.assertEqual(collaboration.budget(second['id']),'team_token_limit')
  payload.update(version=self.org.settings()['version'],max_tokens=200000)
  self.org.save(payload,group['id'])
  self.assertEqual(collaboration.budget(second['id']),'')
  with self.store.connect() as db:self.work.set_goal(db,first['id'],token_used=100000)
  self.assertEqual(self.org.total_tokens(group['id']),100000)
  self.assertEqual(collaboration.budget(second['id']),'team_token_limit')
  with patch.object(self.work,'employee_run') as execute:
   with self.assertRaises(BoardError):self.work.run(second['id'],self.work.goal(second['id'])['version'])
  execute.assert_not_called()

 def test_scope_reads_one_organization_snapshot_during_support_changes(self):
  before=self.org.settings()
  after=copy.deepcopy(before)
  after['version']+=1
  after['teams']['default']['support_teams']=['new-support']
  after['teams']['new-support']={**after['teams']['default'],'id':'new-support','members':['verify'],'support_teams':[]}
  with patch.object(self.org,'settings',side_effect=[before,after]) as settings:
   scope=self.org.scope('default')
  settings.assert_called_once_with()
  self.assertEqual(scope,before['teams']['default'])
  for invalid in ([], 'missing'):
   with self.assertRaises(BoardError):self.org.scope(invalid)

 def test_team_autopilot_assigns_member_work_requests_without_explicit_delegation(self):
  from agent_work.collaboration import Collaboration
  group=self.team()
  settings=self.work.team.settings()
  settings.update(execution_mode='autopilot',autopilot=True)
  self.work.team.save(settings)
  goal=self.work.create('사칙연산 계산기',team_id=group['id'])
  collaboration=Collaboration(self.work)
  collaboration.ensure()
  planner=self.work.jobs(goal['id'])[0]
  self.work.orch.update(goal['id'],dict(worker='owner',status='running',execution_settings=self.work.scoped_settings(goal)),goal['version'])
  claimed=collaboration.claim(goal['id'],planner['id'],'owner')
  payload=dict(
   to_role=group['members'][1],title='계산기 구현',instructions='기존 범위 안에서 사칙연산 구현',
   acceptance=['계산기 테스트 통과'],files=['apps/chess/calculator.py'],requirements=[goal['title']],
   change_type='additive',contract_path=None,expected_revision=None,wait_for='implementation_verified',
   verification=[[sys.executable,'-m','unittest']],idempotency_key='team-calculator-v1',
  )
  request=collaboration.request(goal['id'],planner['id'],payload,claimed['attempt_id'],'owner')
  self.assertEqual(request['status'],'accepted')
  self.assertEqual(request['reason'],'')
  child=self.work.job(goal['id'],request['job_id'])
  self.assertEqual(child['employee'],group['members'][1])
  self.assertEqual(child['status'],'queued')
  self.assertTrue(child['automatic_approval'])
  self.assertEqual(child['verification'],payload['verification'])
  self.assertEqual(self.work.job(goal['id'],planner['id'])['status'],'waiting_conditions')
  with self.store.connect() as db:self.assertIsNone(collaboration.policy(db,goal['id']))
  with self.assertRaises(BoardError):
   collaboration.request(goal['id'],planner['id'],{**payload,'to_role':'implement','idempotency_key':'outsider'},claimed['attempt_id'],'owner')
