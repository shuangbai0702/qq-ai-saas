const TAU=Math.PI*2;
const colors=['#dc8eb4','#ac90d2','#84b9cf'];
const ink='#706080';
const dot=(c,x,y,r,color)=>{c.beginPath();c.arc(x,y,r,0,TAU);c.fillStyle=color;c.fill();};
function path(c,pts,color,width=1,dash=[]){c.beginPath();pts.forEach(([x,y],i)=>c[i?'lineTo':'moveTo'](x,y));c.strokeStyle=color;c.lineWidth=width;c.setLineDash(dash);c.stroke();c.setLineDash([]);}
function label(c,s,x,y,color=ink,size=9,align='left'){c.font=`600 ${size}px "Segoe UI","Microsoft YaHei",sans-serif`;c.textAlign=align;c.fillStyle=color;c.fillText(s,x,y);}
function round(c,x,y,w,h,r,fill,stroke){c.beginPath();c.roundRect(x,y,w,h,r);if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=1;c.stroke();}}
function star(c,x,y,r,color,rotation=0){c.save();c.translate(x,y);c.rotate(rotation);c.beginPath();for(let i=0;i<8;i++){const a=i/8*TAU,v=i%2?r*.24:r;c.lineTo(Math.cos(a)*v,Math.sin(a)*v);}c.closePath();c.fillStyle=color;c.fill();c.restore();}
function cubic(points,t){const v=1-t;return {x:v*v*v*points[0][0]+3*v*v*t*points[1][0]+3*v*t*t*points[2][0]+t*t*t*points[3][0],y:v*v*v*points[0][1]+3*v*v*t*points[1][1]+3*v*t*t*points[2][1]+t*t*t*points[3][1]};}
function curve(c,pts,color,width,dash=[]){c.beginPath();c.moveTo(...pts[0]);c.bezierCurveTo(...pts[1],...pts[2],...pts[3]);c.lineWidth=width;c.strokeStyle=color;c.setLineDash(dash);c.stroke();c.setLineDash([]);}

