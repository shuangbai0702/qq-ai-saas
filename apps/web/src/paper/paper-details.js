import {Layer,fibreLine,openPrint,SelectionInk,clipToPaper} from './paper-ink.js';

const FIELD='input:not([type=checkbox]):not([type=range]):not([type=hidden]),textarea,select';
const TAU=Math.PI*2,hash=n=>{const x=Math.sin(n*127.1+31.7)*43758.5453;return x-Math.floor(x);};
const blend=(dt,running)=>running?1-Math.exp(-dt*13):1;
const visible=r=>r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth;

// The same shallow, continuous R9 print, below the existing table and its text.
// Only rows that are actually receiving feedback allocate a texture.
class TableImpression{
 constructor(host){this.host=host;this.layer=new Layer(host,'paper-row-ink');this.rows=new Map();this.serial=0;host.classList.add('paper-table-host');this.reconcile();}
 reconcile(){
  for(const [node,row]of this.rows)if(!node.isConnected||!this.host.contains(node)){this.free(row);this.rows.delete(node);}
  for(const node of this.host.querySelectorAll('tbody>tr'))if(node.closest('.table-wrap')===this.host&&!this.rows.has(node))this.rows.set(node,{node,seed:31+this.serial++,amount:0});
  if(this.layer.canvas.parentElement!==this.host)this.host.append(this.layer.canvas);
 }
 free(row){if(row.source)row.source.width=row.source.height=0;row.source=null;}
 step(dt,pointer,running){
  const h=this.host,r=h.getBoundingClientRect();
  // client/offset dimensions round fractional CSS pixels. Feeding those rounded
  // dimensions back into a scroll container can create decoration-only overflow.
  const width=Math.max(0,h.clientWidth+r.width-h.offsetWidth),height=Math.max(0,h.clientHeight+r.height-h.offsetHeight);
  const top=Math.max(0,r.top+h.clientTop),bottom=Math.min(innerHeight,r.top+h.clientTop+height);
  if(!visible(r)||bottom<=top)return false;
  const l=this.layer,c=l.ctx,offset=top-r.top-h.clientTop;
  l.resize(width,bottom-top);l.canvas.style.left=h.scrollLeft+'px';l.canvas.style.top=(h.scrollTop+offset)+'px';l.canvas.style.width=width+'px';l.canvas.style.height=(bottom-top)+'px';l.clear();
  let settling=false;
  for(const row of this.rows.values()){
   const box=row.node.getBoundingClientRect(),inside=pointer.inside&&pointer.x>=Math.max(box.left,r.left+h.clientLeft)&&pointer.x<=Math.min(box.right,r.left+h.clientLeft+width)&&pointer.y>=Math.max(box.top,top)&&pointer.y<=Math.min(box.bottom,bottom);
   const focused=row.node.contains(document.activeElement),before=row.amount;row.amount+=(Number(inside||focused)-row.amount)*blend(dt,running);
   if(Math.abs(before-row.amount)>.0001)settling=true;
   if(row.amount<.003||!visible(box)){this.free(row);continue;}
   const w=Math.round(box.width),height=Math.round(box.height);
   if(!row.source||w!==row.width||height!==row.height){this.free(row);row.width=w;row.height=height;row.source=openPrint(w,height,row.seed,'row');}
   if(c){c.save();c.globalAlpha=row.amount;c.drawImage(row.source,box.left-r.left-h.clientLeft,box.top-top,box.width,box.height);c.restore();}
  }
  return settling;
 }
 destroy(){for(const row of this.rows.values())this.free(row);this.rows.clear();this.layer.destroy();this.host.classList.remove('paper-table-host');}
}

class FocusImpression{
 constructor(){this.layer=new Layer(document.body,'paper-focus-ink');this.amount=0;this.target=null;}
 resize(){this.layer.resize(innerWidth,innerHeight);}
 step(dt,running){
  const active=document.activeElement,field=active instanceof Element&&active.matches(FIELD)&&!active.matches(':disabled')?active:null;
  if(field&&field!==this.target){this.target=field;this.amount=0;}
  const before=this.amount;this.amount+=(Number(!!field)-this.amount)*blend(dt,running);const l=this.layer,c=l.ctx;l.mount([...document.querySelectorAll('dialog[open]')].at(-1)||document.body);l.clear();
  if(!c||!this.target?.isConnected||this.amount<.003)return Math.abs(before-this.amount)>.0001;
  const r=this.target.getBoundingClientRect();if(!visible(r))return false;
  const clip=clipToPaper(r,this.target.parentElement);c.save();c.beginPath();c.rect(clip.left,clip.top,Math.max(0,clip.right-clip.left),Math.max(0,clip.bottom-clip.top));c.clip();c.translate(r.left,r.top);
  const points=[];for(let x=10;x<=r.width-10;x+=3)points.push({x,y:r.height-3.8+(hash(x*5)-.5)*.3});
  c.globalAlpha=this.amount*.55;fibreLine(c,points,'#aaa89c',.55,71);fibreLine(c,points.map(p=>({x:p.x,y:p.y+1})),'#fffefb',.9,72);
  c.globalAlpha=this.amount*.8;fibreLine(c,[{x:10,y:r.height-4},{x:13,y:r.height-4.2},{x:18,y:r.height-4.1}],'#c23d43',.8,73);c.restore();
  return Math.abs(before-this.amount)>.0001;
 }
 destroy(){this.target=null;this.layer.destroy();}
}

