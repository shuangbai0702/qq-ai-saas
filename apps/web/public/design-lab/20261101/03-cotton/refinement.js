const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const hash=n=>{const x=Math.sin(n*127.1+31.7)*43758.5453;return x-Math.floor(x);};
const ease=x=>{x=clamp(x);return x*x*(3-2*x);};
const TAU=Math.PI*2;

function clipToPaper(rect,node){
 const box={left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom};
 for(let parent=node instanceof Element?node:node?.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
  const s=getComputedStyle(parent),r=parent.getBoundingClientRect();
  if(/^(auto|scroll|hidden|clip)$/.test(s.overflowX)){box.left=Math.max(box.left,r.left+parent.clientLeft);box.right=Math.min(box.right,r.left+parent.clientLeft+parent.clientWidth);}
  if(/^(auto|scroll|hidden|clip)$/.test(s.overflowY)){box.top=Math.max(box.top,r.top+parent.clientTop);box.bottom=Math.min(box.bottom,r.top+parent.clientTop+parent.clientHeight);}
 }
 return box;
}

// All painters borrow the study's clock and listeners. No independent RAF/timer.
class Layer{
 constructor(node,name){
  this.owned=!(node instanceof HTMLCanvasElement);this.canvas=this.owned?document.createElement('canvas'):node;
  if(this.owned){this.canvas.className=name;this.canvas.setAttribute('aria-hidden','true');node.append(this.canvas);}
  this.ctx=this.canvas.getContext('2d');this.width=this.height=0;
 }
 resize(width,height){
  const w=Math.max(1,Math.round(width)),h=Math.max(1,Math.round(height)),scale=Math.min(devicePixelRatio||1,1.25);
  if(w===this.width&&h===this.height&&scale===this.scale)return false;
  this.width=w;this.height=h;this.scale=scale;this.canvas.width=Math.round(w*scale);this.canvas.height=Math.round(h*scale);this.ctx?.setTransform(scale,0,0,scale,0,0);return true;
 }
 clear(){this.ctx?.clearRect(0,0,this.width,this.height);}
 destroy(){this.canvas.width=this.canvas.height=0;this.ctx=null;if(this.owned)this.canvas.remove();}
}
function fibreLine(ctx,points,colour,width=1,seed=1){
 if(points.length<2)return;
 const alpha=ctx.globalAlpha;
 ctx.strokeStyle=colour;ctx.lineWidth=width;ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();
 points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.stroke();
 ctx.lineWidth=.3;
 for(let strand=0;strand<3;strand++){
  ctx.beginPath();points.forEach((p,i)=>{const x=p.x+(hash(i*13+seed+strand)-.5)*.55,y=p.y+(hash(i*17+seed+strand*8)-.5)*1.1; i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.globalAlpha=alpha*.22;ctx.stroke();
 }
 ctx.globalAlpha=alpha;
}
function openPrint(width,height,seed,kind){
 const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.ceil(width));canvas.height=Math.max(1,Math.ceil(height));
 const c=canvas.getContext('2d');if(!c)return canvas;
 const image=c.createImageData(canvas.width,canvas.height),pixels=image.data,w=canvas.width,h=canvas.height;
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){
  const grain=hash(x*19+y*97+seed*411),fibre=hash(Math.floor(x/5)*53+y*11+seed),edge=Math.min(y,h-1-y);
  let density;
  if(kind==='row'){
   // A continuous, light impression below the text; no rectangular text masks.
   density=Math.exp(-Math.pow((x-31)/78,2)-Math.pow((y-h*.67)/(h*.32),2))*.21;
   density+=Math.exp(-Math.pow((x-w+28)/103,2)-Math.pow((y-h*.43)/(h*.29),2))*.12;
   if(grain>.73)continue;
  }else{
   // An open-centred proofing band, with two imperfectly registered ink edges.
   density=.035+Math.exp(-Math.pow((edge-.8)/1.3,2))*.48;
   if(grain>.69)continue;
  }
  const i=(y*w+x)*4,cool=kind==='row'?x>w*.65:edge<2&&hash(Math.floor(x/29)+seed)>.61;
  const colour=cool?[102,128,121]:[185,65,70];
  pixels[i]=colour[0];pixels[i+1]=colour[1];pixels[i+2]=colour[2];pixels[i+3]=Math.round(255*density*(.36+fibre*.64)*(grain>.14?1:.2));
 }
 c.putImageData(image,0,0);return canvas;
}

