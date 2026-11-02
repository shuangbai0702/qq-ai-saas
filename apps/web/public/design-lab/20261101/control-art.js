const TAU=Math.PI*2;
const colors=['#9db9df','#86abcF','#a8b9cf'];
const ink='#8da8ca';
function dot(c,x,y,r,color){c.beginPath();c.arc(x,y,r,0,TAU);c.fillStyle=color;c.fill();}
function line(c,pts,color,width=1,dash=[]){c.beginPath();pts.forEach(([x,y],i)=>c[i?'lineTo':'moveTo'](x,y));c.strokeStyle=color;c.lineWidth=width;c.setLineDash(dash);c.stroke();c.setLineDash([]);}
function rect(c,x,y,w,h,r,fill,stroke){c.beginPath();c.roundRect(x,y,w,h,r);if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=1;c.stroke();}}
function text(c,value,x,y,color=ink,size=8,align='left'){c.font=`${size}px "Consolas","Microsoft YaHei",sans-serif`;c.textAlign=align;c.fillStyle=color;c.fillText(value,x,y);}
function cubic(pts,t){const v=1-t;return {x:v*v*v*pts[0][0]+3*v*v*t*pts[1][0]+3*v*t*t*pts[2][0]+t*t*t*pts[3][0],y:v*v*v*pts[0][1]+3*v*v*t*pts[1][1]+3*v*t*t*pts[2][1]+t*t*t*pts[3][1]};}
function curve(c,pts,color,width=1,dash=[]){c.beginPath();c.moveTo(...pts[0]);c.bezierCurveTo(...pts[1],...pts[2],...pts[3]);c.strokeStyle=color;c.lineWidth=width;c.setLineDash(dash);c.stroke();c.setLineDash([]);}
function packet(c,pts,t,color){for(let i=7;i>=0;i--){const v=Math.max(0,t-i*.009),p=cubic(pts,v);dot(c,p.x,p.y,Math.max(.6,2.3-i*.2),color+(80-i*8).toString(16));}const p=cubic(pts,t);rect(c,p.x-2.3,p.y-2.3,4.6,4.6,1,color);}
function module(c,x,y,w,title,sub,online){rect(c,x-w/2,y-23,w,46,7,'#ffffff','#d9e6f7');line(c,[[x-w/2+8,y-20],[x+w/2-8,y-20]],'#ffffff',1);dot(c,x-w/2+10,y-8,2,online?'#a3bfb0':'#bccbde');text(c,title,x-w/2+18,y-5,'#89a6cb',9);text(c,sub,x-w/2+10,y+13,'#b0c4de',6);}

