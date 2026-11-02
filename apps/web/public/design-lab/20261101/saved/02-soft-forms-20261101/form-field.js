import { TAU, clamp, hash, noise } from './form-math.js';

const visible = rect => rect.bottom > -20 && rect.top < innerHeight + 20;
function size(canvas, w, h, dpr = Math.min(devicePixelRatio || 1, 1.5)) {
  canvas.width = Math.max(1, Math.round(w * dpr)); canvas.height = Math.max(1, Math.round(h * dpr));
  const ctx = canvas.getContext('2d'); ctx?.setTransform(dpr, 0, 0, dpr, 0, 0); return ctx;
}
function motif(ctx, kind, radius, angle = 0) {
  ctx.save(); ctx.rotate(angle); ctx.beginPath();
  if (kind === 0) {
    for (let i = 0; i < 5; i++) {
      const a = i / 5 * TAU; ctx.moveTo(Math.cos(a) * radius * .33, Math.sin(a) * radius * .33);
      ctx.ellipse(Math.cos(a) * radius * .48, Math.sin(a) * radius * .48, radius * .49, radius * .29, a, 0, TAU);
    }
    ctx.fill();
  } else if (kind === 1) {
    ctx.moveTo(-radius, 0); ctx.lineTo(radius, 0); ctx.moveTo(0, -radius); ctx.lineTo(0, radius); ctx.stroke();
  } else if (kind === 2) {
    for (let i = 0; i < 4; i++) { const a = i * TAU / 4; ctx.moveTo(Math.cos(a) * radius * .7 + radius * .22, Math.sin(a) * radius * .7); ctx.arc(Math.cos(a) * radius * .7, Math.sin(a) * radius * .7, radius * .22, 0, TAU); }
    ctx.fill();
  } else {
    ctx.moveTo(0, -radius); ctx.lineTo(radius * .65, 0); ctx.lineTo(0, radius); ctx.lineTo(-radius * .65, 0); ctx.closePath(); ctx.stroke();
  }
  ctx.restore();
}

