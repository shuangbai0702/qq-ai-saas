import {drawControlStage as drawSoftStage,drawControlAmbient as drawSoftAmbient} from './control-art.js';
const TAU=Math.PI*2;
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const palettes={
  tide:{ink:'#43628b',line:'#b3c8e2',accent:'#709ad0',gold:'#96afd0',surface:'#fff',base:'#eaf2fc',soft:'#e2edf9',text:'#9bb0cc'},
  post:{ink:'#35466c',line:'#8794ab',accent:'#b35b46',gold:'#bb9659',surface:'#fbf5e9',base:'#e9e5d8',soft:'#d9deea',text:'#737e93'},
  bloom:{ink:'#d4dfd7',line:'#758480',accent:'#d2aa83',gold:'#a6c2ae',surface:'#303737',base:'#252c2c',soft:'#3c4743',text:'#a7b4ae'}
};
function rng(seed){return()=>{seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
function line(c,pts,color,width=1,dash=[]){c.beginPath();pts.forEach((v,i)=>c[i?'lineTo':'moveTo'](v[0],v[1]));c.strokeStyle=color;c.lineWidth=width;c.setLineDash(dash);c.stroke();c.setLineDash([]);}
function dot(c,x,y,r,fill,stroke){c.beginPath();c.arc(x,y,r,0,TAU);if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.stroke();}}
function text(c,value,x,y,color,size=9,align='left',mono=false){c.font=`${size}px ${mono?'"Consolas",monospace':'"Segoe UI","Microsoft YaHei",sans-serif'}`;c.textAlign=align;c.fillStyle=color;c.fillText(value,x,y);}
function plate(c,x,y,w,h,p,r=7,selected=false){
  c.save();c.shadowColor='#1b261812';c.shadowBlur=8;c.shadowOffsetY=3;c.fillStyle=p.surface;c.beginPath();c.roundRect(x,y,w,h,r);c.fill();c.restore();
  c.lineWidth=1;c.strokeStyle=selected?p.accent:p.line+'55';c.beginPath();c.roundRect(x+.5,y+.5,w-1,h-1,r);c.stroke();
  c.strokeStyle=p.ink+'0e';c.beginPath();c.roundRect(x+3.5,y+3.5,w-7,h-7,Math.max(2,r-2));c.stroke();
  line(c,[[x+7,y+1.5],[x+w-7,y+1.5]],'#ffffff42',.7);
}
export class Studio{
  constructor(stage,ambient){
    this.stage=stage;this.ambient=ambient;this.sc=stage.getContext('2d');this.ac=ambient.getContext('2d');this.mode='tide';this.t=0;this.last=0;this.seed=283;this.energy=.48;this.density=.65;this.warmth=.72;this.flow=0;this.alive=true;this.dirty=true;
    this.nodes=[{x:.19,y:.29},{x:.80,y:.25},{x:.80,y:.63}];this.routes=[true,true,true];
    this.context={source:'personality',target:'hero',phase:'idle',phaseIndex:-1,selected:0,online:true,night:false,pinned:[],memories:[],activity:0};this.anchors={};this.signals=[];this.frameListeners=new Set();this.cursor={x:0,y:0};
    this.reduced=matchMedia('(prefers-reduced-motion: reduce)');this.running=!this.reduced.matches;
    this.resize=()=>{for(const cv of [stage,ambient]){const r=cv.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,1.6);cv.width=Math.round(r.width*d);cv.height=Math.round(r.height*d);cv.getContext('2d').setTransform(d,0,0,d,0,0);}this.dirty=true;};
    this.ro=new ResizeObserver(this.resize);this.ro.observe(stage);this.ro.observe(ambient);
    this.pointer=e=>{const r=stage.getBoundingClientRect();this.cursor.x=clamp((e.clientX-r.left)/r.width,0,1);this.cursor.y=clamp((e.clientY-r.top)/r.height,0,1);this.cursor.vx=e.clientX;this.cursor.vy=e.clientY;this.dirty=true;};
    this.click=e=>{if(e.target===stage){const r=stage.getBoundingClientRect();this.onPress?.(e.clientX-r.left,e.clientY-r.top);}};
    document.addEventListener('pointermove',this.pointer,{passive:true});document.addEventListener('pointerdown',this.click);
    this.visibility=()=>{this.last=0;if(!document.hidden&&!this.frame&&this.alive)this.frame=requestAnimationFrame(t=>this.tick(t));};document.addEventListener('visibilitychange',this.visibility);
    this.onReduced=e=>{if(e.matches)this.setRunning(false);};this.reduced.addEventListener('change',this.onReduced);
    this.reset();this.resize();this.frame=requestAnimationFrame(t=>this.tick(t));
  }
  reset(){const r=rng(++this.seed);this.samples=Array.from({length:128},()=>r());this.dirty=true;}
  setMode(v){this.mode=v;this.dirty=true;}
  setRunning(v){this.running=v;this.last=0;this.dirty=true;}
  setEnergy(v){this.energy=v;this.dirty=true;}
  setDensity(v){this.density=v;this.dirty=true;}
  setWarmth(v){this.warmth=v;this.dirty=true;}
  setMood(){this.flow=.7;this.dirty=true;}
  setContext(v){Object.assign(this.context,v);this.dirty=true;}
  setAnchors(v){this.anchors=v;this.dirty=true;}
  signal(source,target){this.context.source=source;this.context.target=target;this.signals.push({source,target,at:this.t});this.signals=this.signals.slice(-5);this.dirty=true;}
  feed(){this.flow=1;this.dirty=true;}
  onFrame(fn){this.frameListeners.add(fn);return()=>this.frameListeners.delete(fn);}
  tick(now){
    this.frame=0;if(!this.alive||document.hidden)return;const dt=this.last?Math.min((now-this.last)/1000,.035):.016;this.last=now;
    if(this.running){this.t+=dt;this.flow=Math.max(0,this.flow-dt*.6);this.signals=this.signals.filter(v=>this.t-v.at<3);if(this.mode==='tide'||this.context.phaseIndex>=0||this.flow||this.signals.length)this.dirty=true;}
    this.frameListeners.forEach(fn=>fn(dt,this.running));if(this.dirty){this.drawAmbient();this.drawStage();this.dirty=false;}
    this.frame=requestAnimationFrame(t=>this.tick(t));
  }
  drawAmbient(){
    if(this.mode==='tide'){drawSoftAmbient(this);return;}
    const c=this.ac,w=this.ambient.clientWidth,h=this.ambient.clientHeight,p=palettes[this.mode],a=this.anchors[this.context.source],b=this.anchors[this.context.target]||this.anchors.hero;c.clearRect(0,0,w,h);c.lineWidth=.7;
    if(!a||!b)return;const right=a.x+a.width/2>w/2,rail=right?Math.min(w-10,a.x+a.width+11):Math.max(10,a.x-11),sx=right?a.x+a.width:a.x,sy=a.y+a.height*.42,tx=right?b.x+b.width:b.x,ty=clamp(b.y+b.height*.56,-90,h+90);
    for(let k=0;k<(this.mode==='tide'?3:1);k++){const off=k*3*(right?1:-1);c.beginPath();c.moveTo(sx,sy);c.bezierCurveTo(rail+off,sy,rail+off,ty,tx,ty);c.strokeStyle=p.line+(k?'25':'70');c.setLineDash(this.mode==='post'?[2,4]:[]);c.stroke();}c.setLineDash([]);
    dot(c,sx,sy,2.5,p.accent);dot(c,tx,ty,3,p.surface,p.line+'90');
    const last=this.signals.at(-1);if(last&&this.context.online){const u=this.running?clamp((this.t-last.at)/2,0,1):.6,v=1-u;if(u<1)dot(c,v*v*v*sx+3*v*v*u*rail+3*v*u*u*rail+u*u*u*tx,v*v*v*sy+3*v*v*u*sy+3*v*u*u*ty+u*u*u*ty,3,p.accent);}
    if(a.y<h&&a.y+a.height>0){c.strokeStyle=p.ink+'20';c.beginPath();c.roundRect(a.x-4,a.y-4,a.width+8,a.height+8,8);c.stroke();}
  }
  grid(c,w,h,p,polar=false){
    c.save();c.fillStyle=p.line+'35';for(let x=20;x<w;x+=24)for(let y=18;y<h;y+=24)c.fillRect(x,y,.65,.65);c.restore();
    if(polar){const r=Math.min(w*.30,h*.40);for(let i=0;i<4;i++){c.beginPath();c.ellipse(w*.49,h*.43,r*(.5+i*.24),r*(.5+i*.24),0,0,TAU);c.strokeStyle=p.line+'20';c.lineWidth=.7;c.stroke();}}
  }
  drawStage(){
    if(this.mode==='tide'){drawSoftStage(this);return;}
    const c=this.sc,w=this.stage.clientWidth,h=this.stage.clientHeight,p=palettes[this.mode];c.clearRect(0,0,w,h);this.grid(c,w,h,p,this.mode==='post');
    if(this.mode==='post')this.enamel(c,w,h,p);else if(this.mode==='bloom')this.graphite(c,w,h,p);else this.paper(c,w,h,p);
    this.memorySlots(c,w,h,p);
    text(c,this.context.online?'LOCAL SIMULATION / '+(this.context.phase==='idle'?'READY':this.context.phase):'INSTANCE STOPPED',22,h-47,p.text,8,'left',true);
    if(this.context.night){c.fillStyle=this.mode==='bloom'?'#13221f17':'#79613b0b';c.fillRect(0,0,w,h);}
  }
  connector(c,points,p,active=false,index=0){
    c.save();c.lineJoin='round';c.lineCap='round';line(c,points,active?p.accent:p.line+'65',active?1.8:1,active?[]:[3,4]);
    points.slice(1,-1).forEach(([x,y])=>dot(c,x,y,2,p.surface,p.line+'90'));
    if(active&&this.context.online){const lens=points.slice(1).map((v,i)=>Math.hypot(v[0]-points[i][0],v[1]-points[i][1])),total=lens.reduce((a,b)=>a+b,0);let distance=((this.running?this.t*.35+index*.2:.6)%1)*total;for(let i=0;i<lens.length;i++){if(distance<=lens[i]){const u=distance/lens[i];dot(c,points[i][0]+(points[i+1][0]-points[i][0])*u,points[i][1]+(points[i+1][1]-points[i][1])*u,3.5,p.accent);break;}distance-=lens[i];}}
    c.restore();
  }
  core(c,x,y,size,p){
    const s=size/100;c.save();c.translate(x,y);c.scale(s,s);
    for(let i=0;i<8;i++){const v=-35+i*10;line(c,[[-57,v],[-48,v]],p.line+'95',2);line(c,[[48,v],[57,v]],p.line+'95',2);line(c,[[v,-57],[v,-48]],p.line+'95',2);line(c,[[v,48],[v,57]],p.line+'95',2);}
    plate(c,-48,-48,96,96,p,14,this.context.phaseIndex>=0);
    const g=c.createLinearGradient(-30,-30,35,35);g.addColorStop(0,p.soft);g.addColorStop(1,p.base);c.fillStyle=g;c.beginPath();c.roundRect(-35,-35,70,70,9);c.fill();c.strokeStyle=p.line+'65';c.stroke();
    for(let i=0;i<16;i++){const col=i%4,row=Math.floor(i/4),active=i<(this.context.phaseIndex+1)*4;c.fillStyle=active?p.accent:(this.samples[i]>.45?p.ink+'26':p.line+'30');c.fillRect(-22+col*12,-25+row*10,7,4);}
    text(c,this.context.online?'CORE':'STOP',0,30,p.ink,9,'center',true);
    for(const dx of [-40,40])for(const dy of [-40,40]){dot(c,dx,dy,1.6,p.line+'70');line(c,[[dx-1,dy+1],[dx+1,dy-1]],p.surface,.6);}c.restore();
  }
  module(c,x,y,label,sub,p,active=false,width=106){
    plate(c,x-width/2,y-27,width,54,p,6,active);dot(c,x-width/2+12,y-12,2.3,active?p.accent:p.line);
    text(c,label,x-width/2+22,y-9,p.ink,9);text(c,sub,x-width/2+12,y+14,p.text,7,'left',true);
    for(let i=0;i<5;i++)line(c,[[x+width/2-20+i*2,y+9],[x+width/2-20+i*2,y+17]],p.line+'80',.65);
  }
  paper(c,w,h,p){
    const cx=w*.50,cy=h*.43,s=Math.min(w/700,1),left=w*.15,right=w*.84,top=h*.18,low=h*.62,step=this.context.phaseIndex;
    const routes=[[[left+48,cy],[cx-80,cy],[cx-80,top],[cx-7,top],[cx-7,cy-54]],[[cx+54,cy],[right-25,cy],[right-25,top+20]],[[cx,cy+54],[cx,low],[right-18,low],[right-18,cy+45]]];
    routes.forEach((r,i)=>this.connector(c,r,p,step===i||this.flow>.2,i));
    this.module(c,left,cy,'协议接入','QQ / ONEBOT',p,step===0,Math.max(82,104*s));this.module(c,cx-7,top,'上下文构建','MEMORY + POLICY',p,step===1,Math.max(100,126*s));this.module(c,right,top+20,'统一投递','OUTPUT PLAN',p,step===2,Math.max(82,104*s));
    this.core(c,cx,cy,Math.max(69,105*s),p);
    const gx=w*.70,gy=h*.51;plate(c,gx-40,gy,100,66,p,4);text(c,'CAPABILITY MAP',gx-29,gy+15,p.text,6,'left',true);
    for(let i=0;i<3;i++){const y=gy+28+i*11;dot(c,gx-27,y,2,this.routes[i]?p.ink:p.line+'60');line(c,[[gx-18,y],[gx+30,y]],this.routes[i]?p.line:p.line+'30',1);text(c,this.routes[i]?'ON':'OFF',gx+37,y+2,p.text,5,'left',true);}
    for(let i=0;i<5;i++){const y=cy+83+i*4;line(c,[[w*.12,y],[w*.27+(this.samples[i]*.08)*w,y]],p.line+'35',.7);}
    text(c,'01 / INPUT',w*.08,h*.09,p.text,7,'left',true);text(c,'STATE → CONTEXT → OUTPUT',w*.62,h*.09,p.text,7,'left',true);
  }
  enamel(c,w,h,p){
    const x=w*.49,y=h*.43,r=Math.min(w*.24,h*.31),step=this.context.phaseIndex;c.save();c.translate(x,y);
    const g=c.createLinearGradient(-r,-r,r,r);g.addColorStop(0,'#fffaf1');g.addColorStop(.4,p.base);g.addColorStop(1,'#e2dccc');c.fillStyle=g;c.beginPath();c.arc(0,0,r*.93,0,TAU);c.fill();
    for(let i=0;i<80;i++){const a=i/80*TAU,inner=r*(i%5===0?1.02:1.06);line(c,[[Math.cos(a)*inner,Math.sin(a)*inner],[Math.cos(a)*1.1*r,Math.sin(a)*1.1*r]],p.ink+(i%5===0?'70':'28'),i%5===0?1:.7);}
    for(let j=0;j<3;j++){c.beginPath();c.arc(0,0,r*(.97-j*.045),0,TAU);c.strokeStyle=p.line+(j%2?'30':'60');c.lineWidth=.8;c.stroke();}
    c.beginPath();c.arc(0,0,r*.82,-Math.PI/2,-Math.PI/2+TAU*(this.context.phaseIndex+1)/4);c.strokeStyle=p.accent+'b0';c.lineWidth=3;c.stroke();c.restore();
    this.core(c,x,y,Math.min(103,r*1.08),p);text(c,String(this.context.activity).padStart(3,'0')+' / OPS',x,y+r*.68,p.ink,9,'center',true);
    this.nodes.forEach((n,i)=>{const nx=n.x*w,ny=n.y*h;c.beginPath();c.moveTo(nx,ny);c.bezierCurveTo(x,ny,nx,y,x,y);c.lineWidth=step===i?1.6:1;c.setLineDash([3,4]);c.strokeStyle=(step===i?p.accent:p.ink)+(this.routes[i]?'75':'18');c.stroke();c.setLineDash([]);if(this.routes[i]&&this.context.online&&(step===i||this.flow>.2)){const u=(this.running?this.t*.5+i*.2:.6)%1,v=1-u;dot(c,v*v*v*nx+3*v*v*u*x+3*v*u*u*nx+u*u*u*x,v*v*v*ny+3*v*v*u*ny+3*v*u*u*y+u*u*u*y,3.5,p.accent);}});
    plate(c,w*.06,h*.09,110,28,p,2);text(c,'CONTROL / 002',w*.06+12,h*.09+17,p.ink,8,'left',true);
    const yy=h*.67;for(let i=0;i<8;i++){const xx=w*.07+i*6;c.fillStyle=i<(this.context.phaseIndex+1)*2?p.accent:p.line+'40';c.fillRect(xx,yy,3,7+this.samples[i]*12);}text(c,'ROUTE ACTIVITY',w*.07,yy+32,p.text,6,'left',true);
  }
  graphite(c,w,h,p){
    const cx=w*.50,cy=h*.41,s=Math.min(w/720,1),mods=[['QQ',.14,.24],['LEASE',.15,.54],['SUMMARY',.35,.16],['POLICY',.68,.16],['MODEL',.85,.30],['OUTPUT',.84,.57]];
    mods.forEach(([name,px,py],i)=>{const x=w*px,y=h*py,active=this.context.phaseIndex===i%3;this.connector(c,[[x,y],[x,cy],[cx,cy]],p,active,i);this.module(c,x,y,name,['BOUND','VALID','READY'][i%3]+' / DEMO',p,active,Math.max(69,93*s));});
    this.core(c,cx,cy,Math.max(78,112*s),p);
    const x=w*.34,y=h*.62,ww=w*.32;plate(c,x,y,ww,41,p,4);for(let i=0;i<24;i++){const value=this.samples[i]*(.6+this.warmth*.4),bh=6+value*17;c.fillStyle=i%7===this.context.phaseIndex?p.accent:p.line+'75';c.fillRect(x+9+i*(ww-18)/24,y+31-bh,Math.max(2,(ww-18)/24-2),bh);}
    text(c,'CONTEXT WINDOW',x,y-7,p.text,6,'left',true);text(c,'ENABLED '+this.routes.filter(Boolean).length+'/3',w*.79,h*.09,p.text,7,'left',true);
  }
  memorySlots(c,w,h,p){
    const cardW=Math.min(w*.18,142),y=h*.79;for(let i=0;i<4;i++){const x=w*(.17+i*.22),active=i===this.context.selected,m=this.context.memories[i];plate(c,x-cardW/2,y-16,cardW,32,p,4,active);dot(c,x-cardW/2+9,y,2,active?p.accent:p.line);text(c,(m?.name||'索引记录').slice(0,7),x-cardW/2+16,y+3,active?p.ink:p.text,w<500?6:8);if(this.context.pinned.includes(i))text(c,'+',x+cardW/2-8,y+3,p.gold,10,'center',true);}
  }
  destroy(){if(!this.alive)return;this.alive=false;cancelAnimationFrame(this.frame);this.frameListeners.clear();this.ro.disconnect();document.removeEventListener('pointermove',this.pointer);document.removeEventListener('pointerdown',this.click);document.removeEventListener('visibilitychange',this.visibility);this.reduced.removeEventListener('change',this.onReduced);}
}
export {palettes,clamp};
