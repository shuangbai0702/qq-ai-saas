import { TAU, clamp, hash, fbm, warp } from './paper-math.js';

function canvasSize(canvas, width, height) {
  const dpr = Math.min(devicePixelRatio || 1, 1.5);
  canvas.width = Math.max(1, Math.round(width * dpr));
  canvas.height = Math.max(1, Math.round(height * dpr));
  const ctx = canvas.getContext('2d');
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}
function strokePoints(ctx, points, close = false) {
  ctx.beginPath();
  points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  if (close) ctx.closePath();
  ctx.stroke();
}
function flower(ctx, radius, time, open = 0) {
  ctx.beginPath();
  for (let i = 0; i <= 64; i++) {
    const a = i / 64 * TAU, r = radius * (.82 + (.18 + open * .09) * Math.cos(a * 5 + Math.sin(time * .5) * .2));
    const x = Math.cos(a) * r, y = Math.sin(a) * r;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.closePath(); ctx.fill();
}

// Canvas layers deliberately have no RAF, timers, subscriptions or pointer handlers.
export class NotebookField {
  constructor(canvas) {
    this.canvas = canvas;
    this.alive = true;
    this.patterns = [...document.querySelectorAll('[data-pattern]')].map(node => ({ node, type: node.dataset.pattern, rect: null, ctx: null }));
    this.trims = [...document.querySelectorAll('[data-trim]')].map(node => ({ node, type: node.dataset.trim, rect: null, ctx: null }));
    this.lastDraw = -1;
    this.resize();
  }
  resize() {
    if (!this.alive) return;
    this.width = innerWidth; this.height = innerHeight;
    this.ctx = canvasSize(this.canvas, this.width, this.height);
    this.canvas.dataset.renderer = this.ctx ? 'canvas2d' : 'css-fallback';
    this.measure(true);
  }
  measure(resize = false) {
    if (!this.alive) return;
    for (const item of [...this.patterns, ...this.trims]) {
      const rect = item.node.getBoundingClientRect();
      if (resize || !item.rect || Math.abs(item.rect.width - rect.width) > .5 || Math.abs(item.rect.height - rect.height) > .5) item.ctx = canvasSize(item.node, rect.width, rect.height);
      item.rect = rect;
    }
  }
  draw(time, pointer, force = false) {
    if (!this.alive || (!force && time - this.lastDraw < .031)) return;
    this.lastDraw = time;
    if (this.ctx) this.background(time, pointer);
    for (const item of this.patterns) if (item.ctx && item.rect.bottom > -20 && item.rect.top < innerHeight + 20) this.pattern(item, time, pointer);
    for (const item of this.trims) if (item.ctx && item.rect.bottom > -20 && item.rect.top < innerHeight + 20) this.trim(item, time, pointer);
  }
  background(time, pointer) {
    const ctx = this.ctx, w = this.width, h = this.height;
    ctx.fillStyle = '#e1e9ef'; ctx.fillRect(0, 0, w, h);
    const wash = ctx.createRadialGradient(w * .79, h * .15, 10, w * .79, h * .15, w * .88);
    wash.addColorStop(0, '#edf0ee'); wash.addColorStop(.55, '#e4ebf0'); wash.addColorStop(1, '#d5e3ed');
    ctx.fillStyle = wash; ctx.fillRect(0, 0, w, h);
    const lavender = ctx.createRadialGradient(w * .08, h * .82, 5, w * .08, h * .82, w * .6);
    lavender.addColorStop(0, '#d5dbe978'); lavender.addColorStop(1, '#d5dbe900');
    ctx.fillStyle = lavender; ctx.fillRect(0, 0, w, h);
    ctx.lineWidth = .7; ctx.strokeStyle = '#7fa0bb32';
    for (let y = -50; y < h + 70; y += 29) {
      const points = [];
      for (let x = -40; x <= w + 45; x += 28) points.push(warp(x, y, time, pointer, 1));
      strokePoints(ctx, points);
    }
    ctx.strokeStyle = '#91adc51e';
    for (let x = 15; x < w + 60; x += 116) {
      const points = [];
      for (let y = -30; y <= h + 50; y += 32) points.push(warp(x, y, time, pointer, 1));
      strokePoints(ctx, points);
    }
    // A notebook's scalloped selvedge, generated along the viewport rather than a bitmap.
    const edge = w < 620 ? 5 : 13;
    ctx.beginPath(); ctx.moveTo(0, -50); ctx.lineTo(edge, -50);
    for (let y = -50; y < h + 50; y += 47) {
      const puff = edge + (w < 620 ? 4 : 12) + Math.sin(time * .35 + y * .006) * 1.3;
      ctx.bezierCurveTo(puff, y + 10, puff, y + 36, edge, y + 47);
    }
    ctx.lineTo(0, h + 50); ctx.closePath(); ctx.fillStyle = '#7089a249'; ctx.fill();
    // Embossed contour printing in the margins. The page itself remains the subject.
    if (w > 620) {
      for (const side of [0, 1]) {
        const cx = side ? w - 25 : 29, cy = h * (side ? .73 : .39);
        for (let ring = 0; ring < 7; ring++) {
          const points = [];
          for (let i = 0; i <= 160; i++) {
            const a = i / 160 * TAU, rr = (47 + ring * 11) * (1 + .11 * Math.cos(a * 5 + time * .13 + side));
            points.push(warp(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr * 1.32, time, pointer, .75, side));
          }
          ctx.strokeStyle = ring % 2 ? '#f1f5f37a' : '#8aa9bf25'; ctx.lineWidth = ring % 2 ? 1.5 : .8;
          strokePoints(ctx, points, true);
        }
      }
    }
    ctx.fillStyle = '#718ba514';
    for (let i = 0; i < Math.min(1100, w * h / 1100); i++) {
      const x = hash(i, 2) * w, y = hash(i, 3) * h;
      ctx.fillRect(x, y, .65, .65);
    }
  }
  pattern(item, time, pointer) {
    const { ctx, rect: r, type } = item, w = r.width, h = r.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = type === 'halftone' ? '#e4edf0' : type === 'ribbons' ? '#d2e2eb' : type === 'ruling' ? '#dbe7ec' : '#bed7e7';
    ctx.fillRect(0, 0, w, h);
    const map = (x, y, strength = .65) => {
      const p = warp(x + r.left, y + r.top, time, pointer, strength);
      return { x: p.x - r.left, y: p.y - r.top };
    };
    const localPointer = { x: pointer.x - r.left, y: pointer.y - r.top, inside: pointer.inside };
    if (type === 'garden') {
      const size = w < 200 ? 36 : 53;
      for (let row = -1; row < h / size + 2; row++) for (let col = -1; col < w / size + 2; col++) {
        const x = col * size + (row % 2 ? size / 2 : 0), y = row * size;
        const p = map(x, y, .85), seed = hash(col + 71, row + 20), mark = Math.floor(seed * 5);
        const dist = Math.hypot(localPointer.x - x, localPointer.y - y), near = pointer.inside ? Math.exp(-dist * dist / 13000) : 0;
        const sway = Math.sin(time * .55 + row * .7 + col * .3) * .1 + near * .5;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(sway + (seed - .5) * .25); ctx.scale(1 + near * .26, 1 - near * .09);
        ctx.fillStyle = '#f0f5f3'; ctx.strokeStyle = '#86adc8'; ctx.lineWidth = 1.1;
        if (mark === 0 || mark === 4) {
          flower(ctx, (mark === 4 ? 5.2 : 7.1) + near * 2, time + seed * 5, near);
          ctx.beginPath(); ctx.arc(0, 0, 1.2, 0, TAU); ctx.fillStyle = '#9dbed4'; ctx.fill();
        } else if (mark === 1) {
          const open = 9 + near * 5, rise = 7 + Math.sin(time * .5 + seed * 4) * 1.2;
          ctx.beginPath(); ctx.moveTo(0, 0); ctx.bezierCurveTo(-open * 1.8, -rise * 1.7, -open * 1.7, rise * .9, 0, 0); ctx.bezierCurveTo(open * 1.7, rise * .9, open * 1.8, -rise * 1.7, 0, 0); ctx.fillStyle = '#94b9d173'; ctx.fill(); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(-1, 1); ctx.quadraticCurveTo(-2 - near * 4, 9, -6 - near * 3, 12); ctx.moveTo(1, 1); ctx.quadraticCurveTo(5, 6, 7 + near * 3, 11); ctx.stroke();
          ctx.beginPath(); ctx.arc(0, 0, 1.8, 0, TAU); ctx.fillStyle = '#eaf3f4'; ctx.fill();
        } else if (mark === 2) {
          ctx.beginPath(); ctx.moveTo(-9, 4); ctx.bezierCurveTo(-15, 0, -8, -7, -4, -4); ctx.bezierCurveTo(-2, -12, 8, -10, 8, -3); ctx.bezierCurveTo(16, -4, 16, 6, 8, 6); ctx.lineTo(-7, 6); ctx.closePath(); ctx.fillStyle = '#edf4f2b3'; ctx.fill();
        } else {
          const rr = 5 + near * 4;
          ctx.beginPath(); ctx.moveTo(0, -rr); ctx.quadraticCurveTo(1, -1, rr, 0); ctx.quadraticCurveTo(1, 1, 0, rr); ctx.quadraticCurveTo(-1, 1, -rr, 0); ctx.quadraticCurveTo(-1, -1, 0, -rr); ctx.fill();
          ctx.beginPath(); ctx.arc(11, -8, 1.3, 0, TAU); ctx.fillStyle = '#86abc480'; ctx.fill();
        }
        ctx.restore();
      }
    } else if (type === 'ribbons') {
      const size = w < 150 ? 24 : 32;
      ctx.lineWidth = w < 150 ? 1.5 : 2;
      for (let y = -1; y < h / size + 1; y++) for (let x = -1; x < w / size + 1; x++) {
        const flip = hash(x + 40, y + 13) > .5;
        for (let arc = 0; arc < 2; arc++) {
          const points = [];
          for (let i = 0; i <= 12; i++) {
            const a = i / 12 * Math.PI / 2;
            let xx = arc ? size - Math.cos(a) * size / 2 : Math.sin(a) * size / 2;
            let yy = arc ? size - Math.sin(a) * size / 2 : Math.cos(a) * size / 2;
            if (flip) xx = size - xx;
            points.push(map(x * size + xx, y * size + yy, .95));
          }
          ctx.strokeStyle = (x + y + arc) % 3 ? '#f2f5f0b8' : '#8eb2cd7a'; strokePoints(ctx, points);
        }
      }
    } else if (type === 'halftone') {
      const size = 6.5;
      for (let y = -2; y < h + 5; y += size) for (let x = -2; x < w + 5; x += size) {
        const p = map(x, y, .75), d = Math.hypot(localPointer.x - x, localPointer.y - y);
        const value = fbm(x * .014 + time * .09, y * .014 + 6) + Math.sin(x * .05 + y * .04 - time * .5) * .18;
        const radius = clamp(.4 + value * 2.2 + (pointer.inside ? Math.exp(-d * d / 4000) * 1.1 : 0), .3, 2.55);
        ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, TAU); ctx.fillStyle = '#7f9fbc88'; ctx.fill();
      }
    } else {
      ctx.lineWidth = .8;
      for (let y = -25; y < h + 40; y += 9) {
        const points = [];
        for (let x = -30; x <= w + 40; x += 9) points.push(map(x, y + Math.sin(x * .035 + y * .06 + time * .35) * 3.5, 1.1));
        ctx.strokeStyle = y % 2 ? '#8daec568' : '#f5f7f1a0'; strokePoints(ctx, points);
      }
    }
    ctx.fillStyle = '#5b7c9b0d';
    for (let i = 0; i < Math.min(400, w * h / 80); i++) ctx.fillRect(hash(i, 4) * w, hash(i, 5) * h, .6, .6);
  }
  trim(item, time, pointer) {
    const { ctx, rect: r, type } = item, w = r.width, h = r.height, center = h / 2;
    ctx.clearRect(0, 0, w, h); ctx.lineWidth = 1;
    const px = pointer.x - r.left, py = pointer.y - r.top;
    const influence = x => pointer.inside ? Math.exp(-((x - px) ** 2 + py ** 2) / 10000) : 0;
    if (type === 'lace') {
      for (let lane = 0; lane < 2; lane++) {
        const points = [];
        for (let x = 0; x <= w; x += 2) {
          const wave = Math.sin(x / 22 * TAU + time * .6) * (2.6 + influence(x) * 5);
          points.push({ x, y: center + (lane ? -1 : 1) * wave + (lane ? -3 : 3) });
        }
        ctx.strokeStyle = lane ? '#91adc47a' : '#799bb6ad'; strokePoints(ctx, points);
      }
      for (let x = 5; x < w; x += 11) { ctx.beginPath(); ctx.arc(x, center, .7, 0, TAU); ctx.fillStyle = '#879fb5'; ctx.fill(); }
    } else if (type === 'weave') {
      for (let lane = 0; lane < 3; lane++) {
        const points = [];
        for (let x = 0; x <= w; x += 2) points.push({ x, y: center + Math.sin(x / 58 * TAU - time * .5 + lane * TAU / 3) * (5 + influence(x) * 7) });
        ctx.strokeStyle = ['#8babc6ba', '#f5f7f0', '#a8b7ce'][lane]; ctx.lineWidth = lane === 1 ? 2.2 : 1.1; strokePoints(ctx, points);
      }
    } else {
      ctx.strokeStyle = '#8da8c1aa'; ctx.fillStyle = '#c4d7e4';
      for (let x = 12; x < w; x += 30) {
        const near = influence(x), phase = x * .04 + time * .45;
        ctx.save(); ctx.translate(x, center); ctx.rotate(Math.sin(phase) * .17 + near * .5);
        flower(ctx, 3.5 + near * 3 + Math.sin(phase) * .45, time, near);
        ctx.beginPath(); ctx.arc(14, 0, 1, 0, TAU); ctx.fillStyle = '#90abc2'; ctx.fill(); ctx.fillStyle = '#c4d7e4'; ctx.restore();
      }
    }
  }
  destroy() {
    if (!this.alive) return;
    this.alive = false;
    for (const item of [...this.patterns, ...this.trims]) { item.node.width = item.node.height = 0; item.ctx = null; }
    this.canvas.width = this.canvas.height = 0;
    this.ctx = null; this.patterns.length = this.trims.length = 0;
  }
}