class TableImpression{
 constructor(owner,node){
  this.node=node;this.layer=new Layer(node.querySelector('.table-ink'));this.rows=[...node.querySelectorAll('tbody tr')].map((node,i)=>({node,seed:i+31,amount:0}));
  owner.on(node.querySelector('.table-scroll'),'scroll',()=>{owner.forcePaint=true;owner.request();},{passive:true});
 }
 resize(){
  this.layer.resize(this.node.clientWidth,this.node.clientHeight);
  for(const row of this.rows){
   const r=row.node.getBoundingClientRect(),w=Math.round(r.width),h=Math.round(r.height);if(row.source&&row.width===w&&row.height===h)continue;
   if(row.source)row.source.width=row.source.height=0;row.width=w;row.height=h;
   row.source=openPrint(w,h,row.seed,'row');
  }
 }
 step(dt,pointer,running,paint){
  const l=this.layer,c=l.ctx,outer=this.node.getBoundingClientRect(),blend=running?1-Math.exp(-dt*13):1;if(paint)l.clear();
  for(const row of this.rows){
   const r=row.node.getBoundingClientRect(),inside=pointer.inside&&pointer.x>=Math.max(r.left,outer.left)&&pointer.x<=Math.min(r.right,outer.right)&&pointer.y>=r.top&&pointer.y<=r.bottom;
   row.amount+=(Number(inside||row.node===document.activeElement)-row.amount)*blend;
   if(!paint||!c||row.amount<.003)continue;c.save();c.globalAlpha=row.amount;c.drawImage(row.source,r.left-outer.left,r.top-outer.top,r.width,r.height);c.restore();
  }
 }
 destroy(){for(const row of this.rows)if(row.source)row.source.width=row.source.height=0;this.rows=[];this.layer.destroy();}
}

