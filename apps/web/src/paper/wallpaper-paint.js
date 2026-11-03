// A soft round brush reveals the drawing's original colours for a fixed time.
// The study owns the clock and invalidation; this class owns no RAF or timers.
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hash = n => { const x = Math.sin(n * 127.1 + 31.7) * 43758.5453; return x - Math.floor(x); };
const smooth = v => { v = clamp(v, 0, 1); return v * v * (3 - 2 * v); };
const TILE = 128, MASK_SCALE = .5, INTERVAL = .15, HOLD = 2.6, FADE = 3, SPACING = 7;
function roundTip(seed) {
  const canvas=document.createElement('canvas'); canvas.width=canvas.height=128;
  const ctx=canvas.getContext('2d'); if (!ctx) return canvas;
  const image=ctx.createImageData(128,128);
  for(let y=0;y<128;y++)for(let x=0;x<128;x++){
    const dx=(x-63.5)/62,dy=(y-63.5)/62,r=Math.hypot(dx,dy);
    const tooth=hash(x*3+y*137+seed*19),cloud=Math.sin(x*.19+Math.sin(y*.14))*Math.cos(y*.17)*.025;
    const density=1-smooth((r+cloud-.68)/.32),bristle=hash(Math.floor(y*1.15)*11+seed);
    const fibre=.22+.78*smooth((bristle-.12)/.7),dry=.65+.35*smooth((Math.sin(x*.13+bristle*7)+1)*.5);
    const p=(y*128+x)*4;image.data[p]=image.data[p+1]=image.data[p+2]=255;
    image.data[p+3]=density*fibre*dry*(.54+tooth*.12)*(tooth<.055?.35:1)*255;
  }
  ctx.putImageData(image,0,0); return canvas;
}
export class WallpaperPaint {
  constructor(canvas, invalidate, source = '/paper/deepseek-desk.png') {
    this.canvas = canvas; this.invalidate = invalidate; this.alive = true; this.ready = false;
    // Sparse raster tiles are grouped by time, never evicted by stroke count.
    // Repeated input in a time slice shares tiles, including when paused.
    this.marks = []; this.dirty = true; this.tip=roundTip(1); this.carry=0; this.lastPoint=null;
    this.base = document.createElement('canvas'); this.colour = document.createElement('canvas');
    this.mask = document.createElement('canvas'); this.reveal = document.createElement('canvas');
    this.resize(innerWidth, innerHeight);
    const image = new Image(); this.image = image; image.decoding = 'async';
    image.onload = () => {
      if (!this.alive || this.image !== image) return;
      image.onload = image.onerror = null;
      try { this.prepare(); this.ready = true; this.canvas.dataset.wallpaper = 'ready'; }
      catch { this.ready = false; this.canvas.dataset.wallpaper = 'unavailable'; }
      this.dirty = true; this.invalidate();
    };
    image.onerror = () => {
      if (!this.alive || this.image !== image) return;
      image.onload = image.onerror = null; this.canvas.dataset.wallpaper = 'unavailable'; this.invalidate();
    };
    canvas.dataset.wallpaper = 'loading'; image.src = source;
  }
  resize(w, h) {
    if (!this.alive) return;
    this.w = w; this.h = h; this.scale = Math.min(devicePixelRatio || 1, 1.25);
    for (const canvas of [this.canvas, this.base, this.colour, this.mask, this.reveal]) {
      canvas.width = Math.max(1, Math.round(w * this.scale)); canvas.height = Math.max(1, Math.round(h * this.scale));
      canvas.getContext('2d')?.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    }
    this.ctx = this.canvas.getContext('2d'); this.clear();this.paintedBounds=null;this.fullRedraw=true;
    if (this.ready) this.prepare();
  }
  prepare() {
    const image = this.image, ctx = this.colour.getContext('2d'), base = this.base.getContext('2d');
    if (!ctx || !base || !image?.naturalWidth) throw new Error('Wallpaper canvas unavailable');
    const offset = Math.min(38, this.h * .035);
    const scale = Math.max(this.w / image.naturalWidth, (this.h + offset) / image.naturalHeight);
    const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
    const x = (this.w - w) * (this.w < 700 ? .74 : .5), y = -offset;
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, this.w, this.h); ctx.drawImage(image, x, y, w, h);
    const original = ctx.getImageData(0, 0, this.colour.width, this.colour.height);
    const drawing = base.createImageData(original.width, original.height), data = original.data, out = drawing.data;
    const lum = new Uint8Array(original.width * original.height);
    for (let i = 0, p = 0; p < lum.length; p++, i += 4) lum[p] = data[i] * .2126 + data[i + 1] * .7152 + data[i + 2] * .0722;
    for (let p = 0, i = 0; p < lum.length; p++, i += 4) {
      const x = p % original.width, y = Math.floor(p / original.width);
      const gx = Math.abs(lum[p + (x < original.width - 1 ? 1 : 0)] - lum[p - (x > 0 ? 1 : 0)]);
      const gy = Math.abs(lum[p + (y < original.height - 1 ? original.width : 0)] - lum[p - (y > 0 ? original.width : 0)]);
      const tooth = ((p * 16807 >>> 4) % 17) / 17;
      const tone = clamp(255 - (1 - lum[p] / 255) * 17 - (gx + gy) * .75 + tooth * .8, 188, 255);
      out[i] = tone; out[i + 1] = tone; out[i + 2] = tone - .5; out[i + 3] = 255;
      // A small white pigment component keeps even dark blue strokes matte.
      for (let c = 0; c < 3; c++) data[i + c] = data[i + c] * .83 + 255 * .17;
    }
    base.putImageData(drawing, 0, 0); ctx.putImageData(original, 0, 0);
    this.dirty = true;this.fullRedraw=true;
  }
  add(from, to, time, pressure = .35) {
    if (!this.alive || !this.ready) return;
    const distance = Math.hypot(to.x - from.x, to.y - from.y);
    if (distance < .1 || distance > 260) return;
    this.expire(time);
    const index=Math.floor(time/INTERVAL);
    let mark=this.marks.at(-1);
    if (!mark || mark.index!==index) { mark={index,time,tiles:new Map()}; this.marks.push(mark); }
    const radius=48+clamp(pressure,0,1)*7;
    const angle=Math.atan2(to.y-from.y,to.x-from.x);
    const connected=this.lastPoint && Math.hypot(from.x-this.lastPoint.x,from.y-this.lastPoint.y)<3 && time-this.lastPoint.time<.22;
    if (!connected) { this.carry=0; this.stamp(mark,from.x,from.y,radius,angle); }
    for(let d=SPACING-this.carry;d<=distance;d+=SPACING){
      const t=d/distance;this.stamp(mark,from.x+(to.x-from.x)*t,from.y+(to.y-from.y)*t,radius,angle);
    }
    this.carry=(this.carry+distance)%SPACING;this.lastPoint={x:to.x,y:to.y,time};
    this.dirty = true;
  }
  stamp(mark,x,y,radius,angle) {
    const left=Math.max(0,Math.floor((x-radius)/TILE)),right=Math.min(Math.ceil(this.w/TILE)-1,Math.floor((x+radius)/TILE));
    const top=Math.max(0,Math.floor((y-radius)/TILE)),bottom=Math.min(Math.ceil(this.h/TILE)-1,Math.floor((y+radius)/TILE));
    for(let row=top;row<=bottom;row++)for(let col=left;col<=right;col++){
      const key=row*Math.ceil(this.w/TILE)+col;
      let tile=mark.tiles.get(key);
      if(!tile){
        const canvas=document.createElement('canvas');canvas.width=canvas.height=TILE*MASK_SCALE;
        const ctx=canvas.getContext('2d');if(!ctx)continue;ctx.scale(MASK_SCALE,MASK_SCALE);
        tile={canvas,ctx,x:col*TILE,y:row*TILE};mark.tiles.set(key,tile);
      }
      tile.ctx.save();tile.ctx.translate(x-tile.x,y-tile.y);tile.ctx.rotate(angle);
      tile.ctx.drawImage(this.tip,-radius,-radius,radius*2,radius*2);tile.ctx.restore();
    }
  }
  expire(time) {
    this.marks=this.marks.filter(mark=>{
      if(time-mark.time<HOLD+FADE)return true;
      for(const tile of mark.tiles.values())tile.canvas.width=tile.canvas.height=0;
      mark.tiles.clear();return false;
    });
  }
  clear() {
    for(const mark of this.marks){for(const tile of mark.tiles.values())tile.canvas.width=tile.canvas.height=0;mark.tiles.clear();}
    this.marks.length=0;this.lastPoint=null;this.carry=0;this.dirty=true;
  }
  draw(time) {
    if (!this.alive || !this.ctx || !this.ready || (!this.dirty && !this.marks.length)) return;
    const mask = this.mask.getContext('2d'), reveal = this.reveal.getContext('2d'), ctx = this.ctx;
    if (!mask || !reveal) return;
    this.expire(time);
    // Held strokes are identical between input events. Only redraw once their
    // opacity changes; keep the original time-based hold and fade unchanged.
    let changed=this.dirty,bounds=null;
    for(const mark of this.marks){
      const alpha=1-smooth((time-mark.time-HOLD)/FADE);
      if(mark.alpha!==alpha)changed=true;mark.alpha=alpha;
      for(const tile of mark.tiles.values()){
        if(!bounds)bounds={left:tile.x,top:tile.y,right:tile.x+TILE,bottom:tile.y+TILE};
        else{bounds.left=Math.min(bounds.left,tile.x);bounds.top=Math.min(bounds.top,tile.y);bounds.right=Math.max(bounds.right,tile.x+TILE);bounds.bottom=Math.max(bounds.bottom,tile.y+TILE);}
      }
    }
    // Expired strokes also need their previous rectangle restored to paper.
    const previous=this.paintedBounds;
    if(previous&&!bounds)changed=true;
    if(!changed)return;
    const area=this.fullRedraw?{left:0,top:0,right:this.w,bottom:this.h}:bounds&&previous?{
      left:Math.min(bounds.left,previous.left),top:Math.min(bounds.top,previous.top),
      right:Math.max(bounds.right,previous.right),bottom:Math.max(bounds.bottom,previous.bottom)
    }:bounds||previous;
    if(!area){this.dirty=false;return;}
    // Align the clip to physical pixels, including the resampling edge. Keep
    // full-image coordinates so DPR rounding and image sampling stay identical.
    const x=Math.max(0,Math.floor(area.left*this.scale)-2)/this.scale,y=Math.max(0,Math.floor(area.top*this.scale)-2)/this.scale;
    const right=Math.min(this.canvas.width,Math.ceil(area.right*this.scale)+2)/this.scale,bottom=Math.min(this.canvas.height,Math.ceil(area.bottom*this.scale)+2)/this.scale;
    for(const context of [mask,reveal,ctx]){context.save();context.beginPath();context.rect(x,y,right-x,bottom-y);context.clip();}
    mask.clearRect(x,y,right-x,bottom-y);
    for (const mark of this.marks) {
      mask.globalAlpha=mark.alpha;
      for(const tile of mark.tiles.values())mask.drawImage(tile.canvas,tile.x,tile.y,TILE,TILE);
    }
    mask.globalAlpha=1;
    reveal.clearRect(0, 0, this.w, this.h); reveal.globalCompositeOperation = 'source-over';
    reveal.drawImage(this.colour, 0, 0, this.w, this.h); reveal.globalCompositeOperation = 'destination-in';
    reveal.drawImage(this.mask, 0, 0, this.w, this.h); reveal.globalCompositeOperation = 'source-over';
    ctx.clearRect(0, 0, this.w, this.h); ctx.drawImage(this.base, 0, 0, this.w, this.h);
    ctx.globalAlpha = .9; ctx.drawImage(this.reveal, 0, 0, this.w, this.h); ctx.globalAlpha = 1;
    for(const context of [mask,reveal,ctx])context.restore();
    this.paintedBounds=bounds;this.fullRedraw=false;this.dirty = false;
  }
  destroy() {
    if (!this.alive) return;
    this.clear();this.alive = false; this.ready = false;
    if (this.image) { this.image.onload = this.image.onerror = null; this.image.removeAttribute('src'); this.image = null; }
    for (const canvas of [this.canvas, this.base, this.colour, this.mask, this.reveal,this.tip]) canvas.width = canvas.height = 0;
    this.ctx = null; this.invalidate = null;
  }
}