export function drawControlStage(s){
  const view=s.inspection??s.context,routes=s.inspection?Object.values(s.inspection.capabilities):s.routes,activity=view.activity??s.context.activity,selectedIndex=s.inspection?view.memoryIndex:s.context.selected;
  const c=s.sc,w=s.stage.clientWidth,h=s.stage.clientHeight,cx=w*.5,cy=h*.53,t=s.inspection?s.inspection.time:s.t,online=view.online,phase=view.phaseIndex,busy=phase>=0&&online,unit=Math.max(.68,Math.min(1,w/700));
  c.clearRect(0,0,w,h);
  // Coordinates are structural. Density reveals the dependency construction layer.
  for(let x=17;x<w;x+=20)for(let y=12;y<h;y+=20)dot(c,x,y,.55,'#d9e4f251');
  if(s.density>.4){for(let j=0;j<3;j++){c.beginPath();c.ellipse(cx,cy,55*unit+j*21,55*unit+j*21,0,0,TAU);c.strokeStyle='#dce8f66a';c.lineWidth=.8;c.setLineDash([2,5]);c.stroke();c.setLineDash([]);}}
  // Center endpoints attach immediately; only the cable handles follow a damped spring.
  const oldTime=s.cableTime??t,dt=Math.max(.001,Math.min(.035,t-oldTime));s.cableTime=t;s.cables??=[];
  s.nodes.forEach((node,i)=>{
    const nx=node.x*w,ny=node.y*h,tx=cx+(nx-cx)*.12,ty=cy-44*unit;
    const target={x:(nx+tx)/2,y:Math.min(ny+65,ty-10)};
    let cable=s.cables[i];if(!cable)cable=s.cables[i]={...target,vx:0,vy:0};
    if(s.running){cable.vx+=((target.x-cable.x)*130-cable.vx*18)*dt;cable.vy+=((target.y-cable.y)*130-cable.vy*18)*dt;cable.x+=cable.vx*dt;cable.y+=cable.vy*dt;}else Object.assign(cable,target,{vx:0,vy:0});
    const pts=[[tx,ty],[cable.x,ty],[cable.x,cable.y],[nx,ny]],enabled=routes[i],active=busy&&phase===i&&enabled,color=colors[i];
    curve(c,pts,enabled?'#cfdef153':'#e0e7f030',5);curve(c,pts,enabled?color+'c5':'#ccd9e880',active?1.6:1,enabled?[]:[3,5]);
    if(s.density>.75){line(c,[pts[0],pts[1]],'#d5e2f158',.65,[2,3]);line(c,[pts[2],pts[3]],'#d5e2f158',.65,[2,3]);dot(c,...pts[1],2,'#c6d9f080');dot(c,...pts[2],2,'#c6d9f080');}
    if(active){packet(c,pts,(t*(.4+s.energy*.7))%1,'#7fa5d6');packet(c,pts,(t*(.4+s.energy*.7)+.5)%1,'#a2bee2');}
  });
  const left=w*.14,right=w*.86,mw=Math.min(105,w*.20),inPts=[[left+mw/2,cy],[cx-90*unit,cy],[cx-75*unit,cy],[cx-49*unit,cy]],outPts=[[cx+49*unit,cy],[cx+75*unit,cy],[right-mw/2-20,cy],[right-mw/2,cy]];
  curve(c,inPts,'#c8daef',1.4);curve(c,outPts,'#c8daef',1.4);
  if(busy){if(phase===0)packet(c,inPts,(t*(.6+s.energy))%1,'#7da2d3');if(phase>=2)packet(c,outPts,(t*(.6+s.energy))%1,'#7da2d3');}
  module(c,left,cy,mw,'QQ / SIM','PROTOCOL INPUT',online);module(c,right,cy,mw,'统一输出','OUTPUT / '+String(activity).padStart(3,'0'),online);
  // Online heartbeat is explicitly separate from active request transport.
  if(online){const beat=(t*.42)%1;c.beginPath();c.arc(cx,cy,(55+beat*15)*unit,0,TAU);c.lineWidth=1;c.strokeStyle=`rgba(142,177,222,${(1-beat)*.28})`;c.stroke();}
  c.save();c.translate(cx,cy);c.scale(unit,unit);
  for(let i=0;i<7;i++){const v=-27+i*9;for(const sign of [-1,1]){line(c,[[sign*42,v],[sign*49,v]],'#b4cbe8',1);line(c,[[v,sign*42],[v,sign*49]],'#b4cbe8',1);}}
  const gradient=c.createLinearGradient(-40,-40,40,40);gradient.addColorStop(0,'#ffffff');gradient.addColorStop(.55,'#f3f8ff');gradient.addColorStop(1,'#e2edfb');c.save();c.shadowColor='#7b9dc51c';c.shadowBlur=9;c.shadowOffsetY=3;rect(c,-41,-41,82,82,14,gradient,'#b9d0ee');c.restore();rect(c,-34,-34,68,68,10,'#f9fcff','#dce8f8');line(c,[[-22,-29],[20,-29]],'#ffffff',2);
  text(c,'DS',0,5,online?'#88a9d5':'#bdcbdc',23,'center');text(c,'MODEL / SIM',0,20,'#a8c0df',5,'center');
  for(let i=0;i<4;i++){const active=busy&&i<=phase;rect(c,-24+i*13,29,8,2.5,1,active?'#8eb2e0':'#cfdef1');}c.restore();
  text(c,'CAPABILITY ROUTES',w*.055,h*.07,'#acc0db',7);text(c,'LOCAL / '+String(activity).padStart(3,'0'),w*.945,h*.07,'#acc0db',7,'right');
  text(c,online?'CONNECTION READY':'INSTANCE STOPPED',left,cy+38,'#b3c6df',w<500?5:6,'center');text(c,'ENABLED '+routes.filter(Boolean).length+'/3',right,cy+38,'#b3c6df',w<500?5:6,'center');
  const my=h*.87,cardW=Math.min(w*.18,146);
  for(let i=0;i<4;i++){const x=w*(.17+i*.22),selected=i===selectedIndex;rect(c,x-cardW/2,my-14,cardW,28,5,selected?'#eaf3ff':'#ffffff',selected?'#abc7ed':'#dce7f5');dot(c,x-cardW/2+9,my,2,selected?'#93b4e0':'#c0d2e9');text(c,(s.context.memories[i]?.name||'记忆索引').slice(0,6),x+3,my+3,selected?'#8ba9d0':'#b0c3dc',w<500?7:8,'center');if(s.context.pinned.includes(i))text(c,'+',x+cardW/2-8,my+3,'#a0b7d6',9,'center');}
}

