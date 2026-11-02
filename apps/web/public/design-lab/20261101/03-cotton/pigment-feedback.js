// Three pigments briefly soak into the paper face. Nothing leaves its cut edge.
// This painter is driven by the study's clock and owns no listeners or timers.
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const smooth=v=>{v=clamp(v);return v*v*(3-2*v);};
const hash=n=>{const v=Math.sin(n*127.1+42.7)*43758.5453;return v-Math.floor(v);};
const palettes=[[[66,158,180],[227,118,130],[225,184,92]],[[96,167,145],[136,132,190],[228,139,119]]];
const mix=(a,b,t)=>a+(b-a)*t;
function print(width,height,seed,gain=1){
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext('2d');if(!ctx)return canvas;
  const image=ctx.createImageData(width,height),colours=palettes[seed%palettes.length];
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const u=x/width,v=y/height,tooth=hash(x*3+y*137+seed*19);
    const cloud=Math.sin(u*8+Math.sin(v*5+seed))*.1+Math.cos(v*9-u*4)*.08;
    const a=smooth((u+cloud-.12)/.48),b=smooth((u-cloud-.55)/.4);
    const colour=colours[0].map((c,i)=>mix(mix(c,colours[1][i],a),colours[2][i],b));
    const density=.3+Math.sin(u*11+v*4+seed)*.055+Math.cos(v*14-u*5)*.035;
    const p=(y*width+x)*4;
    for(let c=0;c<3;c++)image.data[p+c]=colour[c];
    image.data[p+3]=255*density*(.68+tooth*.32)*(tooth<.06?.35:1)*gain;
  }
  ctx.putImageData(image,0,0);return canvas;
}
export class PigmentFeedback{
  constructor(){this.alive=true;this.marks=new Map();this.serial=0;}
  add(surface,point,time,still=false){
    if(!this.alive||!surface?.alive)return;
    if(surface.kind==='range'&&this.marks.has(surface)){
      const mark=this.marks.get(surface);mark.time=time;mark.still=still;return;
    }
    this.remove(surface);
    const canvas=surface.feedbackCanvas,w=canvas.width,h=canvas.height;
    if(!w||!h)return;
    const seed=++this.serial,source=print(w,h,seed,surface.kind==='range'?1.7:1),mask=document.createElement('canvas');mask.width=w;mask.height=h;
    this.marks.set(surface,{canvas,source,mask,time,still,life:1.45,x:clamp(point.x/surface.width)*w,y:clamp(point.y/surface.height)*h});
  }
  remove(surface){
    const mark=this.marks.get(surface);if(!mark)return;
    mark.canvas.getContext('2d')?.clearRect(0,0,surface.width,surface.height);
    mark.source.width=mark.source.height=mark.mask.width=mark.mask.height=0;this.marks.delete(surface);
  }
  clear(){for(const surface of this.marks.keys())this.remove(surface);}
  resize(){this.clear();}
  release(surface,time){if(!surface)return;surface.active=false;const mark=this.marks.get(surface);if(mark)mark.time=time;}
  draw(time){
    if(!this.alive)return;
    for(const [surface,m]of this.marks){
      if(surface.active)m.time=time;
      if(!surface.alive||time-m.time>=m.life){this.remove(surface);continue;}
      const age=surface.active ? .23 : clamp((time-m.time)/m.life+(m.still?.23:0));
      const fade=1-smooth((age-.35)/.65),spread=1-Math.pow(1-clamp(age/.34),3);
      const w=m.source.width,h=m.source.height,ctx=m.mask.getContext('2d'),out=m.canvas.getContext('2d');
      if(!ctx||!out)continue;
      if(surface.kind==='range'){
        const filled=6.5+(surface.width-13)*surface.fill;
        out.clearRect(0,0,surface.width,surface.height);out.save();
        out.beginPath();out.roundRect(0,surface.height/2-2.5,Math.max(1,filled),5,2.5);out.clip();
        out.globalAlpha=fade*.94;out.fillStyle='#fffefb';out.fillRect(0,0,surface.width,surface.height);
        out.globalAlpha=fade;out.drawImage(m.source,0,0,surface.width,surface.height);
        out.restore();out.clearRect(filled-7,0,14,surface.height);continue;
      }
      ctx.clearRect(0,0,w,h);ctx.globalCompositeOperation='source-over';ctx.drawImage(m.source,0,0);
      const radius=Math.max(2,Math.hypot(Math.max(m.x,w-m.x),Math.max(m.y,h-m.y))*(.08+spread*1.18));
      const edge=ctx.createRadialGradient(m.x,m.y,radius*.35,m.x,m.y,radius);edge.addColorStop(0,'#fff');edge.addColorStop(.7,'#ffffffc0');edge.addColorStop(1,'#ffffff00');
      ctx.globalCompositeOperation='destination-in';ctx.fillStyle=edge;ctx.fillRect(0,0,w,h);ctx.globalCompositeOperation='source-over';
      out.clearRect(0,0,surface.width,surface.height);out.globalAlpha=fade;out.drawImage(m.mask,0,0,surface.width,surface.height);out.globalAlpha=1;
    }
  }
  destroy(){if(!this.alive)return;this.clear();this.alive=false;}
}