// Pixel materials, not soft-body geometry. All positions stay on an unwarped grid.
export class SurfacePrint {
  constructor(canvas, mode = 'dots', inverse = false) { this.canvas = canvas; this.mode = mode; this.inverse = inverse; this.alive = true; this.phase = hash(mode.length, inverse ? 1 : 0) * TAU; }
  resize(w, h) { if (!this.alive) return; this.w = w; this.h = h; this.ctx = size(this.canvas, w, h, 1.25); }
  draw(time, pointer, hover, press) {
    if (!this.alive || !this.ctx) return;
    const ctx = this.ctx, w = this.w, h = this.h;
    ctx.clearRect(0, 0, w, h);
    const color = this.inverse ? '238,247,246' : '111,155,189';
    const centerX = w * .78, centerY = h * 1.03;
    // Precise concentric arcs give the surface a satin screen-print, without distortion.
    for (let i = 0; i < 7; i++) {
      ctx.beginPath(); ctx.arc(centerX, centerY, 12 + i * 8, -Math.PI, 0);
      ctx.strokeStyle = `rgba(${color},${(.055 + hover * .04).toFixed(3)})`; ctx.lineWidth = i % 2 ? .5 : 1.1; ctx.stroke();
    }
    const pitch = this.mode === 'fine' ? 5 : 8;
    for (let y = 4; y < h; y += pitch) for (let x = 4; x < w; x += pitch) {
      const distance = Math.hypot(x - pointer.x, y - pointer.y);
      const light = hover * Math.exp(-distance * distance / 2200);
      const sweep = Math.pow(Math.max(0, Math.sin(x * .035 + y * .02 - time * .6 + this.phase)), 8);
      const density = noise(x * .06 + this.phase, y * .06) * .5 + sweep * .5;
      const radius = .25 + density * .56 + light * .57 + press * .22;
      ctx.fillStyle = `rgba(${color},${(.12 + light * .22 + press * .08).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, TAU); ctx.fill();
    }
    // A narrow printed line is revealed by opacity, never by stretching its shape.
    const band = (time * 20 + this.phase * 20) % (w + 100) - 50;
    const light = ctx.createLinearGradient(band - 35, 0, band + 35, 0);
    light.addColorStop(0, `rgba(${color},0)`); light.addColorStop(.5, `rgba(${color},${.035 + hover * .07})`); light.addColorStop(1, `rgba(${color},0)`);
    ctx.fillStyle = light; ctx.fillRect(0, 0, w, h);
  }
  destroy() { if (!this.alive) return; this.alive = false; this.canvas.width = this.canvas.height = 0; this.ctx = null; }
}

// One shared clock is supplied by FormStudy. This class creates no async work.
export class VisualField {
  constructor(canvas) {
    this.alive = true; this.canvas = canvas; this.lastDraw = -1;
    this.patterns = [...document.querySelectorAll('[data-pattern],[data-trim]')].map(node => ({ node, type: node.dataset.pattern || node.dataset.trim, trim: node.hasAttribute('data-trim') }));
    this.resize();
  }
  resize() { if (!this.alive) return; this.w = innerWidth; this.h = innerHeight; this.ctx = size(this.canvas, this.w, this.h); this.canvas.dataset.renderer = this.ctx ? 'canvas2d' : 'css-fallback'; this.measure(true); }
  measure(resize = false) {
    if (!this.alive) return;
    for (const item of this.patterns) {
      const rect = item.node.getBoundingClientRect();
      if (resize || !item.rect || Math.abs(item.rect.width - rect.width) > .5 || Math.abs(item.rect.height - rect.height) > .5) item.ctx = size(item.node, rect.width, rect.height);
      item.rect = rect;
    }
  }
  draw(time, pointer, force = false) {
    if (!this.alive || (!force && time - this.lastDraw < .031)) return;
    this.lastDraw = time;
    if (this.ctx) this.background(time, pointer);
    for (const item of this.patterns) if (item.ctx && visible(item.rect)) item.trim ? this.trim(item, time, pointer) : this.pattern(item, time, pointer);
  }
  background(time, pointer) {
    const ctx = this.ctx, w = this.w, h = this.h;
    ctx.fillStyle = '#e1eaf0'; ctx.fillRect(0, 0, w, h);
    const wash = ctx.createRadialGradient(w * .78, h * .14, 0, w * .78, h * .14, w * .9);
    wash.addColorStop(0, '#edf1ef'); wash.addColorStop(.55, '#e3ebf0'); wash.addColorStop(1, '#d7e4ef');
    ctx.fillStyle = wash; ctx.fillRect(0, 0, w, h);
    const lavender = ctx.createRadialGradient(w * .07, h * .77, 0, w * .07, h * .77, w * .55);
    lavender.addColorStop(0, '#d8deee77'); lavender.addColorStop(1, '#d8deee00'); ctx.fillStyle = lavender; ctx.fillRect(0, 0, w, h);
    // Precise circles and elliptical petals sit in the negative space as rigid layers.
    if (w > 620) for (const side of [0, 1]) {
      const cx = side ? w - 20 : 30, cy = h * (side ? .73 : .4);
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(time * (side ? -.012 : .012));
      for (let ring = 0; ring < 3; ring++) {
        ctx.strokeStyle = ring % 2 ? '#f5f8f3aa' : '#8faec530'; ctx.lineWidth = ring % 2 ? 1.1 : .7;
        for (let petal = 0; petal < 6; petal++) {
          ctx.save(); ctx.rotate(petal / 6 * TAU); ctx.beginPath(); ctx.ellipse(37 + ring * 7, 0, 48 + ring * 7, 17 + ring * 2, 0, 0, TAU); ctx.stroke(); ctx.restore();
        }
        ctx.beginPath(); ctx.arc(0, 0, 16 + ring * 29, 0, TAU); ctx.stroke();
      }
      ctx.restore();
    }
    const pitch = w < 620 ? 39 : 47;
    for (let row = 0; row < h / pitch + 1; row++) for (let col = 0; col < w / pitch + 1; col++) {
      const x = col * pitch + 17, y = row * pitch + 17, seed = hash(col, row);
      const distance = Math.hypot(x - pointer.x, y - pointer.y), near = pointer.inside ? Math.exp(-distance * distance / 18000) : 0;
      const sweep = Math.pow(Math.max(0, Math.sin(col * .2 + row * .1 - time * .22)), 12);
      const alpha = .035 + sweep * .08 + near * .32;
      ctx.fillStyle = `rgba(105,148,183,${alpha})`; ctx.strokeStyle = `rgba(105,148,183,${alpha})`; ctx.lineWidth = .8;
      ctx.save(); ctx.translate(x, y);
      if (seed > .68 || near > .18) motif(ctx, Math.floor(seed * 4), 2.6 + seed * 2, time * .08 * (col % 2 ? 1 : -1));
      else { ctx.beginPath(); ctx.arc(0, 0, .65, 0, TAU); ctx.fill(); }
      if (near > .13) { ctx.strokeStyle = `rgba(119,157,188,${near * .13})`; ctx.beginPath(); ctx.arc(0, 0, 11, seed * TAU, seed * TAU + Math.PI * 1.3); ctx.stroke(); }
      ctx.restore();
    }
    ctx.fillStyle = '#607d9b0c';
    for (let i = 0; i < Math.min(900, w * h / 1000); i++) ctx.fillRect(hash(i, 5) * w, hash(i, 8) * h, .6, .6);
  }
  pattern(item, time, pointer) {
    const { ctx, rect: r, type } = item, w = r.width, h = r.height;
    ctx.clearRect(0, 0, w, h); ctx.fillStyle = type === 'halftone' ? '#e0eaf0' : type === 'ribbons' ? '#d5e5ed' : type === 'ruling' ? '#dce8ee' : '#c8deeb'; ctx.fillRect(0, 0, w, h);
    const local = { x: pointer.x - r.left, y: pointer.y - r.top };
    if (type === 'garden') {
      const pitch = w < 200 ? 24 : 36;
      for (let y = 13, row = 0; y < h + 15; y += pitch, row++) for (let x = 12 + row % 2 * pitch / 2, col = 0; x < w + 13; x += pitch, col++) {
        const seed = hash(col + 10, row + 1), near = pointer.inside ? Math.exp(-((local.x - x) ** 2 + (local.y - y) ** 2) / 8000) : 0;
        const phase = time * .16 + seed * TAU;
        ctx.save(); ctx.translate(x, y); ctx.fillStyle = seed > .4 ? '#f1f7f3' : '#8fb4cf99'; ctx.strokeStyle = '#8eafc7b3'; ctx.lineWidth = .9;
        motif(ctx, Math.floor(seed * 4), seed > .4 ? 4.2 : 3.1, phase + near * .35); ctx.restore();
      }
    } else if (type === 'ribbons') {
      const pitch = w < 200 ? 23 : 30;
      for (let row = -1; row < h / pitch + 1; row++) for (let col = -1; col < w / pitch + 1; col++) {
        const seed = hash(col + 20, row + 4), alpha = .22 + .14 * Math.sin(time * .45 + seed * TAU);
        ctx.save(); ctx.translate(col * pitch + pitch / 2, row * pitch + pitch / 2); ctx.rotate(seed > .5 ? 0 : Math.PI / 2);
        ctx.lineWidth = 1.5; ctx.strokeStyle = `rgba(132,171,200,${alpha})`;
        ctx.beginPath(); ctx.arc(-pitch / 2, -pitch / 2, pitch / 2, 0, Math.PI / 2); ctx.moveTo(0, pitch / 2); ctx.arc(pitch / 2, pitch / 2, pitch / 2, Math.PI, Math.PI * 1.5); ctx.stroke();
        ctx.strokeStyle = '#f6f8f1b0'; ctx.lineWidth = .8; ctx.beginPath(); ctx.arc(0, 0, 2.3, 0, TAU); ctx.stroke(); ctx.restore();
      }
    } else if (type === 'halftone') {
      for (let y = 4; y < h; y += 6) for (let x = 4; x < w; x += 6) {
        const light = (Math.sin(x * .05 + y * .035 - time * .45) + 1) / 2;
        const near = pointer.inside ? Math.exp(-((local.x - x) ** 2 + (local.y - y) ** 2) / 3500) : 0;
        const radius = clamp(.4 + light * 1.1 + near * .4, .4, 1.9);
        ctx.fillStyle = '#7d9fbc99'; ctx.beginPath(); ctx.arc(x, y, radius, 0, TAU); ctx.fill();
      }
    } else {
      for (let x = -h; x < w + h; x += 7) {
        const opacity = .1 + .1 * (Math.sin(x * .04 - time * .5) + 1) / 2;
        ctx.strokeStyle = `rgba(103,148,183,${opacity})`; ctx.lineWidth = .7; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x + h * .55, h); ctx.stroke();
      }
      ctx.strokeStyle = '#f6faf2a6'; ctx.lineWidth = .6;
      for (let y = 3; y < h; y += 14) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
    }
  }
  trim(item, time, pointer) {
    const { ctx, rect: r, type } = item, w = r.width, h = r.height, center = h / 2;
    ctx.clearRect(0, 0, w, h); ctx.lineWidth = 1;
    if (type === 'lace') {
      for (let x = 10, i = 0; x < w; x += 21, i++) {
        const alpha = .28 + .3 * (Math.sin(time * .8 - i * .3) + 1) / 2;
        ctx.strokeStyle = `rgba(113,154,187,${alpha})`; ctx.beginPath(); ctx.arc(x, center, 6, 0, TAU); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + 7, center); ctx.lineTo(x + 14, center); ctx.stroke();
      }
    } else if (type === 'weave') {
      for (let lane = 0; lane < 3; lane++) {
        ctx.setLineDash([8 + lane * 4, 7]); ctx.lineDashOffset = -time * (12 + lane * 6);
        ctx.strokeStyle = ['#8aabc0b8', '#bcc5dba6', '#8aabc066'][lane]; ctx.beginPath(); ctx.moveTo(0, center - 5 + lane * 5); ctx.lineTo(w, center - 5 + lane * 5); ctx.stroke();
      }
      ctx.setLineDash([]);
    } else {
      for (let x = 12, i = 0; x < w; x += 27, i++) {
        const near = pointer.inside ? Math.exp(-((pointer.x - r.left - x) ** 2 + (pointer.y - r.top - center) ** 2) / 8000) : 0;
        ctx.save(); ctx.translate(x, center); ctx.fillStyle = '#adc8dbb0'; ctx.strokeStyle = '#8aa9c0'; ctx.lineWidth = .8;
        motif(ctx, i % 4, 3.4, time * .25 + near * .4); ctx.restore();
      }
    }
  }
  destroy() {
    if (!this.alive) return; this.alive = false;
    for (const item of this.patterns) { item.node.width = item.node.height = 0; item.ctx = null; }
    this.canvas.width = this.canvas.height = 0; this.ctx = null; this.patterns.length = 0;
  }
}
