import {PaperRenderer} from './paper-renderer.js';
import {PaperSurface} from './paper-surface.js';
import {WallpaperPaint} from './wallpaper-paint.js';
import {PigmentFeedback} from './pigment-feedback.js';
import {PaperDetails} from './paper-details.js';

// The surfaces and painters above are the approved study's implementation.
// This adapter only maps the application's existing DOM and native controls.
const PANEL='.panel,.stat,.roster,.companion-note,.auth-story,.auth-form>div,.config-nav,.quota-summary,.memory-card,.save-bar,.edit-dialog,details';
const ACTION='button,a.primary,a.secondary,a.danger,a.back,.setup-checklist a,.tabs>a,.main-nav>a,.config-nav nav a';
const BADGE='.badge,.signal,.count,.draft-tag';
const INPUT='input[type="checkbox"],input[type="range"],input[type="file"]';
const DISCOVER=[PANEL,ACTION,BADGE,INPUT].join(',');
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const hash=v=>{const n=Math.sin(v*127.1+23.1)*43758.5453;return n-Math.floor(n);};
const own=node=>node instanceof Element&&!!node.closest('[data-paper-owned],.paper-world,.paper-motion');
const prepare=(canvas,w,h)=>{const dpr=Math.min(devicePixelRatio||1,1.25);canvas.width=Math.max(1,Math.round(w*dpr));canvas.height=Math.max(1,Math.round(h*dpr));canvas.getContext('2d')?.setTransform(dpr,0,0,dpr,0,0);};

class NativeControl{
 constructor(input,renderer){
  this.target=input;this.parent=input.parentElement;this.alive=true;this.kind=['range','file'].includes(input.type)?input.type:input.closest('.toggle-row')?'switch':'checkbox';
  this.hostAdded=!this.parent.classList.contains('paper-native-host');this.parent.classList.add('paper-native-host');
  this.node=document.createElement('span');this.node.className='paper-native-plate';this.node.dataset.paperOwned='native';this.node.setAttribute('aria-hidden','true');this.parent.append(this.node);
  input.classList.add('paper-native-input');
  this.active=false;this.fill=0;this.place();
  if(this.kind==='range'||this.kind==='file'){
   this.feedbackCanvas=document.createElement('canvas');this.feedbackCanvas.className='paper-range-pigment';this.node.append(this.feedbackCanvas);this.node.classList.add('paper-'+this.kind+'-plate');prepare(this.feedbackCanvas,this.width,this.height);
  }else{
   this.node.dataset.stock=input.checked?'cream':'recess';this.node.dataset.radius=this.kind==='switch'?'14':'5';this.surface=new PaperSurface(this.node,renderer);this.surface.svg.dataset.paperOwned='surface';this.feedbackCanvas=this.surface.feedbackCanvas;
   input.dataset.paperControl=this.kind;
  }
  this.refresh();
 }
 place(){
  const input=this.target;if(!this.alive)return;
  const file=this.kind==='file',style=file?getComputedStyle(input):null;
  // The native file button has an explicit CSS size. Only its colour plate
  // overlays it; the native button, filename and picker remain untouched.
  const w=file?parseFloat(style.getPropertyValue('--paper-file-width')):input.offsetWidth,h=file?parseFloat(style.getPropertyValue('--paper-file-height')):input.offsetHeight;
  this.node.style.left=input.offsetLeft+(file?input.clientLeft+parseFloat(style.paddingLeft):0)+'px';this.node.style.top=input.offsetTop+(file?input.clientTop+parseFloat(style.paddingTop):0)+'px';this.node.style.width=w+'px';this.node.style.height=h+'px';
  const changed=w!==this.width||h!==this.height;this.width=w;this.height=h;
  if((this.kind==='range'||file)&&this.feedbackCanvas&&changed)prepare(this.feedbackCanvas,w,h);
  if(this.surface){this.surface.resize();this.surface.measure();}
 }
 refresh(){
  if(!this.alive)return;this.node.dataset.disabled=String(this.target.matches(':disabled'));if(this.kind==='file')return;this.node.dataset.checked=String(this.target.checked);
  if(this.kind==='range'){
   const min=Number(this.target.min)||0,max=this.target.max===''?100:Number(this.target.max);this.fill=clamp((Number(this.target.value)-min)/Math.max(.0001,max-min));this.target.style.setProperty('--paper-range-fill',this.fill*100+'%');
  }else{
   const stock=this.target.checked?'cream':'recess';if(this.node.dataset.stock!==stock){this.node.dataset.stock=stock;this.surface.paint();}
  }
 }
 measure(){this.place();this.refresh();}
 step(dt,pointer,light,time,running,paint){if(this.surface)this.surface.step(dt,pointer,light,time,running,paint);}
 destroy(){
  if(!this.alive)return;this.alive=false;this.active=false;this.surface?.destroy();this.feedbackCanvas.width=this.feedbackCanvas.height=0;this.node.remove();
  this.target.classList.remove('paper-native-input');delete this.target.dataset.paperControl;this.target.style.removeProperty('--paper-range-fill');
  if(this.hostAdded&&!this.parent.querySelector('.paper-native-plate'))this.parent.classList.remove('paper-native-host');
 }
}