export function drawSoftStage(s){
  const c=s.sc,w=s.stage.clientWidth,h=s.stage.clientHeight,t=s.t,online=s.context.online,phase=s.context.phaseIndex,active=phase>=0,cx=w*.48,cy=h*.42,scale=Math.min(1,w/680),vigor=.65+s.energy*.8;
  c.clearRect(0,0,w,h);
  // Printed coordinates, animated heartbeat, and a responsive local field are separate layers.
  c.fillStyle='#ba9dca25';for(let x=18;x<w;x+=20)for(let y=12;y<h;y+=20){const distance=Math.hypot(x-s.cursor.x*w,y-s.cursor.y*h),reach=60+s.density*140,field=Math.max(0,1-distance/reach);dot(c,x,y,.7+field*.65,field>0?'#c997c6aa':'#bdacca36');}
  c.save();c.translate(cx,cy);c.rotate(-.24);c.beginPath();c.ellipse(0,0,Math.min(w*.23,180),h*.26,0,0,TAU);c.fillStyle='#e9d9f22b';c.fill();c.strokeStyle='#d2b5de66';c.setLineDash([2,7]);c.lineWidth=1;c.stroke();c.restore();
  for(let j=0;j<3;j++){
    const beat=online?(t*vigor*.34+j/3)%1:.15+j*.19,r=(60+beat*37)*Math.max(.75,scale);
    c.beginPath();c.arc(cx,cy,r,-.7,TAU-.8);c.strokeStyle=`rgba(188,148,211,${online?(1-beat)*.25:.06})`;c.lineWidth=1.2;c.stroke();
  }
  // These connectors use the very same normalized positions as the draggable HTML nodes.
  s.nodes.forEach((n,i)=>{
    const x=n.x*w,y=n.y*h,dx=x-cx,pts=[[cx+Math.sign(dx)*45*scale,cy],[cx+dx*.45,cy],[cx+dx*.42,y],[x,y]],enabled=s.routes[i],isActive=active&&phase===i&&enabled&&online,color=colors[i];
    curve(c,pts,enabled?color+'27':'#bcaac42c',7);
    curve(c,pts,enabled?color+'b8':'#c8bcc788',isActive?2:1.1,enabled?[]:[3,5]);
    for(let n=0;n<3;n++){const p=cubic(pts,.2+n*.3);dot(c,p.x,p.y,2.2,'#fff9f9');dot(c,p.x,p.y,1,color+'80');}
    if(isActive)for(let packet=0;packet<2;packet++){
      const u=(t*(.45+s.energy*.8)+packet*.5)%1,p=cubic(pts,u);for(let tail=1;tail<6;tail++){const q=cubic(pts,Math.max(0,u-tail*.018));dot(c,q.x,q.y,Math.max(.5,2-tail*.25),color+(75-tail*10).toString(16));}star(c,p.x,p.y,4.5,color,t*1.5);
    }
  });
  // A restrained input rail: no fake messages during idle, only a connection indicator.
  const inY=cy+h*.19,leftX=w*.13;path(c,[[leftX,inY],[cx-35*scale,inY],[cx-35*scale,cy+43*scale]],'#c3a8d572',1,[3,5]);
  round(c,leftX-27,inY-12,54,24,9,'#f5ecfa','#d4bedf');dot(c,leftX-15,inY,2,online?'#90bba9':'#bcaabc');label(c,'QQ',leftX+2,inY+3,ink,8,'center');
  if(active&&online){const u=(t*vigor*.7)%1;star(c,leftX+(cx-leftX-35*scale)*u,inY,3.5,'#ca90bd');}
  // The central terminal is all vector drawing: pressure, blink, processing eyes and bob.
  const bob=online?Math.sin(t*2.3*vigor)*2.3:0,squash=active?1+Math.sin(t*9)*.015:1;
  c.save();c.translate(cx,cy+bob);c.scale(scale*squash,scale/squash);
  dot(c,0,52,43,'#ccb2db19');c.fillStyle='#c5aedb';round(c,-62,-29,124,59,18,'#c9b1df');
  path(c,[[0,-48],[0,-64]],'#c8a2d5',3);dot(c,0,-65,5,'#f1aacb');dot(c,-1,-67,1.6,'#fffaf9');
  const g=c.createLinearGradient(-45,-45,45,45);g.addColorStop(0,'#fff6fb');g.addColorStop(.45,'#f2d9ed');g.addColorStop(1,'#d5b6e7');
  c.save();c.shadowColor='#b38cc333';c.shadowBlur=12;c.shadowOffsetY=7;round(c,-53,-48,106,94,24,g);c.restore();
  round(c,-53,-48,106,94,24,null,'#bd9ed1');path(c,[[-31,-43],[27,-43]],'#fffdfb',3);round(c,-44,-32,88,58,17,'#fffdf6','#e6cfdf');
  const blink=online&&(t%4.6>4.43||!online),eyesClosed=!online||blink;
  if(eyesClosed){path(c,[[-28,-3],[-20,1],[-12,-3]],'#8f729f',3);path(c,[[12,-3],[20,1],[28,-3]],'#8f729f',3);}
  else {const look=(s.cursor.x-.5)*5;for(const x of [-21,21]){round(c,x-6+look,-17,12,19,6,active?'#b486bb':'#78618e');dot(c,x-2+look,-13,2.1,'#ffffff');}}
  c.save();c.globalAlpha=.55;c.scale(1,.5);dot(c,-32,16,7,'#f4acc7');dot(c,32,16,7,'#f4acc7');c.restore();
  c.beginPath();c.moveTo(-7,8);c.quadraticCurveTo(0,18,7,8);c.strokeStyle='#b58db0';c.lineWidth=2;c.stroke();
  round(c,-31,34,23,4,2,'#fff9fc');for(let i=0;i<3;i++)dot(c,23+i*6,36,1.5,i===Math.max(0,phase)?'#b180c4':'#d7bfdf');
  path(c,[[-33,50],[33,50]],'#c5a2d7',6);c.restore();
  const sx=w*.06,sy=h*.08;label(c,'INPUT → CONTEXT → OUTPUT',sx,sy,'#a38bb1',w<550?6:8);label(c,'SIM / '+String(s.context.activity).padStart(3,'0'),w*.94,sy,'#a38bb1',8,'right');
  star(c,w*.35,h*.18,4+Math.sin(t)*1.4,'#cca5d3',.2);star(c,w*.59,h*.68,5,'#e5b4cc',-.2);
  // Clickable memory samples mirror the selected record in the actual component below.
  const cardW=Math.min(w*.185,148),my=h*.85;
  for(let i=0;i<4;i++){const x=w*(.17+i*.22),selected=i===s.context.selected,color=selected?'#cd96c2':'#cab8d4';round(c,x-cardW/2,my-16,cardW,32,10,selected?'#f9e7f2':'#fffcf8',color);dot(c,x-cardW/2+11,my,selected?3:2,selected?'#cf8db6':'#c4b2ce');label(c,(s.context.memories[i]?.name||'记忆索引').slice(0,6),x+1,my+3,selected?'#8b5f8c':'#9c87a6',w<550?7:9,'center');if(s.context.pinned.includes(i))star(c,x+cardW/2-9,my,3,'#c491be');}
  if(s.context.night){c.fillStyle='#73699b0d';c.fillRect(0,0,w,h);}
}

