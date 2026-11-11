const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const original=Module._load;
Module._load=function(name,...args){return name==='canvas'?{}:original.call(this,name,...args);};
const {JSDOM}=Module.createRequire(require.resolve('jest-environment-jsdom',{paths:[process.env.AGENT_WORK_JSDOM_ROOT||process.cwd()]}))('jsdom');
Module._load=original;
const asset=name=>fs.readFileSync(path.join(__dirname,'../agent_work/assets',name),'utf8');
const tick=()=>new Promise(setImmediate);

async function scene(t,{reduced=false,hidden=false,deferImages=false,employees}={}){
  const dom=new JSDOM('<body><main></main></body>',{runScripts:'outside-only'});
  const w=dom.window,host=w.document.querySelector('main'),draws=[],images=[],frames=new Map(),listeners=new Set();
  let frameId=0;
  Object.defineProperty(w.document,'hidden',{get:()=>hidden});
  Object.defineProperty(w,'devicePixelRatio',{value:3});
  const media={matches:reduced,addEventListener:(event,fn)=>listeners.add(fn),removeEventListener:(event,fn)=>listeners.delete(fn)};
  w.matchMedia=()=>media;
  w.requestAnimationFrame=fn=>{frames.set(++frameId,fn);return frameId;};
  w.cancelAnimationFrame=id=>frames.delete(id);
  w.Image=class{
    set src(value){this.url=value;images.push(this);if(!deferImages)queueMicrotask(()=>this.onload());}
  };
  const context=Object.fromEntries(['setTransform','clearRect','save','translate','scale','beginPath','ellipse','fill','stroke','fillRect','restore','moveTo','lineTo','drawImage'].map(name=>[name,(...args)=>draws.push([name,...args])]));
  w.HTMLCanvasElement.prototype.getContext=()=>context;
  w.eval(asset('office-art.js'));
  w.eval(asset('office-scene.js'));
  const camera={zoom:1,x:0,y:0};
  employees=employees||[
    {id:'implement',name:'개발',state:'working',current_job:{title:'실제 구현'}},
    {id:'plan',name:'기획',state:'idle'},
    {id:'verify',name:'QA',state:'waiting_user'}
  ];
  const controller=w.TackOfficeScene.create(host,{camera,onSelect(){},employees});
  t.after(()=>{controller.destroy();dom.window.close();});
  await tick();
  return {w,host,controller,camera,employees,draws,images,frames,listeners,media,
    step(time){const next=[...frames.values()];frames.clear();for(const fn of next)fn(time);},
    visibility(value){hidden=value;w.document.dispatchEvent(new w.Event('visibilitychange'));},
    preference(value){media.matches=value;for(const fn of listeners)fn();}
  };
}

test('scene shares atlas decoding, caps pixel ratio and paints at most 24fps',async t=>{
  const s=await scene(t);
  assert.equal(s.images.length,2);
  assert.equal(s.host.querySelector('canvas').width,2000);
  assert.equal(s.host.dataset.motion,'running');
  s.step(0);
  const count=s.draws.filter(([name])=>name==='clearRect').length;
  s.step(10);s.step(20);
  assert.equal(s.draws.filter(([name])=>name==='clearRect').length,count);
  s.step(50);
  assert.equal(s.draws.filter(([name])=>name==='clearRect').length,count+1);
});

test('recorded working and idle employees animate independently with actual activity text',async t=>{
  const s=await scene(t);
  for(let time=0;time<=1200;time+=100)s.step(time);
  const art=s.w.TackOfficeArt;
  const sources=s.draws.filter(([name])=>name==='drawImage');
  for(const pose of ['sit','type']){
    const sprite=art['person-1-front-'+pose];
    assert.ok(sources.some(([,image,x,y])=>x===sprite.sx&&y===sprite.sy));
  }
  const idle=art['person-0-front-sit'];
  const heights=new Set(sources.filter(([,image,x,y])=>x===idle.sx&&y===idle.sy).map(args=>args[7]));
  assert.ok(heights.size>1);
  assert.match(s.host.querySelector('[data-employee=implement] .scene-bubble').textContent,/실제 구현/);
  assert.equal(s.host.querySelectorAll('.scene-activity-dots i').length,3);
  assert.equal(s.host.querySelector('[data-employee=plan] .scene-bubble'),null);
});