class InkPlot{
 constructor(canvas){this.layer=new Layer(canvas);this.base=document.createElement('canvas');this.amount=0;this.key='';}
 resize(){
  if(!this.layer.resize(this.layer.canvas.clientWidth,this.layer.canvas.clientHeight))return;
  const {width:w,height:h}=this.layer;this.base.width=this.layer.canvas.width;this.base.height=this.layer.canvas.height;
  const c=this.base.getContext('2d');if(!c)return;c.setTransform(this.layer.scale,0,0,this.layer.scale,0,0);
  const red=[.26,.35,.31,.52,.47,.58,.43,.63,.75,.65,.78,.87,.78,.9],grey=[.16,.23,.27,.23,.34,.3,.4,.45,.39,.51,.48,.57,.53,.64];
  this.lines=[red,grey].map(values=>values.map((v,i)=>({x:30+i/(values.length-1)*(w-43),y:h-28-v*(h-49)})));
  c.font='8px Aptos, Segoe UI, sans-serif';c.fillStyle='#a1a192';c.textAlign='right';
  for(let i=1;i<4;i++){
   const y=h-28-i/4*(h-49);c.fillText(String(i*25),21,y+3);c.strokeStyle='#a9a99a';c.globalAlpha=.24;c.lineWidth=.55;c.setLineDash([1.2,3.6]);c.beginPath();c.moveTo(30,y);c.lineTo(w-12,y);c.stroke();
  }
  c.globalAlpha=1;c.setLineDash([]);fibreLine(c,[{x:30,y:12},{x:30,y:h-28},{x:w-12,y:h-28}],'#bebeb0',.65,11);
  c.textAlign='center';for(let i=0;i<4;i++)c.fillText(String(i*4).padStart(2,'0'),30+i/3*(w-43),h-9);
  this.colours=['#bb3037','#929287'];
  // The print process is shared; the two sample colours remain distinct.
  for(const [index,line]of this.lines.entries()){
   const colour=this.colours[index],rgb=[1,3,5].map(i=>parseInt(colour.slice(i,i+2),16)),transfer=rgb.map((v,i)=>Math.round(v*.3+[255,254,251][i]*.7));
   fibreLine(c,line.map(p=>({x:p.x,y:p.y+.7})),`rgb(${transfer})`,1.9,35);
   for(let i=1;i<line.length;i++){
    const a=line[i-1],b=line[i],length=Math.hypot(b.x-a.x,b.y-a.y);
    for(let d=0;d<length;d+=.65){const t=d/length,seed=hash(d*13+i*71);c.fillStyle=`rgba(${rgb},${.25+seed*.42})`;c.beginPath();c.arc(a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t,.48+seed*.5,0,TAU);c.fill();}
   }
   for(const [i,p]of line.entries()){if(i%3&&i!==line.length-1)continue;c.fillStyle='#fffefb';c.strokeStyle=colour;c.lineWidth=.85;c.setLineDash([.7,.35]);c.beginPath();c.arc(p.x,p.y,2.6,0,TAU);c.fill();c.stroke();c.setLineDash([]);}
  }
  this.key='';
 }
 step(dt,pointer,running,paint){
  const l=this.layer,c=l.ctx,r=l.canvas.getBoundingClientRect(),inside=pointer.inside&&pointer.x>=r.left&&pointer.x<=r.right&&pointer.y>=r.top&&pointer.y<=r.bottom;
  this.amount+=(Number(inside)-this.amount)*(running?1-Math.exp(-dt*11):1);if(!paint||!c||!this.lines)return;
  const local=clamp(pointer.x-r.left,30,l.width-13),key=local.toFixed(1)+'/'+(pointer.y-r.top).toFixed(1)+'/'+this.amount.toFixed(3);if(key===this.key)return;this.key=key;
  l.clear();c.drawImage(this.base,0,0,l.width,l.height);if(this.amount<.002)return;
  let p,distance=Infinity;for(const [series,line]of this.lines.entries()){const index=clamp((local-30)/(l.width-43)*(line.length-1),0,line.length-1.001),a=line[Math.floor(index)],b=line[Math.floor(index)+1],y=a.y+(b.y-a.y)*(index%1),d=Math.abs(pointer.y-r.top-y);if(d<distance){p={x:local,y,colour:this.colours[series]};distance=d;}}
  c.save();c.globalAlpha=this.amount;
  // A small dry impression follows the ink. Its registration never moves the line.
  for(let y=-14;y<=14;y+=2.4)for(let x=-31;x<=31;x+=2.4){const seed=hash(x*31+y*83),distance=x*x/961+y*y/196;if(distance>1||seed<.28)continue;c.fillStyle=`rgba(200,71,70,${(1-distance)*.17})`;c.beginPath();c.arc(p.x+x,p.y+y,.35+seed*.3,0,TAU);c.fill();}
  c.strokeStyle=p.colour;c.lineWidth=.65;c.setLineDash([1.2,1.8]);c.beginPath();c.arc(p.x,p.y,5.4,0,TAU);c.stroke();c.setLineDash([]);
  c.fillStyle=p.colour;c.beginPath();c.arc(p.x,p.y,1.5,0,TAU);c.fill();c.restore();
 }
 destroy(){this.layer.destroy();this.base.width=this.base.height=0;this.lines=[];}
}