// Read the real chart's M/L paths. Missing values remain separate subpaths.
// The original SVG, axes, labels and accessible description remain in React.
class InkChart{
 constructor(svg){this.node=svg;this.host=svg.closest('.memory-trend');this.layer=new Layer(this.host,'paper-chart-ink');this.base=document.createElement('canvas');this.key='';this.amount=0;this.host.classList.add('paper-ink-chart');}
 resize(){
  const r=this.node.getBoundingClientRect(),host=this.host.getBoundingClientRect();if(!r.width||!r.height)return;
  const resized=this.layer.resize(r.width,r.height);Object.assign(this.layer.canvas.style,{left:(r.left-host.left-this.host.clientLeft)+'px',top:(r.top-host.top-this.host.clientTop)+'px',width:r.width+'px',height:r.height+'px'});
  const paths=[...this.node.querySelectorAll('g>path')],view=this.node.viewBox.baseVal,matrix=this.node.getScreenCTM();if(!matrix)return;
  const key=JSON.stringify([r.width,r.height,view.width,view.height,matrix.a,matrix.d,matrix.e-r.left,matrix.f-r.top,paths.map(n=>[n.getAttribute('d'),n.getAttribute('stroke')])]);if(!resized&&key===this.key)return;this.key=key;
  this.base.width=this.layer.canvas.width;this.base.height=this.layer.canvas.height;const c=this.base.getContext('2d');if(!c)return;
  // SVG meet/letterboxing must use the same projection as the native axes.
  this.sx=matrix.a;this.sy=matrix.d;this.ox=matrix.e-r.left;this.oy=matrix.f-r.top;c.setTransform(this.layer.scale*this.sx,0,0,this.layer.scale*this.sy,this.layer.scale*this.ox,this.layer.scale*this.oy);
  const number='(-?\\d+(?:\\.\\d+)?(?:e[+-]?\\d+)?)',pattern=new RegExp('([ML])\\s*'+number+'[, ]+'+number,'gi');
  this.lines=paths.map(path=>{const chunks=[];let chunk;for(const m of(path.getAttribute('d')||'').matchAll(pattern)){if(m[1].toUpperCase()==='M'){chunk=[];chunks.push(chunk);}chunk?.push({x:Number(m[2]),y:Number(m[3])});}return chunks;});
  this.colours=paths.map(path=>path.getAttribute('stroke')||'#bb3037');
  this.lines.forEach((chunks,index)=>{
   c.fillStyle=this.colours[index];const colour=c.fillStyle,rgb=[1,3,5].map(i=>parseInt(colour.slice(i,i+2),16));
   const transfer=rgb.map((v,i)=>Math.round(v*.3+[255,254,251][i]*.7));
   for(const points of chunks){
    // Share the original dry-ink process, while retaining each series colour.
    fibreLine(c,points.map(p=>({x:p.x,y:p.y+.7})),`rgb(${transfer})`,1.9,35);
    for(let i=1;i<points.length;i++){
     const a=points[i-1],b=points[i],length=Math.hypot(b.x-a.x,b.y-a.y);
     for(let d=0;d<length;d+=.65){const t=d/length,seed=hash(d*13+i*71);c.fillStyle=`rgba(${rgb},${.25+seed*.42})`;c.beginPath();c.arc(a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t,.48+seed*.5,0,TAU);c.fill();}
    }
    for(let i=0;i<points.length;i++)if(i%Math.max(1,Math.ceil(points.length/5))===0||i===points.length-1){
     const p=points[i];c.fillStyle='#fffefb';c.strokeStyle=colour;c.lineWidth=.85;c.setLineDash([.7,.35]);c.beginPath();c.arc(p.x,p.y,2.6,0,TAU);c.fill();c.stroke();c.setLineDash([]);
    }
   }
  });
 }
 step(dt,pointer,running){
  const r=this.node.getBoundingClientRect();if(!visible(r))return false;
  const inside=pointer.inside&&pointer.x>=r.left&&pointer.x<=r.right&&pointer.y>=r.top&&pointer.y<=r.bottom,before=this.amount;this.amount+=(Number(inside)-this.amount)*blend(dt,running);
  const l=this.layer,c=l.ctx;if(!c||!this.lines)return false;l.clear();c.drawImage(this.base,0,0,l.width,l.height);
  if(this.amount>.003){
   const x=(pointer.x-r.left-this.ox)/this.sx,y=(pointer.y-r.top-this.oy)/this.sy;let point,distance=Infinity;
   for(const [index,chunks]of this.lines.entries())for(const points of chunks)for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i];if(x>=a.x&&x<=b.x&&b.x>a.x){const py=a.y+(b.y-a.y)*(x-a.x)/(b.x-a.x),d=Math.abs(y-py);if(d<distance){point={x,y:py,colour:this.colours[index]};distance=d;}}}
   if(point){c.save();c.translate(this.ox,this.oy);c.scale(this.sx,this.sy);c.globalAlpha=this.amount;c.strokeStyle=point.colour;c.lineWidth=.65;c.setLineDash([1.2,1.8]);c.beginPath();c.arc(point.x,point.y,5.4,0,TAU);c.stroke();c.setLineDash([]);c.fillStyle=point.colour;c.beginPath();c.arc(point.x,point.y,1.4,0,TAU);c.fill();c.restore();}
  }
  return Math.abs(before-this.amount)>.0001;
 }
 destroy(){this.layer.destroy();this.base.width=this.base.height=0;this.lines=[];this.host.classList.remove('paper-ink-chart');}
}

