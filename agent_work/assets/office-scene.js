/* Isometric renderer for Tack state. Art and depth ordering adapted from AI Office (MIT); see ai-office.LICENSE. */
window.TackOfficeScene=(()=>{
  const project=(x,y)=>({x:(x-y)*32,y:(x+y)*16});
  const hash=id=>{let n=0;for(const c of id)n=(n*31+c.charCodeAt(0))>>>0;return n;};
  const look=id=>({plan:0,implement:1,assess:2,verify:3,cto:4,frontend:5,qa:6}[id]??hash(id)%12);
  const labels={external:'외부 세션',working:'업무 중',waiting_user:'답변 필요',awaiting_approval:'승인 필요',conflict:'병합 확인',needs_recheck:'재확인 필요',failed:'확인 필요',queued:'실행 대기',idle:'대기 중',waiting_contract:'계약 대기',waiting_reply:'답변 대기',waiting_jobs:'동료 업무 대기',waiting_conditions:'선행 조건 대기',awaiting_review:'검토 중',paused:'중단'};
  let imagePromise;
  function images(){
    if(!imagePromise){
      const sources=new Map();
      const entries=Object.entries(window.TackOfficeArt||{}).filter(([key])=>!key.startsWith('avatar-'));
      for(const [,art] of entries){
        if(sources.has(art.url))continue;
        sources.set(art.url,new Promise(resolve=>{
          const image=new Image();
          image.onload=()=>resolve(image);
          image.onerror=()=>resolve(null);
          image.src=art.url;
        }));
      }
      imagePromise=Promise.all(entries.map(async([key,art])=>[key,await sources.get(art.url)])).then(Object.fromEntries);
    }
    return imagePromise;
  }
  // AI Office shared/iso/depth.ts: sort boxes by spatial occlusion, not just their screen Y.
  function behind(a,b){const e=1e-6;if(a.x+a.w-a.y<=b.x-b.y-b.d+e||b.x+b.w-b.y<=a.x-a.y-a.d+e)return false;
    if((a.x+a.w+a.y+a.d)*8-a.z<=(b.x+b.y)*8-b.z-b.h+e||(b.x+b.w+b.y+b.d)*8-b.z<=(a.x+a.y)*8-a.z-a.h+e)return false;
    if(a.x+a.w<=b.x+e||a.y+a.d<=b.y+e)return true;
    return !(b.x+b.w<=a.x+e||b.y+b.d<=a.y+e)&&a.z+a.h<=b.z+e;}
  function depthOrder(items){const edges=items.map(()=>[]),seen=new Set(),out=[];for(let i=0;i<items.length;i++)for(let j=0;j<items.length;j++)if(i!==j&&behind(items[i].box,items[j].box))edges[j].push(i);
    const visit=i=>{if(seen.has(i))return;seen.add(i);for(const j of edges[i])visit(j);out.push(items[i]);};
    [...items.keys()].sort((i,j)=>{const a=items[i].box,b=items[j].box;return a.x+a.y+(a.w+a.d)/2-b.x-b.y-(b.w+b.d)/2;}).forEach(visit);return out;}
  function create(host,{employees,onSelect,selected='',camera={zoom:1,x:0,y:0}}){
    const art=window.TackOfficeArt||{},canvas=document.createElement('canvas');canvas.className='office-canvas';canvas.setAttribute('aria-hidden','true');host.append(canvas);
    const ctx=canvas.getContext('2d'),layer=document.createElement('div');layer.className='scene-people';host.append(layer);
    let width=0,height=0,scale=1,offset={x:0,y:0},loaded={},destroyed=false,frame=null,drag=null;
    let elapsed=camera.sceneTime||0,lastTick=null,lastPaint=-Infinity,ready=false,positionKey='';
    const motionActors=camera.motionActors||(camera.motionActors=new Map());
    const motionPreference=window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const frameInterval=1000/24;
    const actors=[],items=[];let bubbles=0;
    function item(key,x,y,rot=0,role=''){const name=key+'-'+rot+(role?'-'+role:''),sprite=art[name];if(!sprite)return;items.push({key:name,x,y,box:{...sprite.box,x:x+sprite.box.x,y:y+sprite.box.y}});}
    item('bookshelf',1,.1);item('bookshelf',3.2,.1);item('plant',.2,.2);item('whiteboard',8.5,.3);item('plant',16,.4);
    item('prop-cooler',.25,4,3);item('prop-coffeeCorner',.3,6,3);item('prop-printer',.3,8.5,3);
    item('sofa-green',12,10,2);item('rug-round',13,10.5);item('plant',16.5,12);item('plant',.4,12.5);
    item('lv-meeting-table',13,3);item('lv-meeting-table',13,5);for(const [x,y,r]of [[12,3.5,2],[12,5.5,2],[15,3.5,0],[15,5.5,0]])for(const role of ['seat','back'])item('chair',x,y,r,role);
    employees.forEach((employee,index)=>{const col=index%3,row=Math.floor(index/3),x=2.5+col*3.3,y=3+row*2.8;
      item('desk',x,y);item('chair',x,y-1,0,'seat');item('chair',x,y-1,0,'back');
      const point=project(x+.5,y-.5);
      const actor={employee,index,x:x+.5,y:y-.5,point,key:'person-'+look(employee.id)+'-front-',phase:hash(employee.id)%6000,desk:{x,y},box:{x:x+.32,y:y-.68,w:.36,d:.36,z:12,h:30}};const home={x:actor.x,y:actor.y};
      let motion=motionActors.get(employee.id);
      if(!motion||motion.home.x!==home.x||motion.home.y!==home.y){
        motion={...home,home,mode:'desk',direction:'SW',nextAt:elapsed+2200+actor.phase*.6,lastTime:elapsed,path:[],next:0};
        motionActors.set(employee.id,motion);
      }
      actor.motion=motion;
      actors.push(actor);items.push(actor);
      const b=document.createElement('button');b.type='button';b.className='studio-desk';b.dataset.employee=employee.id;b.dataset.state=employee.state;b.style.setProperty('--motion-delay',String(-actor.phase/1000)+'s');b.setAttribute('aria-label',employee.name+' · '+(labels[employee.state]||employee.state)+' · 직원 정보 보기');b.setAttribute('aria-pressed',String(selected===employee.id));
      const dot=document.createElement('span');dot.className='scene-person-dot';dot.setAttribute('aria-hidden','true');const name=document.createElement('strong');name.textContent=employee.name;b.append(dot,name);
      const state=document.createElement('span');state.textContent=labels[employee.state]||employee.state;state.className='scene-person-state';b.append(state);b.addEventListener('click',()=>onSelect(employee.id));layer.append(b);actor.button=b;
      const location=document.createElement('small');location.className='scene-person-location';location.hidden=true;b.append(location);actor.location=location;
      if(bubbles<2&&['working','waiting_user','awaiting_approval','conflict','needs_recheck','failed'].includes(employee.state)){bubbles++;
        const bubble=document.createElement('span');bubble.className='scene-bubble';bubble.textContent=employee.state==='working'?(employee.activity||employee.current_job?.title||'업무를 진행하고 있어요'):(employee.state==='waiting_user'?'답변을 기다리고 있어요':labels[employee.state]||employee.state);bubble.title=employee.current_job?.title||bubble.textContent;
        if(employee.state==='working'){
          const dots=document.createElement('span');dots.className='scene-activity-dots';dots.setAttribute('aria-hidden','true');
          for(let i=0;i<3;i++)dots.append(document.createElement('i'));
          bubble.append(dots);
        }
        b.append(bubble);
      }
    });
    for(const actor of actors){const desk=items.find(it=>it.key==='desk-0'&&it.x===actor.desk.x&&it.y===actor.desk.y);if(desk)desk.actor=actor;}
    let ordered=depthOrder(items);
    const room=art.room||{width:1052,height:640,o:[466,108]},left=-room.o[0],top=-room.o[1];
    function routeFor(actor){
      const {desk,index}=actor,home=actor.motion.home;
      const slot=Math.floor(index/2),aisle=desk.x+2.4,lane=desk.y+1.45;
      const coffee=index%2===0;
      const destination=coffee?{x:1.55+(slot%2)*.6,y:5.8+Math.floor(slot/2)}:{x:12.6+(slot%3)*1.1,y:11.9+Math.floor(slot/3)*.9};
      const corridor=coffee?1.55:11.5;
      return {zone:coffee?'coffee':'lounge',path:[home,{x:aisle,y:home.y},{x:aisle,y:lane},{x:corridor,y:lane},{x:corridor,y:destination.y},destination]};
    }
    function returnToDesk(actor){
      const motion=actor.motion;
      const visited=motion.mode==='resting'?motion.path:motion.path.slice(0,motion.next);
      motion.path=[{x:motion.x,y:motion.y},...visited.slice().reverse()];
      motion.next=1;
      motion.mode='returning';
    }
    function updateActors(time,advance){
      let changed=false;
      for(const actor of actors){
        const motion=actor.motion;
        const delta=advance?Math.max(0,Math.min((time-motion.lastTime)/1000,.1)):0;
        motion.lastTime=time;
        const restingAllowed=['idle','done'].includes(actor.employee.state);
        if(advance){
          if(!restingAllowed){
            if(motion.mode==='walking'||motion.mode==='resting')returnToDesk(actor);
            if(motion.mode==='desk')motion.nextAt=time+2200+actor.phase*.6;
          }
          if(motion.mode==='desk'&&restingAllowed&&time>=motion.nextAt){
            const route=routeFor(actor);
            motion.zone=route.zone;motion.path=route.path;motion.next=1;motion.mode='walking';
          }
          if(motion.mode==='resting'&&time>=motion.nextAt)returnToDesk(actor);
          if(motion.mode==='walking'||motion.mode==='returning'){
            const target=motion.path[motion.next];
            const dx=target.x-motion.x,dy=target.y-motion.y,distance=Math.hypot(dx,dy);
            const travel=delta*(restingAllowed?1.8:3.4);
            if(distance<=travel||distance<.001){
              motion.x=target.x;motion.y=target.y;motion.next++;
              if(motion.next===motion.path.length){
                motion.mode=motion.mode==='returning'?'desk':'resting';
                motion.nextAt=time+(motion.mode==='resting'?9000:7000)+actor.phase;
              }
            }else{
              motion.x+=dx/distance*travel;motion.y+=dy/distance*travel;
            }
            if(distance>.001)motion.direction=Math.abs(dx)>Math.abs(dy)?(dx>0?'SE':'NW'):(dy>0?'SW':'NE');
            changed=true;
          }
        }
        const seated=motion.mode==='desk';
        if(actor.box.x!==motion.x-.18||actor.box.y!==motion.y-.18||actor.box.z!==(seated?12:0))changed=true;
        actor.point=project(motion.x,motion.y);
        actor.box={x:motion.x-.18,y:motion.y-.18,w:.36,d:.36,z:seated?12:0,h:34};
        const activity={walking:'휴게 공간으로 이동',resting:'휴식 중',returning:'자리로 복귀'}[motion.mode]||'';
        if(actor.button.dataset.sceneActivity!==motion.mode){
          actor.button.dataset.sceneActivity=motion.mode;
          actor.location.textContent=activity;actor.location.hidden=!activity;
          actor.button.setAttribute('aria-label',actor.employee.name+' · '+(labels[actor.employee.state]||actor.employee.state)+(activity?' · '+activity+' 연출':'')+' · 직원 정보 보기');
        }
      }
      if(changed)ordered=depthOrder(items);
    }
    function spriteFor(actor,time){
      const motion=actor.motion,beat=time+actor.phase;
      if(motion.mode==='desk')return actor.key+(actor.employee.state==='working'&&Math.floor(beat/180)%4!==3?'type':'sit');
      let pose='stand';
      if(motion.mode==='resting')pose=Math.floor(beat/2200)%4===0?'cupPut':'cup';
      else if(!motionPreference?.matches)pose=Math.floor(beat/150)%2?'walk1':'walk2';
      return 'person-'+look(actor.employee.id)+'-'+motion.direction+'-'+pose;
    }
    function position(){
      const key=[width,height,camera.zoom,camera.x,camera.y].join(':');
      if(key!==positionKey){
        positionKey=key;
        const pad=width<600?12:54;
        scale=Math.min((width-pad*2)/room.width,(height-pad*2)/room.height)*camera.zoom;
        scale=Math.max(.15,scale);
        offset={x:(width-room.width*scale)/2-left*scale+camera.x,y:(height-room.height*scale)/2-top*scale+camera.y};
      }
      if(!host.isConnected)return;
      const labelScale=Math.max(.72,Math.min(1,scale));
      const placements=[];
      for(const actor of actors){
        const sprite=art[spriteFor(actor,elapsed)];
        const drop=actor.motion.mode==='desk'?2-(12-(sprite?.drop||14))*2:0;
        const labelY=actor.point.y+drop-(sprite?.height||70)-6;
        const center=offset.x+actor.point.x*scale;
        const anchor=offset.y+labelY*scale;
        const metricsKey=[actor.motion.mode,actor.button.matches(':hover')].join(':');
        if(actor.metricsKey!==metricsKey){
          actor.metricsKey=metricsKey;
          const bubble=actor.button.querySelector('.scene-bubble');
          actor.labelMetrics={
            width:Math.max(actor.button.offsetWidth||actor.employee.name.length*12+34,actor.location.hidden?0:actor.location.offsetWidth||102,bubble?.offsetWidth||0),
            above:(actor.button.offsetHeight||29)+(bubble?(bubble.offsetHeight||44)+11:0),
            below:actor.location.hidden?0:(actor.location.offsetHeight||17)+5
          };
        }
        const metrics=actor.labelMetrics;
        const stepX=metrics.width*labelScale+12;
        const stepY=(metrics.above+metrics.below)*labelScale+8;
        let placement;
        for(let row=0;row<=actors.length&&!placement;row++){
          for(const column of [0,-1,1]){
            const shiftX=column*stepX,shiftY=-row*stepY;
            const box={left:center+shiftX-metrics.width*labelScale/2,right:center+shiftX+metrics.width*labelScale/2,top:anchor+shiftY-metrics.above*labelScale,bottom:anchor+shiftY+metrics.below*labelScale};
            if(box.left<8||box.right>width-8||box.top<8)continue;
            const overlap=placements.some(p=>box.left<p.box.right+6&&box.right>p.box.left-6&&box.top<p.box.bottom+6&&box.bottom>p.box.top-6);
            if(!overlap){placement={actor,center,anchor,shiftX,shiftY,box};break;}
          }
        }
        placements.push(placement||{actor,center,anchor,shiftX:0,shiftY:0,box:{left:center,right:center,top:anchor,bottom:anchor}});
      }
      for(const {actor,center,anchor,shiftX,shiftY} of placements){
        const length=Math.hypot(shiftX,shiftY);
        actor.button.style.left=(center+shiftX)+'px';
        actor.button.style.top=(anchor+shiftY)+'px';
        actor.button.style.setProperty('--label-scale',String(labelScale));
        actor.button.style.setProperty('--leader-length',String(length/labelScale)+'px');
        actor.button.style.setProperty('--leader-angle',String(Math.atan2(shiftX,-shiftY)*180/Math.PI)+'deg');
        actor.button.dataset.labelShifted=String(length>1);
      }
    }
    function drawMonitor(actor,time){
      if(actor.employee.state!=='working')return;
      const point=project(actor.desk.x,actor.desk.y);
      const pulse=.65+.15*Math.sin((time+actor.phase)/450);
      ctx.save();
      ctx.globalAlpha=pulse*.24;
      ctx.fillStyle='#8de4cc';
      ctx.beginPath();
      ctx.ellipse(point.x-5,point.y-35,21,16,-.45,0,Math.PI*2);
      ctx.fill();
      ctx.globalAlpha=pulse;
      ctx.strokeStyle='#89e1bd';
      ctx.lineWidth=1.5;
      const line=Math.floor((time+actor.phase)/400)%3;
      for(let i=0;i<3;i++){
        const y=point.y-37+i*4;
        ctx.beginPath();
        ctx.moveTo(point.x-12,y);
        ctx.lineTo(point.x-12+(i===line?12:7),y+(i===line?6:3.5));
        ctx.stroke();
      }
      ctx.restore();
    }
    function drawActor(actor,time){
      const state=actor.employee.state,working=state==='working';
      const beat=time+actor.phase;
      const key=spriteFor(actor,time),sprite=art[key],image=loaded[key];
      if(!sprite||!image)return;
      const breathing=['idle','queued','waiting_reply','waiting_jobs','waiting_conditions'].includes(state);
      const walking=['walking','returning'].includes(actor.motion.mode);
      const movement=walking?-Math.abs(Math.sin(beat/150*Math.PI))*.8:working?Math.sin(beat/95)*.45:breathing?Math.sin(beat/900)*.75:0;
      const seated=actor.motion.mode==='desk';
      const drop=seated?2-(12-sprite.drop)*2:0;
      const y=actor.point.y+drop-sprite.height+movement;
      if(!seated){
        ctx.save();ctx.globalAlpha=.16;ctx.fillStyle='#292b3d';ctx.beginPath();
        ctx.ellipse(actor.point.x,actor.point.y-1,11,4,0,0,Math.PI*2);ctx.fill();ctx.restore();
      }
      ctx.drawImage(image,sprite.sx||0,sprite.sy||0,sprite.width,sprite.height,actor.point.x-sprite.width/2,y,sprite.width,sprite.height);
      if(selected===actor.employee.id){
        const pulse=1+Math.sin(time/500)*.08;
        ctx.strokeStyle='#b5a0ff';
        ctx.lineWidth=2/scale;
        ctx.beginPath();
        ctx.ellipse(actor.point.x,actor.point.y-(seated?18:2),19*pulse,9*pulse,0,0,Math.PI*2);
        ctx.stroke();
      }
      if(working){
        for(let i=0;i<3;i++){
          ctx.globalAlpha=.25+.75*(.5+.5*Math.sin(beat/260-i*1.2));
          ctx.fillStyle='#94e1bf';
          ctx.fillRect(actor.point.x-6+i*5,y-8,3,3);
        }
        ctx.globalAlpha=1;
      }
    }
    function drawSteam(time){
      const point=project(.3,6);
      ctx.save();
      ctx.fillStyle='#e9dcd1';
      for(let i=0;i<3;i++){
        const progress=((time+i*800)%2600)/2600;
        ctx.globalAlpha=(1-progress)*.23;
        const x=point.x-5+Math.sin(progress*5+i)*2;
        ctx.fillRect(x,point.y-47-progress*14,2,3);
      }
      ctx.restore();
    }
    function draw(){
      if(destroyed)return;
      updateActors(elapsed,canAnimate());
      position();
      if(!ctx)return;
      const time=motionPreference?.matches?0:elapsed;
      const ratio=Math.min(window.devicePixelRatio||1,2);
      ctx.setTransform(ratio,0,0,ratio,0,0);
      ctx.clearRect(0,0,width,height);
      ctx.save();
      ctx.translate(offset.x,offset.y);
      ctx.scale(scale,scale);
      ctx.imageSmoothingEnabled=false;
      if(loaded.room)ctx.drawImage(loaded.room,0,0,room.width,room.height,left,top,room.width,room.height);
      for(const item of ordered){
        if(item.employee){drawActor(item,time);continue;}
        const sprite=art[item.key],image=loaded[item.key];
        if(!sprite||!image)continue;
        const point=project(item.x,item.y);
        ctx.drawImage(image,sprite.sx||0,sprite.sy||0,sprite.width,sprite.height,point.x-sprite.o[0],point.y-sprite.o[1],sprite.width,sprite.height);
        if(item.actor)drawMonitor(item.actor,time);
        if(item.key==='prop-coffeeCorner-3')drawSteam(time);
      }
      ctx.restore();
    }
    function resize(){
      width=host.clientWidth||1000;
      height=host.clientHeight||650;
      const ratio=Math.min(window.devicePixelRatio||1,2);
      canvas.width=Math.round(width*ratio);
      canvas.height=Math.round(height*ratio);
      draw();
    }
    function animate(timestamp){
      frame=null;
      if(!canAnimate())return;
      if(lastTick!==null)elapsed+=Math.min(timestamp-lastTick,100);
      camera.sceneTime=elapsed;
      lastTick=timestamp;
      if(timestamp-lastPaint>=frameInterval){draw();lastPaint=timestamp;}
      frame=window.requestAnimationFrame(animate);
    }
    function canAnimate(){
      return ready&&!!ctx&&!destroyed&&!document.hidden&&!motionPreference?.matches&&!camera.motionPaused;
    }
    function syncAnimation(){
      if(frame!==null)window.cancelAnimationFrame(frame);
      frame=null;
      lastTick=null;
      lastPaint=-Infinity;
      const running=canAnimate();
      host.dataset.motion=running?'running':'paused';
      draw();
      if(running)frame=window.requestAnimationFrame(animate);
    }
    function setMotionPaused(paused){
      camera.motionPaused=paused;
      syncAnimation();
    }
    function zoomBy(factor){camera.zoom=Math.max(.65,Math.min(2.5,camera.zoom*factor));draw();}
    function reset(){camera.zoom=1;camera.x=0;camera.y=0;draw();}
    canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,cx:camera.x,cy:camera.y};canvas.setPointerCapture?.(e.pointerId);});
    canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;camera.x=Math.max(-width,Math.min(width,drag.cx+dx));camera.y=Math.max(-height,Math.min(height,drag.cy+dy));draw();});
    const release=()=>{drag=null;};canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);
    canvas.addEventListener('wheel',e=>{e.preventDefault();zoomBy(e.deltaY>0?.9:1.1);},{passive:false});
    const observer=window.ResizeObserver?new ResizeObserver(resize):null;observer?.observe(host);window.addEventListener('resize',resize);resize();
    document.addEventListener('visibilitychange',syncAnimation);
    motionPreference?.addEventListener('change',syncAnimation);
    images().then(value=>{
      if(destroyed)return;
      loaded=value;
      if(!loaded.room||!loaded['desk-0']){host.dataset.failed='true';return;}
      resize();
      host.dataset.ready='true';
      ready=true;
      syncAnimation();
    });
    return {zoomBy,reset,setMotionPaused,destroy(){
      if(destroyed)return;
      destroyed=true;
      if(frame!==null)window.cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('resize',resize);
      document.removeEventListener('visibilitychange',syncAnimation);
      motionPreference?.removeEventListener('change',syncAnimation);
    }};
  }
  return {create,look,project,depthOrder};
})();