const dependencies={identity:['hero','conversation','resources'],personality:['conversation','capability','hero'],memory:['conversation','capability','hero'],capability:['conversation','hero','resources'],conversation:['hero','memory','capability'],resources:['hero','identity'],stickers:['conversation','hero'],rhythm:['capability','hero'],states:['hero']};
function pageRoute(a,b,w,h,index){const right=a.x+a.width/2>w/2,rail=right?Math.min(w-8,a.x+a.width+10+index*3):Math.max(8,a.x-10-index*3),sx=right?a.x+a.width:a.x,sy=a.y+a.height*.42,tx=right?b.x+b.width:b.x,ty=Math.max(-60,Math.min(h+60,b.y+b.height*.5));return [[sx,sy],[rail,sy],[rail,ty],[tx,ty]];}
export function drawControlAmbient(s){
  const c=s.ac,w=s.ambient.clientWidth,h=s.ambient.clientHeight,source=s.context.source,a=s.anchors[source];c.clearRect(0,0,w,h);
  if(!a)return;
  // Dependency routes come from real component bounds, never arbitrary cursor trails.
  const targets=[s.context.target,...(dependencies[source]||['hero'])].filter((v,i,arr)=>v!==source&&arr.indexOf(v)===i).slice(0,1+Math.floor(s.density*2));
  for(const [i,key] of targets.entries()){
    const b=s.anchors[key];if(!b)continue;const pts=pageRoute(a,b,w,h,i);curve(c,pts,i?'#b4c9e32f':'#a0bce054',i?.65:1,[3,5]);dot(c,...pts[0],2,'#aac3e2');dot(c,...pts[3],2,'#b1c9e6');
    if(!s.context.online)continue;for(const signal of s.signals){if(signal.source!==source)continue;const age=s.t-signal.at,u=Math.min(1,age/(1.2+i*.2));if(u<1)packet(c,pts,u,'#9cbbe1');}
  }
  if(a.y<h&&a.y+a.height>0){const p=2;for(const [x,y,dx,dy] of [[a.x-p,a.y-p,1,1],[a.x+a.width+p,a.y-p,-1,1],[a.x-p,a.y+a.height+p,1,-1],[a.x+a.width+p,a.y+a.height+p,-1,-1]])line(c,[[x,y+dy*10],[x,y],[x+dx*10,y]],'#a9c4e54f',1);}
  // Finite impact rings visualize a committed operation at its source component.
  for(const signal of s.signals){if(signal.source!==source)continue;const age=s.t-signal.at;if(age>1.2)continue;const x=a.x+a.width/2,y=a.y+a.height*.42;c.beginPath();c.ellipse(x,y,a.width*.52+age*15,a.height*.52+age*10,0,0,TAU);c.strokeStyle=`rgba(146,177,216,${(1-age/1.2)*.12})`;c.lineWidth=1;c.stroke();}
}