export class PaperDetails{
 constructor(owner){
  this.owner=owner;this.alive=true;this.tables=new Map();this.charts=new Map();this.fields=new Set();this.focus=new FocusImpression();this.selection=new SelectionInk(owner);
  for(const type of ['input','focusin','focusout'])owner.on(document,type,()=>{this.selection.dirty=true;owner.force=true;owner.request();});
 }
 isQuiet(target){return target instanceof Element&&!!target.closest('tbody>tr,'+FIELD+',summary,.memory-trend');}
 reconcile(){
  if(!this.alive)return;
  for(const [host,table]of this.tables)if(!host.isConnected||!host.querySelector('table')){table.destroy();this.tables.delete(host);}
  for(const host of document.querySelectorAll('.table-wrap'))if(host.querySelector('table')){if(!this.tables.has(host))this.tables.set(host,new TableImpression(host));else this.tables.get(host).reconcile();}
  for(const [node,chart]of this.charts)if(!node.isConnected){chart.destroy();this.charts.delete(node);}
  for(const node of document.querySelectorAll('.memory-trend>svg'))if(!this.charts.has(node))this.charts.set(node,new InkChart(node));
  for(const node of this.fields)if(!node.isConnected||!node.matches(FIELD)){node.classList.remove('paper-focus-field');this.fields.delete(node);}
  for(const node of document.querySelectorAll(FIELD))if(!this.fields.has(node)){this.fields.add(node);node.classList.add('paper-focus-field');}
  for(const [node,paper]of this.owner.surfaces)if(node.matches('details')){
   const opacity=node.open?'1':node.matches('.memory-advanced')?'.76':'.65';paper.quiet=true;
   paper.base.setAttribute('fill-opacity',node.open?'1':'.12');paper.canvas.style.opacity=opacity;paper.depths.forEach(n=>n.setAttribute('opacity',node.open?'1':'.2'));paper.brushForeign.setAttribute('opacity',node.open?'1':'.45');
  }
  this.selection.dirty=true;
 }
 measure(){this.focus.resize();this.selection.resize();for(const chart of this.charts.values())chart.resize();this.selection.dirty=true;}
 step(dt,pointer,time,running){
  if(!this.alive)return false;let settling=this.focus.step(dt,running);
  for(const table of this.tables.values())settling=table.step(dt,pointer,running)||settling;
  for(const chart of this.charts.values())settling=chart.step(dt,pointer,running)||settling;
  this.selection.draw(time,running);
  if(running&&this.selection.items.length&&(this.selection.active?time-this.selection.started<.18:time-this.selection.ended<.25))settling=true;
  return settling;
 }
 destroy(){
  if(!this.alive)return;this.alive=false;
  for(const table of this.tables.values())table.destroy();for(const chart of this.charts.values())chart.destroy();for(const field of this.fields)field.classList.remove('paper-focus-field');
  this.tables.clear();this.charts.clear();this.fields.clear();this.focus.destroy();this.selection.destroy();
 }
}