test('manual pause, hidden tab and live reduced-motion changes cancel and resume one loop',async t=>{
  const s=await scene(t);
  assert.equal(s.frames.size,1);
  s.controller.setMotionPaused(true);
  assert.equal(s.frames.size,0);
  assert.equal(s.host.dataset.motion,'paused');
  s.visibility(true);s.controller.setMotionPaused(false);
  assert.equal(s.frames.size,0);
  s.visibility(false);
  assert.equal(s.frames.size,1);
  s.preference(true);
  assert.equal(s.frames.size,0);
  s.visibility(true);s.visibility(false);
  assert.equal(s.frames.size,0);
  s.preference(false);
  assert.equal(s.frames.size,1);
  s.controller.destroy();
  assert.equal(s.frames.size,0);
  assert.equal(s.listeners.size,0);
  const count=s.draws.length;
  s.visibility(false);s.w.dispatchEvent(new s.w.Event('resize'));
  assert.equal(s.draws.length,count);
});

test('reduced-motion starts static and late image loads cannot restart a destroyed scene',async t=>{
  const reduced=await scene(t,{reduced:true});
  assert.equal(reduced.frames.size,0);
  assert.equal(reduced.host.dataset.motion,'paused');
  assert.ok(reduced.draws.some(([name])=>name==='drawImage'));
  const late=await scene(t,{deferImages:true});
  late.controller.destroy();
  for(const image of late.images)image.onload();
  await tick();
  assert.equal(late.frames.size,0);
  assert.notEqual(late.host.dataset.ready,'true');
});

test('all twelve looks have four-direction walking, standing and coffee frames',async t=>{
  const s=await scene(t);
  for(let look=0;look<12;look++)for(const direction of ['SW','SE','NW','NE'])for(const pose of ['stand','walk1','walk2','cup','cupPut']){
    const frame=s.w.TackOfficeArt['person-'+look+'-'+direction+'-'+pose];
    assert.ok(frame);
    assert.ok(frame.sx+frame.width<=1024&&frame.sy+frame.height<=2048);
    assert.equal(frame.url,'/office-atlas.png');
  }
});

test('idle staff walk to a rest area and return while working and waiting staff remain seated',async t=>{
  const s=await scene(t),motion=s.camera.motionActors.get('plan'),modes=new Set(),button=s.host.querySelector('[data-employee=plan]');
  const initial=button.style.left,positions=new Set();
  s.step(0);
  for(let time=100;time<=50000;time+=100){
    s.step(time);modes.add(motion.mode);positions.add(button.style.left);
    assert.equal(s.camera.motionActors.get('implement').mode,'desk');
    assert.equal(s.camera.motionActors.get('verify').mode,'desk');
    assert.ok(motion.x>=0&&motion.x<=18&&motion.y>=0&&motion.y<=14);
  }
  for(const mode of ['desk','walking','resting','returning'])assert.ok(modes.has(mode),mode);
  assert.ok([...positions].some(position=>position!==initial));
  assert.match(button.getAttribute('aria-label'),/대기 중/);
});

test('an arriving job interrupts a break and the staff member walks back to their desk',async t=>{
  const s=await scene(t),motion=s.camera.motionActors.get('plan');
  motion.nextAt=0;s.step(0);
  let time=0;
  while(motion.mode!=='resting'&&time<30000){time+=100;s.step(time);}
  assert.equal(motion.mode,'resting');
  s.employees[1].state='working';time+=100;s.step(time);
  assert.equal(motion.mode,'returning');
  while(motion.mode!=='desk'&&time<60000){time+=100;s.step(time);}
  assert.equal(motion.mode,'desk');
  assert.equal(motion.x,motion.home.x);assert.equal(motion.y,motion.home.y);
  time+=1000;s.step(time);assert.equal(motion.mode,'desk');
});

