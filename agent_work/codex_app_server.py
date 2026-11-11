"""Owned stdio Codex connection. One worker may reuse it across scoped employee jobs."""
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from .store import BoardError
from .usage import Usage

class AppServer:
    def __init__(self,cwd,codex_home=None):
        env=dict(os.environ)
        if codex_home:env["CODEX_HOME"]=str(codex_home)
        for name in ('GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE'):env.pop(name,None)
        self.proc=subprocess.Popen(['codex','-c','sandbox_workspace_write.writable_roots=[]','app-server','--listen','stdio://'],cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,env={**env,'GIT_CEILING_DIRECTORIES':str(Path(cwd).resolve().parent)})
        self.messages=queue.Queue(maxsize=1000);self.closed=threading.Event();self.sequence=0;self.initialized=False;self.finished_turns=set()
        def read():
            try:
                while not self.closed.is_set():
                    raw=self.proc.stdout.readline(2000001)
                    if not raw:break
                    if len(raw)>2000000:break
                    try:value=json.loads(raw)
                    except (ValueError,UnicodeError):continue
                    if not isinstance(value,dict):continue
                    while not self.closed.is_set():
                        try:self.messages.put(value,timeout=.1);break
                        except queue.Full:pass
            finally:
                while not self.closed.is_set():
                    try:self.messages.put(None,timeout=.1);break
                    except queue.Full:pass
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()

    def write(self,value):
        try:self.proc.stdin.write((json.dumps(value)+'\n').encode());self.proc.stdin.flush()
        except (BrokenPipeError,OSError):raise BoardError('Codex app-server 연결이 종료됐습니다.',502)

    def send(self,method,params):
        self.sequence+=1;self.write({'id':self.sequence,'method':method,'params':params});return self.sequence

    def receive(self,handle,cancel,deadline):
        while True:
            reason=cancel()
            if reason:raise InterruptedError(reason)
            if time.monotonic()>deadline:raise BoardError('Codex app-server 응답 시간이 초과됐습니다.',504)
            try:value=self.messages.get(timeout=.1)
            except queue.Empty:continue
            if value is None:raise BoardError('Codex app-server가 응답 전에 종료됐습니다.',502)
            if 'method' in value:
                if 'id' in value:
                    if getattr(self,'request_handler',None) and self.request_handler(value):return {'notification':True}
                    method=value['method']
                    if method in {'item/commandExecution/requestApproval','item/fileChange/requestApproval'}:self.write({'id':value['id'],'result':{'decision':'decline'}})
                    else:self.write({'id':value['id'],'error':{'code':-32601,'message':'Tack의 승인 범위를 확대할 수 없습니다. 구조화된 사용자 질문으로 반환하세요.'}})
                else:handle(value)
                return {'notification':True}
            return value

    def call(self,method,params,handle,cancel):
        request=self.send(method,params);deadline=time.monotonic()+30
        while True:
            value=self.receive(handle,cancel,deadline)
            if value.get('id')!=request:continue
            if 'error' in value:raise BoardError('Codex app-server '+method+': '+str(value['error'].get('message','요청 실패'))[:500],502)
            return value.get('result',{})

    def run(self,work,goal,job,worker,run_id,prompt,thread,write,network,model):
        turn=None;thread_id=thread;status=None;body='';usage=None;count=0;last_update=0
        def cancel():
            state=work.goal(goal['id'])
            from .collaboration import Collaboration
            return 'interrupted' if state.get('worker')!=worker or state.get('pause_requested') else work.orch.budget_reason(state) or Collaboration(work).budget(goal['id'],job['employee'])
        def event(value):
            nonlocal turn,status,body,usage,count,last_update
            method=value.get('method');p=value.get('params',{})
            if (p.get('turnId') or p.get('turn',{}).get('id')) in self.finished_turns:return
            if p.get('threadId') and thread_id and p['threadId']!=thread_id:return
            if p.get('turnId') and turn and p['turnId']!=turn:return
            count+=1
            if method=='thread/tokenUsage/updated':usage=p.get('tokenUsage',{}).get('last')
            if method=='item/completed' and p.get('item',{}).get('type')=='agentMessage':body=p['item'].get('text','')
            if method=='turn/completed':
                current=p.get('turn',{})
                if turn and current.get('id')!=turn:return
                status=current.get('status');turn=current.get('id',turn)
                if not body:
                    body=next((item.get('text','') for item in reversed(current.get('items',[])) if item.get('type')=='agentMessage'),'')
            now=time.monotonic()
            if now-last_update>.5:
                work.orch.update(goal['id'],{'heartbeat':time.time()},worker=worker)
                activity={'item/started':'도구 실행 중','item/completed':'실행 결과 확인','item/agentMessage/delta':'응답 작성 중','thread/tokenUsage/updated':'사용량 수집 중'}.get(method,'에이전트와 연결 중')
                work.orch.run_update(run_id,event_count=count,activity=activity);last_update=now
        try:
            if not self.initialized:
                self.call('initialize',{'clientInfo':{'name':'tack','title':'Tack','version':'0.2.0'},'capabilities':{'experimentalApi':True}},event,cancel)
                self.write({'method':'initialized','params':{}});self.initialized=True
            cwd=job.get('execution_workspace',work.workspace)
            params={'cwd':cwd,'approvalPolicy':'never','sandbox':'workspace-write' if write else 'read-only'}
            if model:params['model']=model
            if thread:params['threadId']=thread
            reply=self.call('thread/resume' if thread else 'thread/start',params,event,cancel)
            thread_id=reply.get('thread',{}).get('id')
            if not thread_id:raise BoardError('Codex가 유효한 직원 대화를 반환하지 않았습니다.',502)
            work.bind_thread(goal['id'],job['id'],job['employee'],thread_id,thread)
            work.orch.run_update(run_id,thread_id=thread_id,adapter='codex-app-server')
            sandbox={'type':'workspaceWrite','writableRoots':[cwd],'networkAccess':network,'excludeTmpdirEnvVar':True,'excludeSlashTmp':True} if write else {'type':'readOnly'}
            params={'threadId':thread_id,'cwd':cwd,'approvalPolicy':'never','sandboxPolicy':sandbox,'input':[{'type':'text','text':prompt}],'outputSchema':json.loads((Path(__file__).parent/'assets/employee.schema.json').read_text())}
            if model:params['model']=model
            reply=self.call('turn/start',params,event,cancel);turn=reply.get('turn',{}).get('id',turn)
            if not turn:raise BoardError('Codex가 유효한 실행 ID를 반환하지 않았습니다.',502)
            work.orch.run_update(run_id,turn_id=turn)
            while status is None:self.receive(event,cancel,time.monotonic()+7200)
            self.finished_turns.add(turn)
            from .workforce import validate_result
            result=validate_result(json.loads(body)) if status=='completed' else None
            if usage is not None:
                values={'input_tokens':usage.get('inputTokens'),'cached_input_tokens':usage.get('cachedInputTokens'),'output_tokens':usage.get('outputTokens')}
                Usage(work.store).turn(run_id,values)
                tokens=sum(v for k,v in values.items() if k!='cached_input_tokens' and type(v) is int)
                work.orch.add_tokens(goal['id'],worker,tokens)
                work.orch.run_update(run_id,tokens=tokens)
            return {'status':'succeeded' if status=='completed' else 'interrupted' if status=='interrupted' else 'failed','exit_code':0 if status=='completed' else 1,'team_result':result,'error':'' if status=='completed' else 'Codex 실행 '+str(status)}
        except InterruptedError as error:
            if thread_id and turn:
                self.send('turn/interrupt',{'threadId':thread_id,'turnId':turn})
            self.close()
            return {'status':'interrupted','exit_code':None,'team_result':None,'error':str(error)}
        except (BoardError,ValueError,TypeError) as error:
            self.close()
            return {'status':'failed','exit_code':1,'team_result':None,'error':str(error)[:700]}

    def close(self):
        if self.closed.is_set():return
        self.closed.set()
        try:
            if self.proc.poll() is None:
                os.killpg(self.proc.pid,signal.SIGTERM)
                try:self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:os.killpg(self.proc.pid,signal.SIGKILL);self.proc.wait()
        except ProcessLookupError:pass
        for stream in (self.proc.stdin,self.proc.stdout):
            try:stream.close()
            except (OSError,ValueError):pass
        self.reader.join(timeout=1)
