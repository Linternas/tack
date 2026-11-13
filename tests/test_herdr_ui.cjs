const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../agent_work/assets/herdr/tack-tasks/main.js'),'utf8')
  .replace(/^import .*;\n/gm,'').replace('export default class TackTasks','class TackTasks')+'\nTackTasks;';

function setup(config={}) {
  let focus={terminal:'t6',space:'project',cwd:'/project/a'};
  const pending=[];
  const requests=[];
  const text=[];
  const clicks={};
  const commands=[];
  let currentId;
  const element=new Proxy({}, {get:(_,name)=>(...args)=>{
    if (name==='id') currentId=args[0];
    if (name==='on_click') clicks[currentId]=args[0];
    if (name==='child' && typeof args[0]==='string') text.push(args[0]);
    if (name==='when' && args[0]) args[1](element);
    return element;
  }});
  const context={View:class {},div:()=>element,v_flex:()=>element,h_flex:()=>element,
    InputState:{new:({value})=>({value:()=>value,set_value:v=>{value=v;},on:()=>{}})},Input:{new:()=>element},
    run:(name,args)=>commands.push([name,args]),props:()=>config,focused:()=>focus,theme:()=>({}),home:()=>'/home/test',
    exec:(_command,args)=>new Promise(resolve=>requests.push({args,resolve})),Date};
  const Mod=vm.runInNewContext(source,context);
  let notifications=0;
  const cx={notify:()=>{notifications++;},timer:{every:()=>{}},focus_handle:()=>({}),spawn:callback=>pending.push(callback(cx))};
  const ui=new Mod();ui.init({},cx);
  const reply=(index,address,task)=>requests[index].resolve({code:0,stdout:JSON.stringify({session:{key:address,address,program:'codex'},project:{name:'project'},task,tasks:[]})});
  return {ui,cx,requests,pending,text,reply,clicks,commands,setFocus:value=>{focus=value;},notifications:()=>notifications};
}

test('native render never notifies and hides old task during an employee switch',async()=>{
  const app=setup();
  app.reply(0,'t6',{id:'TASK-1',title:'Previous employee work',status:'todo'});await app.pending[0];
  app.setFocus({terminal:'t3',space:'project'});
  const before=app.notifications();app.ui.render(app.cx);
  assert.equal(app.notifications(),before);
  assert.equal(app.text.includes('Previous employee work'),false);
  app.ui.poll(app.cx);
  assert.equal(app.ui.data,null);
  app.reply(1,'t3',{id:'TASK-2',title:'Current employee work',status:'todo'});await app.pending[1];
  assert.equal(app.ui.data.task.id,'TASK-2');
});

test('late responses never replace the selected employee snapshot',async()=>{
  const app=setup();
  app.setFocus({terminal:'t3',space:'project'});app.ui.poll(app.cx);
  app.reply(1,'t3',{id:'CURRENT'});await app.pending[1];
  app.reply(0,'t6',{id:'STALE'});await app.pending[0];
  assert.equal(app.ui.data.task.id,'CURRENT');
});

test('bind uses the inspected execution key and explicit previous task',async()=>{
  const app=setup();app.reply(0,'t6',{id:'OLD'});await app.pending[0];
  app.ui.connect({id:'NEW'},app.cx);
  assert.deepEqual(Array.from(app.requests[1].args),['herdr','bind','t6','NEW','--session','t6','--from-task','OLD']);
  app.reply(1,'t6',{id:'NEW'});await app.pending[1];
  app.ui.connect(null,app.cx);
  assert.deepEqual(Array.from(app.requests[2].args),['herdr','unbind','t6','--session','t6','--from-task','NEW']);
  app.reply(2,'t6',null);await app.pending[2];
  assert.equal(app.ui.data.task,null);
});

test('team operations preserve project, goal and version and hide stale project results',async()=>{
  const app=setup();app.reply(0,'t6',null);await app.pending[0];
  app.ui.view='team';app.ui.teamFolder='/project/a';app.ui.teamSpace='project';app.ui.selectedGoal='GOAL-A';
  app.ui.teamCall('approve',{version:7},app.cx);
  const args=Array.from(app.requests[1].args);
  assert.deepEqual(args,['herdr','team','--folder','/project/a','--action','approve','--payload','{"version":7}','--goal','GOAL-A']);
  app.setFocus({space:'other-project',terminal:'t3',cwd:'/project/b'});
  app.ui.poll(app.cx);
  assert.equal(app.ui.teamFolder,'/project/b');
  assert.equal(app.ui.selectedGoal,null);
  app.requests[1].resolve({code:0,stdout:JSON.stringify({enabled:true,goal:{id:'STALE'}})});
  await app.pending[1];
  assert.equal(app.ui.team,null);
  app.requests[2].resolve({code:0,stdout:JSON.stringify({enabled:true,project:'B',goals:[],jobs:[]})});
  await app.pending[2];assert.equal(app.ui.team.project,'B');
});

test('managed executions show their task without manual rebinding controls',async()=>{
  const app=setup();
  app.requests[0].resolve({code:0,stdout:JSON.stringify({session:{address:'t6',program:'backend',managed:true},project:{name:'project'},task:{id:'JOB',title:'Managed job',status:'in_progress'},tasks:[]})});
  await app.pending[0];app.ui.render(app.cx);
  assert.ok(app.text.includes('Managed job'));
  assert.equal(app.text.includes('작업 변경'),false);
  assert.equal(app.text.includes('연결 해제'),false);
});


test('team layout opens team operations and toggles only its execution block',async()=>{
  const app=setup({view:'team',execution_block:'execution'});
  assert.equal(app.requests[0].args[1],'team');
  app.requests[0].resolve({code:0,stdout:JSON.stringify({enabled:true,project:'project',goals:[],jobs:[],roles:[]})});
  await app.pending[0];
  const before=app.notifications();app.ui.render(app.cx);
  assert.equal(app.notifications(),before);
  app.clicks['tack-execution-toggle']({},app.cx);
  assert.equal(app.commands[0][0],'layout.toggle');
  assert.equal(app.commands[0][1].id,'execution');
});

test('team layout reserves the primary area for Tack and opens terminals on demand',()=>{
  const layout=JSON.parse(fs.readFileSync(path.join(__dirname,'../agent_work/assets/herdr/tack-team.json'),'utf8'));
  const work=layout.children.find(item=>item.id==='work');
  const body=work.children.find(item=>item.id==='work-body');
  assert.equal(body.children[0].content.mod,'tack-tasks');
  assert.equal(body.children[0].content.props.view,'team');
  assert.equal(body.children.find(item=>item.id==='execution').hidden,true);
  assert.ok(layout.children[0].children.find(item=>item.id==='sidebar-lists').children.some(item=>item.content.mod==='agents'));
});

test('wide team layout selects a running goal and loads its detail outside render',async()=>{
  const app=setup({view:'team',wide:true});
  app.requests[0].resolve({code:0,stdout:JSON.stringify({enabled:true,goals:[{id:'DONE',status:'done'},{id:'RUN',status:'running'}],jobs:[],roles:[]})});
  await app.pending[0];
  assert.equal(app.ui.selectedGoal,'RUN');
  app.ui.poll(app.cx);
  assert.ok(Array.from(app.requests[1].args).includes('RUN'));
  app.requests[1].resolve({code:0,stdout:JSON.stringify({enabled:true,goal:{id:'RUN',status:'running'},goals:[],jobs:[],roles:[]})});
  await app.pending[1];
  const before=app.notifications();app.ui.render(app.cx);
  assert.equal(app.notifications(),before);
});
