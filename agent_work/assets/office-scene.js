/* Isometric renderer for Tack state. Art and depth ordering adapted from AI Office (MIT); see ai-office.LICENSE. */
window.TackOfficeScene=(()=>{
  const project=(x,y)=>({x:(x-y)*32,y:(x+y)*16});
  const hash=id=>{let n=0;for(const c of id)n=(n*31+c.charCodeAt(0))>>>0;return n;};
  const look=id=>({plan:0,implement:1,assess:2,verify:3,cto:4,frontend:5,qa:6}[id]??hash(id)%12);
  const labels={working:'업무 중',waiting_user:'답변 필요',awaiting_approval:'승인 필요',conflict:'병합 확인',needs_recheck:'재확인 필요',failed:'확인 필요',queued:'실행 대기',idle:'대기 중',waiting_contract:'계약 대기',waiting_reply:'답변 대기',waiting_jobs:'동료 업무 대기',waiting_conditions:'선행 조건 대기',awaiting_review:'검토 중',paused:'중단'};
  let imagePromise;
  function images(){if(!imagePromise)imagePromise=Promise.all(Object.entries(window.TackOfficeArt||{}).map(([key,art])=>new Promise(resolve=>{const image=new Image();image.onload=()=>resolve([key,image]);image.onerror=()=>resolve([key,null]);image.src=art.url;}))).then(Object.fromEntries);return imagePromise;}
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
    let width=0,height=0,scale=1,offset={x:0,y:0},loaded={},destroyed=false,timer,drag=null;
    const actors=[],items=[];let bubbles=0;
    function item(key,x,y,rot=0,role=''){const name=key+'-'+rot+(role?'-'+role:''),sprite=art[name];if(!sprite)return;items.push({key:name,x,y,box:{...sprite.box,x:x+sprite.box.x,y:y+sprite.box.y}});}
    item('bookshelf',1,.1);item('bookshelf',3.2,.1);item('plant',.2,.2);item('whiteboard',8.5,.3);item('plant',16,.4);
    item('prop-cooler',.25,4,3);item('prop-coffeeCorner',.3,6,3);item('prop-printer',.3,8.5,3);
    item('sofa-green',12,10,2);item('rug-round',13,10.5);item('plant',16.5,12);item('plant',.4,12.5);
    item('lv-meeting-table',13,3);item('lv-meeting-table',13,5);for(const [x,y,r]of [[12,3.5,2],[12,5.5,2],[15,3.5,0],[15,5.5,0]])for(const role of ['seat','back'])item('chair',x,y,r,role);
    employees.forEach((employee,index)=>{const col=index%3,row=Math.floor(index/3),x=2.5+col*3.3,y=3+row*2.8;
      item('desk',x,y);item('chair',x,y-1,0,'seat');item('chair',x,y-1,0,'back');
      const point=project(x+.5,y-.5),sprite=art['person-'+look(employee.id)+'-front-sit'];
      const actor={employee,x:x+.5,y:y-.5,point,key:'person-'+look(employee.id)+'-front-',box:{x:x+.32,y:y-.68,w:.36,d:.36,z:12,h:30}};actors.push(actor);items.push(actor);
      const b=document.createElement('button');b.type='button';b.className='studio-desk';b.dataset.employee=employee.id;b.dataset.state=employee.state;b.setAttribute('aria-label',employee.name+' · '+(labels[employee.state]||employee.state)+' · 직원 정보 보기');b.setAttribute('aria-pressed',String(selected===employee.id));
      const dot=document.createElement('span');dot.className='scene-person-dot';dot.setAttribute('aria-hidden','true');const name=document.createElement('strong');name.textContent=employee.name;b.append(dot,name);
      const state=document.createElement('span');state.textContent=labels[employee.state]||employee.state;state.className='scene-person-state';b.append(state);b.addEventListener('click',()=>onSelect(employee.id));layer.append(b);actor.button=b;
      actor.labelY=point.y+2-(12-(sprite?.drop||14))*2-(sprite?.height||70)-6;actor.screenPoint=point;
      if(bubbles<2&&['working','waiting_user','awaiting_approval','conflict','needs_recheck','failed'].includes(employee.state)){bubbles++;
        const bubble=document.createElement('span');bubble.className='scene-bubble';bubble.textContent=employee.state==='working'?(employee.activity||employee.current_job?.title||'업무를 진행하고 있어요'):(employee.state==='waiting_user'?'답변을 기다리고 있어요':labels[employee.state]||employee.state);bubble.title=employee.current_job?.title||bubble.textContent;b.append(bubble);
      }
    });
    const ordered=depthOrder(items),room=art.room||{width:1052,height:640,o:[466,108]},left=-room.o[0],top=-room.o[1];
    function position(){const pad=width<600?12:54;scale=Math.min((width-pad*2)/room.width,(height-pad*2)/room.height)*camera.zoom;scale=Math.max(.15,scale);offset={x:(width-room.width*scale)/2-left*scale+camera.x,y:(height-room.height*scale)/2-top*scale+camera.y};
      for(const a of actors){a.button.style.left=(offset.x+a.point.x*scale)+'px';a.button.style.top=(offset.y+a.labelY*scale)+'px';a.button.style.setProperty('--label-scale',String(Math.max(.72,Math.min(1,scale))));}
    }
    function draw(){if(destroyed)return;position();if(!ctx)return;ctx.setTransform(devicePixelRatio||1,0,0,devicePixelRatio||1,0,0);ctx.clearRect(0,0,width,height);ctx.save();ctx.translate(offset.x,offset.y);ctx.scale(scale,scale);ctx.imageSmoothingEnabled=false;
      if(loaded.room)ctx.drawImage(loaded.room,0,0,room.width,room.height,left,top,room.width,room.height);
      for(const it of ordered){if(it.employee){const active=it.employee.state==='working',pose=active&&Math.floor(Date.now()/550)%2?'type':'sit',key=it.key+pose,sprite=art[key],image=loaded[key];if(sprite&&image){const p=it.point,drop=2-(12-sprite.drop)*2;ctx.drawImage(image,sprite.sx||0,sprite.sy||0,sprite.width,sprite.height,p.x-sprite.width/2,p.y+drop-sprite.height,sprite.width,sprite.height);}
        if(selected===it.employee.id){ctx.strokeStyle='#b5a0ff';ctx.lineWidth=2/scale;ctx.beginPath();ctx.ellipse(it.point.x,it.point.y-18,19,9,0,0,Math.PI*2);ctx.stroke();}}
        else{const sprite=art[it.key],image=loaded[it.key];if(sprite&&image){const p=project(it.x,it.y);ctx.drawImage(image,sprite.sx||0,sprite.sy||0,sprite.width,sprite.height,p.x-sprite.o[0],p.y-sprite.o[1],sprite.width,sprite.height);}}}
      ctx.restore();}
    function resize(){width=host.clientWidth||1000;height=host.clientHeight||650;const ratio=window.devicePixelRatio||1;canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);draw();}
    function animate(){if(destroyed)return;draw();if(actors.some(a=>a.employee.state==='working')&&!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)timer=setTimeout(animate,550);}
    function zoomBy(factor){camera.zoom=Math.max(.65,Math.min(2.5,camera.zoom*factor));draw();}
    function reset(){camera.zoom=1;camera.x=0;camera.y=0;draw();}
    canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,cx:camera.x,cy:camera.y};canvas.setPointerCapture?.(e.pointerId);});
    canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;camera.x=Math.max(-width,Math.min(width,drag.cx+dx));camera.y=Math.max(-height,Math.min(height,drag.cy+dy));draw();});
    const release=()=>{drag=null;};canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);
    canvas.addEventListener('wheel',e=>{e.preventDefault();zoomBy(e.deltaY>0?.9:1.1);},{passive:false});
    const observer=window.ResizeObserver?new ResizeObserver(resize):null;observer?.observe(host);window.addEventListener('resize',resize);resize();
    images().then(value=>{if(destroyed)return;loaded=value;if(!loaded.room||!loaded['desk-0']){host.dataset.failed='true';return;}host.dataset.ready='true';animate();});
    return {zoomBy,reset,destroy(){destroyed=true;clearTimeout(timer);observer?.disconnect();window.removeEventListener('resize',resize);}};
  }
  return {create,look,project,depthOrder};
})();