class PaperRuntime{
 constructor({ground,drawing,motion}){
  this.alive=true;this.life=new AbortController();this.frame=0;this.time=0;this.last=0;this.accumulated=0;this.anchor=performance.now();this.reduced=matchMedia('(prefers-reduced-motion: reduce)');this.running=!this.reduced.matches;this.suspended=!this.running||document.hidden;
  this.pointer={x:-1000,y:-1000,inside:false};this.lastContact=null;this.pointerDown=false;this.surfaces=new Map();this.stamps=new Map();this.pending=new Set([document.body]);this.measureNeeded=true;this.force=true;
  this.ground=ground;this.motion=motion;document.body.classList.add('paper-app');this.renderer=new PaperRenderer();this.feedback=new PigmentFeedback();
  this.drawing=new WallpaperPaint(drawing,()=>{if(this.alive){this.force=true;this.request();}});this.resize();
  this.ro=new ResizeObserver(entries=>{if(!this.alive)return;for(const {target}of entries){const surface=this.surfaces.get(target);surface?.resize?.();}this.measureNeeded=this.force=true;this.request();});
  this.io=new IntersectionObserver(entries=>{if(!this.alive)return;for(const entry of entries){const surface=this.surfaces.get(entry.target);if(surface){surface.visible=entry.isIntersecting;if(entry.isIntersecting){this.measureNeeded=true;const paper=surface.surface||surface;paper.drawn=false;}}}this.request();},{rootMargin:'100px'});
  this.details=new PaperDetails(this);this.bind();this.reconcile();
  this.mo=new MutationObserver(records=>{
   if(!this.alive)return;
   for(const record of records){
    if(own(record.target))continue;
    if(record.type==='attributes'){
     if(record.attributeName==='class'){
      const clean=value=>(value||'').split(/\s+/).filter(c=>!c.startsWith('paper-')&&c!=='stock-ready').sort().join(' ');
      if(clean(record.oldValue)===clean(record.target.getAttribute('class')))continue;
     }
     this.pending.add(record.target);
    }else if(record.type==='characterData'){
     const parent=record.target.parentElement;if(parent?.closest('table,.memory-trend'))this.pending.add(parent);
    }else{
     for(const node of record.addedNodes)if(node instanceof Element&&!own(node))this.pending.add(node);
     // React can replace the text of a button and remove its decoration.
     if(record.removedNodes.length)this.pending.add(record.target);
    }
   }
   if(this.pending.size){this.measureNeeded=true;this.request();}
  });
  this.mo.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeOldValue:true,attributeFilter:['class','disabled','open','d','viewBox']});
  this.syncMotion();document.body.dataset.paper='ready';document.body.dataset.paperRenderer=this.renderer.renderer;this.request();
 }
 on(target,type,fn,options={}){target.addEventListener(type,fn,{...options,signal:this.life.signal});}
 now(){return this.accumulated+(this.suspended?0:(performance.now()-this.anchor)/1000);}
 clock(running=this.running){this.accumulated=this.now();this.anchor=performance.now();this.running=running;this.suspended=!running||document.hidden||this.pageSuspended;this.last=0;}
 suspendPage(value){this.accumulated=this.now();this.anchor=performance.now();this.pageSuspended=value;this.suspended=!this.running||document.hidden||value;this.last=0;}
 request(){if(!this.alive||this.frame||document.hidden||this.pageSuspended)return;this.frame=requestAnimationFrame(now=>this.tick(now));}
 resize(){prepare(this.ground,innerWidth,innerHeight);this.renderer.resize();this.backgroundDrawn=false;}
 decorate(target){
  if((own(target)&&target!==this.motion)||this.surfaces.has(target)||!target.isConnected||target.matches('.skip-link'))return;
  let surface;
  if(target.matches(INPUT))surface=new NativeControl(target,this.renderer);
  else{
   const panel=target.matches(PANEL),radius=panel?4:target.matches(BADGE)?6:target.matches('button.icon')?9:9;
   const cut=panel?(target.matches('.companion-note,.auth-story')?'torn':'paper'):undefined;
   const before={};for(const key of ['stock','radius','cut','relief'])before[key]=target.dataset[key];
   target.dataset.stock='cream';target.dataset.radius=String(radius);target.dataset.relief=panel?'.35':'.7';if(cut)target.dataset.cut=cut;
   target.classList.add('paper-surface');target.classList.add(panel?'paper-panel':target.matches(BADGE)?'paper-badge':'paper-action');
   surface=new PaperSurface(target,this.renderer);surface.target=target;surface.kind=panel?'panel':'action';surface.previous=before;surface.svg.dataset.paperOwned='surface';
   if(target.matches('details'))target.querySelector(':scope>summary')?.prepend(surface.svg);
  }
  surface.visible=true;this.surfaces.set(target,surface);this.ro.observe(target);this.io.observe(target);
  if(target.matches('.companion-note,.auth-story'))this.addStamp(target);
 }
 remove(target,surface){
  this.feedback.remove(surface);this.ro.unobserve(target);this.io.unobserve(target);surface.destroy();
  if(surface.previous){
   for(const [key,value]of Object.entries(surface.previous)){if(value===undefined)delete target.dataset[key];else target.dataset[key]=value;}
   target.classList.remove('paper-surface','paper-panel','paper-action','paper-badge','paper-pressed');target.style.removeProperty('--paper-depth');
  }
  this.surfaces.delete(target);
 }
 reconcile(){
  for(const [target,surface]of this.surfaces){
   if(!target.isConnected||!target.matches(DISCOVER)){this.remove(target,surface);continue;}
   if(surface instanceof NativeControl){if(surface.node.parentElement!==surface.parent)surface.parent.append(surface.node);surface.refresh();}
   else{target.classList.add('paper-surface','stock-ready',surface.kind==='panel'?'paper-panel':target.matches(BADGE)?'paper-badge':'paper-action');const artHost=target.matches('details')?target.querySelector(':scope>summary'):target;if(artHost&&surface.svg.parentElement!==artHost)artHost.prepend(surface.svg);}
  }
  for(const root of this.pending){
   if(!root.isConnected||own(root))continue;
   if(root.matches(DISCOVER))this.decorate(root);
   for(const target of root.querySelectorAll(DISCOVER))this.decorate(target);
  }
  this.pending.clear();
  for(const [target,stamp]of this.stamps){if(!target.isConnected){stamp.canvas.width=stamp.canvas.height=0;stamp.canvas.remove();this.stamps.delete(target);}else if(stamp.canvas.parentElement!==target)target.append(stamp.canvas);}
  this.details.reconcile();
  this.measureNeeded=this.force=true;
 }
 addStamp(target){
  const canvas=document.createElement('canvas');canvas.className='paper-star';canvas.dataset.paperOwned='star';canvas.setAttribute('aria-hidden','true');prepare(canvas,92,92);target.append(canvas);
  const points=[],bayer=[[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]];
  for(let row=0;row<48;row++)for(let col=0;col<48;col++){
   const x=col*1.8+3,y=row*1.8+3,px=x-46,py=y-46,a=Math.atan2(py,px),r=Math.hypot(px,py),edge=24+7*Math.cos(a*5+Math.PI/2);
   if((r<edge&&r>8)||r<4.3)points.push({x,y,threshold:bayer[row%4][col%4]/16,seed:hash(row*48+col)});
  }
  this.stamps.set(target,{canvas,points,key:''});
 }
 measure(){for(const surface of this.surfaces.values())surface.measure();this.details.measure();this.measureNeeded=false;}
 resolve(target){
  if(!(target instanceof Element))return null;
  const input=target.closest(INPUT);if(input)return this.surfaces.get(input);
  return this.surfaces.get(target.closest(DISCOVER));
 }
 print(surface,event){
  if(!surface?.alive||(surface.target||surface.node).matches(':disabled'))return;
  const rect=(surface.kind==='file'?surface.node:surface.target||surface.node).getBoundingClientRect();
  const local=event?.detail>0&&event.clientX>=rect.left&&event.clientX<=rect.right&&event.clientY>=rect.top&&event.clientY<=rect.bottom;
  const x=local?event.clientX-rect.left:rect.width/2,y=local?event.clientY-rect.top:rect.height/2;
  this.feedback.add(surface,{x:x/Math.max(1,rect.width)*surface.width,y:y/Math.max(1,rect.height)*surface.height},this.now(),!this.running);this.force=true;this.request();
 }
 release(){
  this.pointerDown=false;
  for(const surface of this.surfaces.values()){
   const paper=surface.surface||surface;paper.pressed=false;
   if(surface.active)this.feedback.release(surface,this.now());
   (surface.target||surface.node).classList.remove('paper-pressed');
  }
  this.force=true;this.request();
 }
 syncMotion(){
  document.body.dataset.paperMotion=this.running?'on':'off';this.motion.setAttribute('aria-pressed',String(this.running));this.motion.setAttribute('aria-label',this.running?'暂停纸面动态':'开启纸面动态');this.motion.querySelector('span').textContent=this.running?'动态开启':'动态暂停';
 }
 tick(now){
  this.frame=0;if(!this.alive||document.hidden||this.pageSuspended)return;
  if(this.pending.size)this.reconcile();if(this.measureNeeded)this.measure();
  const dt=this.last?Math.min(.08,(now-this.last)/1000):1/60;this.last=now;this.time=this.now();let settling=false;
  if(!this.backgroundDrawn){this.renderer.render(this.ground,{width:innerWidth,height:innerHeight,origin:[0,0],light:[-300,-400],pointer:[-1000,-1000],stock:'ground',relief:0,role:0,time:0,hover:0,press:0,finish:0});this.backgroundDrawn=true;}
  for(const surface of this.surfaces.values()){
   if(!surface.visible)continue;
   const paper=surface.surface||surface;
   const before=[paper.hover,paper.press];surface.step(dt,this.pointer,[-300,-400],this.time,this.running,true);
   if(paper.hover!==undefined){
    if(Math.abs(paper.hover-before[0])>.0001||Math.abs(paper.press-before[1])>.0001)settling=true;
    if(surface.kind==='action')surface.node.style.setProperty('--paper-depth',(paper.press*1.2).toFixed(3)+'px');
    if(paper.brush?.marks.length)settling=true;
   }
  }
  for(const [target,stamp]of this.stamps){
   const surface=this.surfaces.get(target),coverage=.4+(surface?.hover||0)*.5,key=coverage.toFixed(2);if(key===stamp.key)continue;stamp.key=key;
   const ctx=stamp.canvas.getContext('2d');if(!ctx)continue;ctx.clearRect(0,0,92,92);
   for(const p of stamp.points){if(p.threshold>coverage)continue;ctx.fillStyle=`rgba(207,38,41,${(.45+p.seed*.35).toFixed(3)})`;ctx.beginPath();ctx.arc(p.x+(p.seed-.5)*.25,p.y+(hash(p.x)-.5)*.25,.5+p.seed*.26,0,Math.PI*2);ctx.fill();}
  }
  settling=this.details.step(dt,this.pointer,this.time,this.running)||settling;
  this.drawing.draw(this.time);this.feedback.draw(this.time);this.force=false;
  if(this.running&&(settling||this.drawing.marks.length||this.feedback.marks.size||[...this.surfaces.values()].some(s=>s.active)))this.request();
 }
 bind(){
  this.on(document,'pointermove',event=>{
   if(this.measureNeeded)this.measure();this.pointer={x:event.clientX,y:event.clientY,inside:true,quiet:this.details.isQuiet(event.target)};const surface=this.resolve(event.target),time=this.now();
   if(this.lastContact){this.drawing.add(this.lastContact.point,this.pointer,time,this.pointerDown?.65:.3);
    if(!this.pointer.quiet&&surface?.kind==='panel'&&this.lastContact.surface===surface&&!event.target.closest('button,a,input,textarea,select,label'))surface.mark(this.lastContact.point,this.pointer,time,.25);
   }
   this.lastContact={surface,point:{...this.pointer}};this.force=true;this.request();
  },{passive:true});
  this.on(document,'pointerdown',event=>{
   if(event.button!==0)return;this.pointerDown=true;this.pointer={x:event.clientX,y:event.clientY,inside:true,quiet:this.details.isQuiet(event.target)};const surface=this.resolve(event.target);if(!surface)return;
   if(surface.target?.disabled)return;
   const paper=surface.surface||surface;paper.pressed=true;(surface.target||surface.node).classList.add('paper-pressed');
   if(surface.kind==='range'){surface.active=true;surface.refresh();this.print(surface,event);}
   this.force=true;this.request();
  },{passive:true});
  this.on(window,'pointerup',()=>this.release());this.on(window,'pointercancel',()=>this.release());
  this.on(window,'blur',()=>{this.pointer.inside=false;this.lastContact=null;this.release();});
  this.on(document,'pointerout',event=>{if(!event.relatedTarget){this.pointer.inside=false;this.lastContact=null;this.release();}});
  this.on(document,'focusin',event=>{const paper=this.resolve(event.target);if(paper&&!(paper.kind==='panel'&&this.details.isQuiet(event.target)))(paper.surface||paper).focused=true;this.request();});
  this.on(document,'focusout',()=>{for(const surface of this.surfaces.values())(surface.surface||surface).focused=false;this.request();});
  this.on(document,'keydown',event=>{if(!event.repeat&&[' ','Enter'].includes(event.key)&&event.target instanceof Element&&event.target.matches('button,a')){const surface=this.resolve(event.target);if(surface){(surface.surface||surface).pressed=true;this.request();}}});
  this.on(document,'keyup',event=>{if([' ','Enter'].includes(event.key))this.release();});
  this.on(document,'click',event=>{
   if(!(event.target instanceof Element))return;const target=event.target.closest(ACTION+','+BADGE+',input[type="file"]');if(!target||(own(target)&&target!==this.motion))return;
   // Observe the native action once; never synthesize a business click.
   this.print(this.surfaces.get(target),event);
  });
  this.on(document,'change',event=>{if(event.target instanceof HTMLInputElement&&event.target.type==='checkbox'){const surface=this.surfaces.get(event.target);surface?.refresh();this.print(surface);}});
  this.on(document,'input',event=>{if(event.target instanceof HTMLInputElement&&event.target.type==='range'){const surface=this.surfaces.get(event.target);surface?.refresh();this.print(surface);}});
  this.on(this.motion,'click',()=>{this.clock(!this.running);this.syncMotion();this.request();});
  this.on(this.reduced,'change',event=>{this.clock(!event.matches);this.syncMotion();this.request();});
  this.on(window,'scroll',()=>{this.lastContact=null;this.measureNeeded=true;this.request();},{passive:true,capture:true});
  this.on(window,'resize',()=>{this.lastContact=null;this.feedback.resize();this.resize();this.drawing.resize(innerWidth,innerHeight);this.measureNeeded=true;this.request();},{passive:true});
  this.on(document,'visibilitychange',()=>{this.clock();if(document.hidden){this.release();cancelAnimationFrame(this.frame);this.frame=0;}else{this.measureNeeded=true;this.request();}});
  this.on(this.renderer.canvas,'webglcontextlost',event=>{event.preventDefault();this.renderer.releaseGL();this.renderer.renderer='canvas2d';document.body.dataset.paperRenderer='canvas2d';this.backgroundDrawn=false;for(const surface of this.surfaces.values())(surface.surface||surface).drawn=false;this.request();});
  this.on(window,'pagehide',event=>{if(event.persisted){this.suspendPage(true);this.release();cancelAnimationFrame(this.frame);this.frame=0;}else this.destroy();});
  this.on(window,'pageshow',event=>{if(event.persisted){this.suspendPage(false);this.measureNeeded=true;this.request();}});
 }
 destroy(){
  if(!this.alive)return;this.alive=false;this.life.abort();cancelAnimationFrame(this.frame);this.frame=0;this.mo?.disconnect();this.ro.disconnect();this.io.disconnect();
  this.details.destroy();
  for(const [target,surface]of this.surfaces)this.remove(target,surface);this.feedback.destroy();this.drawing.destroy();this.renderer.destroy();this.ground.width=this.ground.height=0;
  for(const stamp of this.stamps.values()){stamp.canvas.width=stamp.canvas.height=0;stamp.canvas.remove();}this.stamps.clear();this.pending.clear();
  document.body.classList.remove('paper-app');delete document.body.dataset.paper;delete document.body.dataset.paperMotion;delete document.body.dataset.paperRenderer;
 }
}
export function mountPaperExperience(elements){const runtime=new PaperRuntime(elements);return()=>runtime.destroy();}