test('paused walking position survives scene replacement and reduced motion never starts a break',async t=>{
  const s=await scene(t),motion=s.camera.motionActors.get('plan');
  motion.nextAt=0;s.step(0);s.step(100);s.step(200);
  s.controller.setMotionPaused(true);
  const position={x:motion.x,y:motion.y},before=s.host.querySelector('[data-employee=plan]').style.left;
  s.controller.destroy();
  const host=s.w.document.createElement('main');s.w.document.body.append(host);
  const replacement=s.w.TackOfficeScene.create(host,{camera:s.camera,employees:s.employees,onSelect(){}});
  await tick();
  assert.equal(motion.x,position.x);assert.equal(motion.y,position.y);
  assert.equal(host.querySelector('[data-employee=plan]').style.left,before);
  assert.equal(s.frames.size,0);replacement.destroy();
  const reduced=await scene(t,{reduced:true});
  reduced.camera.motionActors.get('plan').nextAt=0;
  reduced.step(20000);
  assert.equal(reduced.camera.motionActors.get('plan').mode,'desk');
});

test('all twelve staff routes clear desk and chair footprints after leaving their own seat',async t=>{
  const employees=Array.from({length:12},(_,i)=>({id:'staff'+i,name:'직원'+i,state:'idle'}));
  const s=await scene(t,{employees}),obstacles=[];
  for(let i=0;i<12;i++){
    const x=2.5+(i%3)*3.3,y=3+Math.floor(i/3)*2.8;
    for(const [key,dx,dy] of [['desk-0',x,y],['chair-0-seat',x,y-1],['chair-0-back',x,y-1]]){
      const box=s.w.TackOfficeArt[key].box;
      obstacles.push({x:dx+box.x,y:dy+box.y,w:box.w,d:box.d});
    }
  }
  s.step(0);
  for(let time=100;time<=35000;time+=100){
    s.step(time);
    for(const motion of s.camera.motionActors.values()){
      if(motion.mode==='desk'||motion.next===1||motion.mode==='returning'&&motion.next===motion.path.length-1)continue;
      assert.ok(obstacles.every(o=>motion.x+.16<=o.x||motion.x-.16>=o.x+o.w||motion.y+.16<=o.y||motion.y-.16>=o.y+o.d),'furniture collision at '+motion.x+','+motion.y);
    }
  }
});

test('work arriving during the outbound walk reverses only the visited route',async t=>{
  const s=await scene(t),motion=s.camera.motionActors.get('plan');
  motion.nextAt=0;s.step(0);
  for(let time=100;time<=1700;time+=100)s.step(time);
  const before={x:motion.x,y:motion.y};
  s.employees[1].state='queued';s.step(1800);
  assert.equal(motion.mode,'returning');
  assert.ok(Math.hypot(motion.x-before.x,motion.y-before.y)<=.35);
  for(let time=1900;time<=12000&&motion.mode!=='desk';time+=100)s.step(time);
  assert.equal(motion.mode,'desk');
  assert.equal(motion.x,motion.home.x);assert.equal(motion.y,motion.home.y);
});

test('crowded employee labels separate and retain leader lines without resetting paused positions',async t=>{
  const s=await scene(t,{employees:Array.from({length:7},(_,i)=>({id:'crowd'+i,name:'긴이름직원'+i,state:'idle'}))});
  for(const motion of s.camera.motionActors.values())Object.assign(motion,{mode:'resting',x:13,y:11.9,nextAt:100000});
  s.step(0);s.step(100);
  const labels=[...s.host.querySelectorAll('.studio-desk')].map(n=>({left:parseFloat(n.style.left),top:parseFloat(n.style.top),lift:parseFloat(n.style.getPropertyValue('--leader-length'))}));
  const labelScale=Number(s.host.querySelector('.studio-desk').style.getPropertyValue('--label-scale'));
  const fullWidth=Math.max('긴이름직원0'.length*12+34,102)*labelScale;
  const fullHeight=51*labelScale;
  for(let i=0;i<labels.length;i++)for(let j=0;j<i;j++)assert.ok(Math.abs(labels[i].top-labels[j].top)>=fullHeight+6||Math.abs(labels[i].left-labels[j].left)>=fullWidth+6);
  assert.ok(labels.every(n=>n.lift<=250));
  assert.ok(labels.some(n=>n.lift>0));
  s.controller.setMotionPaused(true);
  const positions=[...s.host.querySelectorAll('.studio-desk')].map(n=>n.style.top);
  s.controller.zoomBy(1);
  assert.deepEqual([...s.host.querySelectorAll('.studio-desk')].map(n=>n.style.top),positions);
});
