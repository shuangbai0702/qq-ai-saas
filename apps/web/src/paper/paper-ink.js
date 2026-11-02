// Directly migrated from the approved R9 specimen; native selection stays native.
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
  if(this.owned){this.canvas.dataset.paperOwned='detail';this.canvas.className=name;this.canvas.setAttribute('aria-hidden','true');node.append(this.canvas);}
  this.ctx=this.canvas.getContext('2d');this.width=this.height=0;
 }
 resize(width,height){
  const w=Math.max(1,Math.round(width)),h=Math.max(1,Math.round(height)),scale=Math.min(devicePixelRatio||1,1.25);
  if(w===this.width&&h===this.height&&scale===this.scale)return false;
  this.width=w;this.height=h;this.scale=scale;this.canvas.width=Math.round(w*scale);this.canvas.height=Math.round(h*scale);this.ctx?.setTransform(scale,0,0,scale,0,0);return true;
 }
 clear(){this.ctx?.clearRect(0,0,this.width,this.height);}
 mount(host){if(this.owned&&this.canvas.parentElement!==host)host.append(this.canvas);}
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

class SelectionInk{
 constructor(owner){
  this.owner=owner;this.layer=new Layer(document.body,'paper-selection-ink');this.items=[];this.signature='';this.active=false;this.started=0;this.ended=0;this.dirty=true;this.contrast=matchMedia('(forced-colors: active)');
  for(const type of ['selectionchange','select','keyup','pointerup'])owner.on(document,type,()=>{this.dirty=true;owner.force=true;owner.request();});
  owner.on(this.contrast,'change',()=>{this.dirty=true;owner.force=true;owner.request();});
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
  this.signature=signature;document.body.classList.toggle('paper-selection-active',!!merged.length);
  if(!merged.length){if(this.active)this.ended=time;this.active=false;if(!running){this.disposeItems();this.ended=time-1;}return;}
  if(!this.active)this.started=time-(running?0:.26);this.active=true;this.disposeItems();
  this.items=merged.map((box,i)=>({...box,source:openPrint(box.w,box.h+4,i+21,'selection')}));
 }
 disposeItems(){for(const item of this.items)item.source.width=item.source.height=0;this.items=[];}
 draw(time,running){
  if(this.dirty)this.refresh(time,running);const l=this.layer,c=l.ctx;if(!c)return;l.mount([...document.querySelectorAll('dialog[open]')].at(-1)||document.body);l.clear();
  const alpha=this.active?1:1-ease((time-this.ended)/.25);if(alpha<=0){this.disposeItems();return;}
  const soak=.8+.2*ease((time-this.started)/.18);
  for(const item of this.items){
   const {x,y,w,h,source}=item;c.save();c.globalAlpha=alpha*soak;c.drawImage(source,x,y-2,w,h+4);c.restore();
  }
 }
 resize(){this.layer.resize(innerWidth,innerHeight);this.dirty=true;}
 destroy(){this.disposeItems();this.layer.destroy();document.body.classList.remove('paper-selection-active');}
}


export {Layer,fibreLine,openPrint,SelectionInk,clipToPaper};