export function drawSoftAmbient(s){
  const c=s.ac,w=s.ambient.clientWidth,h=s.ambient.clientHeight,t=s.t,online=s.context.online,px=s.cursor.vx??w/2,py=s.cursor.vy??h/2,reach=70+s.density*230;
  c.clearRect(0,0,w,h);
  // An elastic coordinate fabric. It deforms only near the pointer, under actual controls.
  for(let x=22;x<w;x+=36)for(let y=20;y<h;y+=36){const dx=x-px,dy=y-py,d=Math.hypot(dx,dy),fall=Math.max(0,1-d/reach),wave=online?Math.sin(t*(.8+s.energy)+d*.018)*fall:0,offset=wave*6;dot(c,x+dx/(d||1)*offset,y+dy/(d||1)*offset,.65+fall*.45,fall>0?'#bd8fbd52':'#c7add52c');if(fall>.55){path(c,[[x-2+offset,y],[x+2+offset,y]],'#c6a3d326',.7);}}
  const a=s.anchors[s.context.source],b=s.anchors[s.context.target]||s.anchors.hero;
  if(a&&b&&s.context.source!==s.context.target){
    const right=a.x+a.width/2>w/2,rail=right?Math.min(w-12,a.x+a.width+13):Math.max(12,a.x-13),sx=right?a.x+a.width:a.x,sy=a.y+a.height*.45,tx=right?b.x+b.width:b.x,ty=Math.max(-40,Math.min(h+40,b.y+b.height*.5)),pts=[[sx,sy],[rail,sy],[rail,ty],[tx,ty]];
    curve(c,pts,'#d2a4cb42',5);curve(c,pts,'#b68ac98a',1,[3,4]);dot(c,sx,sy,3,'#d29ac4');star(c,tx,ty,4,'#b691cc');
    for(const signal of s.signals){if(signal.source!==s.context.source||signal.target!==s.context.target)continue;const u=Math.min(1,(t-signal.at)/1.6);if(u>=1)continue;const p=cubic(pts,u);star(c,p.x,p.y,4,'#d088b5',t);}
    if(a.y<h&&a.y+a.height>0){round(c,a.x-4,a.y-4,a.width+8,a.height+8,22,null,'#d8afd450');}
  }
}