class SelectionInk{
 constructor(owner){
  this.owner=owner;this.layer=new Layer(document.body,'selection-ink');this.items=[];this.signature='';this.active=false;this.started=0;this.ended=0;this.dirty=true;this.contrast=matchMedia('(forced-colors: active)');
  for(const type of ['selectionchange','select','keyup','pointerup'])owner.on(document,type,()=>{this.dirty=true;owner.forcePaint=true;owner.request();});
  owner.on(this.contrast,'change',()=>{this.dirty=true;owner.forcePaint=true;owner.request();});
 }
 nativeRects(input){
  const start=input.selectionStart,end=input.selectionEnd;if(start===null||end===null||start===end||input.type==='password')return [];
  const r=input.getBoundingClientRect(),s=getComputedStyle(input),mirror=document.createElement('div'),span=document.createElement('span');
  const line=parseFloat(s.lineHeight)||parseFloat(s.fontSize)*1.4,multi=input.tagName==='TEXTAREA';
  mirror.setAttribute('aria-hidden','true');Object.assign(mirror.style,{position:'fixed',visibility:'hidden',pointerEvents:'none',boxSizing:'border-box',font:s.font,letterSpacing:s.letterSpacing,lineHeight:line+'px',width:input.clientWidth+'px',padding:multi?s.padding:'0',whiteSpace:multi?'pre-wrap':'pre',overflowWrap:multi?'break-word':'normal',left:r.left+(multi?0:parseFloat(s.paddingLeft))-input.scrollLeft+'px',top:r.top+(multi?0:(r.height-line)/2)-input.scrollTop+'px'});
  span.textContent=input.value.slice(start,end);mirror.append(document.createTextNode(input.value.slice(0,start)),span,document.createTextNode(input.value.slice(end)));document.body.append(mirror);
  try{const range=document.createRange();range.selectNodeContents(span);return [...range.getClientRects()].map(b=>({left:Math.max(b.left,r.left+2),top:Math.max(b.top,r.top+2),right:Math.min(b.right,r.right-2),bottom:Math.min(b.bottom,r.bottom-2)}));}
  finally{mirror.remove();}
 }
 refresh(time,running){
  this.dirty=false;let rects=[],anchor;const input=document.activeElement,selection=getSelection();
  if(!this.contrast.matches){
   if(input instanceof HTMLInputElement||input instanceof HTMLTextAreaElement){rects=this.nativeRects(input);anchor=input;}
   else if(selection&&!selection.isCollapsed&&selection.rangeCount){const range=selection.getRangeAt(0);rects=[...range.getClientRects()];anchor=range.commonAncestorContainer;}
  }
  const merged=[];
  for(const raw of rects.slice(0,200)){
   const r=clipToPaper(raw,anchor);
   const box={x:Math.max(0,r.left),y:Math.max(0,r.top),w:Math.min(innerWidth,r.right)-Math.max(0,r.left),h:Math.min(innerHeight,r.bottom)-Math.max(0,r.top)};
   if(box.w<1||box.h<1)continue;const last=merged.at(-1);
   if(last&&Math.abs(last.y-box.y)<2&&box.x<=last.x+last.w+1){const right=Math.max(last.x+last.w,box.x+box.w);last.x=Math.min(last.x,box.x);last.w=right-last.x;last.h=Math.max(last.h,box.h);}
   else merged.push(box);
  }
  const signature=merged.map(b=>[b.x,b.y,b.w,b.h].map(n=>n.toFixed(1)).join('/')).join('|');if(signature===this.signature)return;
  this.signature=signature;document.body.classList.toggle('ink-selection-active',!!merged.length);
  if(!merged.length){if(this.active)this.ended=time;this.active=false;if(!running){this.disposeItems();this.ended=time-1;}return;}
  if(!this.active)this.started=time-(running?0:.26);this.active=true;this.disposeItems();
  this.items=merged.map((box,i)=>({...box,source:openPrint(box.w,box.h+4,i+21,'selection')}));
 }
 disposeItems(){for(const item of this.items)item.source.width=item.source.height=0;this.items=[];}
 draw(time,running){
  if(this.dirty)this.refresh(time,running);const l=this.layer,c=l.ctx;if(!c)return;l.clear();
  const alpha=this.active?1:1-ease((time-this.ended)/.25);if(alpha<=0){this.disposeItems();return;}
  const soak=.8+.2*ease((time-this.started)/.18);
  for(const item of this.items){
   const {x,y,w,h,source}=item;c.save();c.globalAlpha=alpha*soak;c.drawImage(source,x,y-2,w,h+4);c.restore();
  }
 }
 resize(){this.layer.resize(innerWidth,innerHeight);this.dirty=true;}
 destroy(){this.disposeItems();this.layer.destroy();document.body.classList.remove('ink-selection-active');}
}


export class PaperDetails{
 constructor(owner){
  this.owner=owner;this.alive=true;this.table=new TableImpression(owner,document.querySelector('.paper-table'));
  this.fields=[...document.querySelectorAll('.input-shell')].map(node=>({node,paper:owner.lookup.get(node),stock:node.dataset.stock,layer:new Layer(node,'focus-ink'),amount:0}));
  this.plot=new InkPlot(document.querySelector('.plot-ink'));this.selection=new SelectionInk(owner);
  for(const node of document.querySelectorAll('[data-transmission]')){
   const p=owner.lookup.get(node),opacity=clamp(Number(node.dataset.transmission));p.base.setAttribute('fill-opacity','.12');p.canvas.style.opacity=String(opacity);p.depths.forEach(d=>d.setAttribute('opacity','.2'));p.brushForeign.setAttribute('opacity','.45');
  }
  owner.on(document,'focusin',e=>this.focus(e.target));
  owner.on(document,'focusout',()=>{for(const f of this.fields){f.node.dataset.stock=f.stock;f.paper.paint();}this.selection.dirty=true;});
  owner.on(document,'input',()=>{this.selection.dirty=true;owner.forcePaint=true;owner.request();});
  for(const details of document.querySelectorAll('.paper-disclosure'))owner.on(details,'toggle',()=>{owner.needsMeasure=owner.forcePaint=true;owner.request();});
  owner.on(document,'scroll',()=>{this.selection.dirty=true;owner.forcePaint=true;owner.request();},{capture:true,passive:true});
  this.measure();
 }
 focus(target){
  const field=this.fields.find(f=>f.node.contains(target));if(field){field.node.dataset.stock='cream';field.paper.paint();}this.selection.dirty=true;this.owner.forcePaint=true;this.owner.request();
 }
 measure(){
  if(!this.alive)return;
  for(const item of this.fields)item.layer.resize(item.node.offsetWidth,item.node.offsetHeight);
  this.table.resize();this.plot.resize();this.selection.resize();
 }
 step(dt,pointer,time,running,paint){
  if(!this.alive)return;const blend=running?1-Math.exp(-dt*12):1;
  this.table.step(dt,pointer,running,paint);
  for(const item of this.fields){
   const focused=item.node.contains(document.activeElement);item.amount+=(Number(focused)-item.amount)*blend;if(!paint)continue;
   const l=item.layer,c=l.ctx,w=l.width,h=l.height;l.clear();if(!c||item.amount<.003)continue;c.save();c.globalAlpha=item.amount;
   const points=[];for(let x=10;x<=w-10;x+=3)points.push({x,y:h-3.8+(hash(x*5)-.5)*.3});
   c.globalAlpha=item.amount*.55;fibreLine(c,points,'#aaa89c',.55,71);fibreLine(c,points.map(p=>({x:p.x,y:p.y+1})),'#fffefb',.9,72);
   c.globalAlpha=item.amount*.8;fibreLine(c,[{x:10,y:h-4},{x:13,y:h-4.2},{x:18,y:h-4.1}],'#c23d43',.8,73);c.restore();
  }
  this.plot.step(dt,pointer,running,paint);if(paint)this.selection.draw(time,running);
 }
 destroy(){
  if(!this.alive)return;this.alive=false;
  for(const item of this.fields)item.layer.destroy();
  this.fields=[];this.table.destroy();this.plot.destroy();this.selection.destroy();
 }
}
