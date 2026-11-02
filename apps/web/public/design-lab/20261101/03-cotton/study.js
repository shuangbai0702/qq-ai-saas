import { PaperRenderer, colors } from './paper-renderer.js';
import { DryInk } from './dry-ink.js';
import { WallpaperPaint } from './wallpaper-paint.js';
import { PigmentFeedback } from './pigment-feedback.js';
import { PaperDetails } from './refinement.js';

const NS = 'http://www.w3.org/2000/svg', TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hash = value => { const n = Math.sin(value * 127.1 + 23.1) * 43758.5453; return n - Math.floor(n); };
const svg = (tag, attrs = {}, parent) => { const node = document.createElementNS(NS, tag); for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value); parent?.append(node); return node; };
const spline = points => {
  const f = n => n.toFixed(2), n = points.length; let d = `M${f(points[0].x)},${f(points[0].y)}`;
  for (let i = 0; i < n; i++) { const a = points[(i + n - 1) % n], b = points[i], c = points[(i + 1) % n], e = points[(i + 2) % n]; d += `C${f(b.x + (c.x - a.x) / 6)},${f(b.y + (c.y - a.y) / 6)} ${f(c.x - (e.x - b.x) / 6)},${f(c.y - (e.y - b.y) / 6)} ${f(c.x)},${f(c.y)}`; }
  return d + 'Z';
};
function contour(w, h, radius, cut) {
  if (['paper', 'torn', 'angle', 'backing'].includes(cut)) {
    let corners = cut === 'angle' ? [[1,2],[w-1,0],[w,h-3],[19,h],[0,h-17]] : cut === 'backing' ? [[1,4],[w-14,0],[w,13],[w-1,h-5],[w-20,h],[2,h-3]] : [[0,2],[w-16,0],[w,12],[w-1,h-5],[20,h],[1,h-7]];
    if (cut === 'torn') {
      corners = [[1,1],[w-1,0],[w,h-7]];
      for (let x = w - 4; x > 3; x -= 4) corners.push([x, h - 2.8 + (hash(x + w) - .5) * 3.6 + Math.sin(x * .09) * .5]);
      corners.push([0,h-5]);
    }
    const points = [];
    for (let i = 0; i < corners.length; i++) {
      const a = corners[i], b = corners[(i + 1) % corners.length], dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx,dy), steps = Math.max(1, Math.ceil(length / 4));
      for (let j = 0; j < steps; j++) { const t = j / steps, fuzz = (hash(i * 91 + j) - .5) * .12; points.push({ x:a[0] + dx * t + dy / length * fuzz, y:a[1] + dy * t - dx / length * fuzz, nx:dy / length, ny:-dx / length }); }
    }
    return points;
  }
  const r = Math.min(radius, w / 2, h / 2), hor = Math.max(0, w - 2 * r), vert = Math.max(0, h - 2 * r), arc = Math.PI * r / 2;
  const lengths = [hor, arc, vert, arc, hor, arc, vert, arc], total = lengths.reduce((a, b) => a + b, 0), count = cut === 'scallop' ? Math.min(300, Math.max(120, Math.round(total / 2.8))) : Math.min(600, Math.max(96, Math.round(total / 4)));
  const lobes = Math.max(7, Math.round(total / 18));
  return Array.from({ length: count }, (_, i) => {
    const u = i / count;
    if (cut === 'flower') { const a = u * TAU - Math.PI / 2, rr = Math.min(w, h) * (.456 + .044 * Math.cos(a * 6)); return { x: w / 2 + Math.cos(a) * rr, y: h / 2 + Math.sin(a) * rr, nx: Math.cos(a), ny: Math.sin(a) }; }
    let dist = total * u, seg = 0; while (seg < 7 && dist > lengths[seg]) dist -= lengths[seg++]; const t = lengths[seg] ? dist / lengths[seg] : 0;
    let x, y, nx = 0, ny = 0;
    if (seg === 0) { x = r + hor * t; y = 0; ny = -1; } else if (seg === 2) { x = w; y = r + vert * t; nx = 1; } else if (seg === 4) { x = w - r - hor * t; y = h; ny = 1; } else if (seg === 6) { x = 0; y = h - r - vert * t; nx = -1; } else { const angle = [-Math.PI / 2, 0, Math.PI / 2, Math.PI][(seg - 1) / 2] + t * Math.PI / 2; nx = Math.cos(angle); ny = Math.sin(angle); x = (seg === 1 || seg === 3 ? w - r : r) + nx * r; y = (seg === 1 || seg === 7 ? r : h - r) + ny * r; }
    const fuzz = (Math.sin(u * TAU * 37) * .028 + Math.cos(u * TAU * 53) * .018) * (w > 200 ? 1.2 : .65);
    let offset = fuzz;
    if (cut === 'scallop') offset += (Math.cos(u * TAU * lobes) - 1) * 1.3;
    if (cut === 'ticket') offset -= 5.5 * Math.exp(-Math.pow((y - h / 2) / 6.5, 4)) * Math.abs(nx);
    return { x: x + nx * offset, y: y + ny * offset, nx, ny };
  });
}
function prepareCanvas(canvas, w, h) { const dpr = Math.min(devicePixelRatio || 1, 1.25); canvas.width = Math.max(1, Math.round(w * dpr)); canvas.height = Math.max(1, Math.round(h * dpr)); canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0); }
let serial = 0;

class CottonSurface {
  constructor(node, renderer) {
    this.node = node; this.renderer = renderer; this.alive = true; this.id = `cotton-${++serial}`; this.phase = hash(serial) * TAU; this.hover = this.press = 0; this.pressed = this.focused = false; this.drawn = false; this.points = []; this.pencilLength = 0;
    this.quiet = !!node.closest('.refinement-panel,.input-shell,.paper-disclosure');
    if (node.matches('.ui-button')) { this.ink = document.createElement('span'); this.ink.className = 'ink'; this.ink.append(...node.childNodes); node.append(this.ink); }
    this.svg = svg('svg', { class: 'stock-art', 'aria-hidden': 'true', focusable: 'false' });
    const defs = svg('defs', {}, this.svg); this.shape = svg('path', { id: this.id + '-shape' }, defs);
    svg('use', { href: '#' + this.id + '-shape' }, svg('clipPath', { id: this.id + '-clip' }, defs));
    this.rimGradient = svg('linearGradient', { id: this.id + '-rim', x1: '.1', y1: '0', x2: '.8', y2: '1' }, defs);
    this.rimStops = [svg('stop', { offset: '0' }, this.rimGradient), svg('stop', { offset: '.45' }, this.rimGradient), svg('stop', { offset: '1' }, this.rimGradient)];
    const shadow = svg('filter', { id: this.id + '-shadow', x: '-40%', y: '-80%', width: '180%', height: '300%', 'color-interpolation-filters': 'sRGB' }, defs);
    this.shadow = svg('feDropShadow', { dx: '0', dy: '4', stdDeviation: '4', 'flood-color': '#8b8170', 'flood-opacity': '.13' }, shadow);
    this.base = svg('use', { href: '#' + this.id + '-shape', fill: '#ddc9c6', filter: `url(#${this.id}-shadow)` }, this.svg);
    this.depths = [1, 2].map(n => svg('use', { href: '#' + this.id + '-shape', class: 'stock-edge', stroke: '#c4c0b6', transform: `translate(0,${n * .65})` }, this.svg));
    const layer = svg('g', { 'clip-path': `url(#${this.id}-clip)` }, this.svg);
    this.foreign = svg('foreignObject', { x: 0, y: 0 }, layer); this.canvas = document.createElement('canvas'); this.canvas.className = 'stock-material'; this.foreign.append(this.canvas);
    this.feedbackForeign = svg('foreignObject', { x: 0, y: 0 }, layer); this.feedbackCanvas = document.createElement('canvas'); this.feedbackCanvas.className = 'pigment-layer'; this.feedbackForeign.append(this.feedbackCanvas);
    this.brushForeign = svg('foreignObject', { x: 0, y: 0 }, layer); const brushCanvas = document.createElement('canvas'); brushCanvas.className = 'brush-layer'; this.brushForeign.append(brushCanvas); this.brush = new DryInk(brushCanvas, 18);
    this.rim = svg('use', { href: '#' + this.id + '-shape', class: 'stock-rim', stroke: `url(#${this.id}-rim)` }, this.svg);
    this.inner = svg('use', { href: '#' + this.id + '-shape', class: 'stock-inner', stroke: '#fff8eb', opacity: '.6' }, this.svg);
    this.fibers = svg('path', { class: 'stock-edge', stroke: '#c5a8ae', opacity: '.24' }, this.svg);
    this.section = svg('path', { class: 'paper-section', fill: 'none', stroke: '#aaa69b', 'stroke-width': '.55', 'stroke-linecap': 'round' }, this.svg);
    this.pencil = svg('path', { class: 'paper-pencil', fill: 'none', stroke: '#898579', 'stroke-width': '.85', 'stroke-linecap': 'round' }, this.svg);
    this.transfer = svg('path', { class: 'paper-transfer', fill: 'none', stroke: '#d32a2f', 'stroke-width': '.65', 'stroke-linecap': 'round' }, this.svg);
    this.node.prepend(this.svg); this.node.classList.add('stock-ready'); this.paint(); this.resize();
  }
  paint() {
    this.stock = this.node.dataset.stock;
    if (this.node.matches('[role=switch]') && this.node.getAttribute('aria-checked') === 'false') this.stock = 'recess';
    const palette = colors[this.stock] || colors.cream;
    this.rimStops[0].setAttribute('stop-color', palette[2]); this.rimStops[1].setAttribute('stop-color', palette[1]); this.rimStops[2].setAttribute('stop-color', palette[1]);
    this.base.setAttribute('fill', palette[0]); this.inner.setAttribute('stroke', palette[2]); this.depths.forEach(node => node.setAttribute('stroke', palette[1])); this.drawn = false;
  }
  resize() {
    if (!this.alive) return;
    const w = this.node.offsetWidth, h = this.node.offsetHeight; if (!w || !h || (w === this.width && h === this.height)) return;
    this.width = w; this.height = h; const radius = Number(this.node.dataset.radius || parseFloat(getComputedStyle(this.node).getPropertyValue('--radius')) || 16);
    this.points = contour(w, h, radius, this.node.dataset.cut); this.shape.setAttribute('d', spline(this.points)); this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    this.foreign.setAttribute('width', w); this.foreign.setAttribute('height', h); prepareCanvas(this.canvas, w, h);
    this.feedbackForeign.setAttribute('width', w); this.feedbackForeign.setAttribute('height', h); prepareCanvas(this.feedbackCanvas, w, h);
    this.brushForeign.setAttribute('width', w); this.brushForeign.setAttribute('height', h); this.brush.resize(w, h);
    this.inner.setAttribute('transform', `translate(1,1) scale(${Math.max(.2, (w - 2) / w)},${Math.max(.2, (h - 2) / h)})`);
    const fibers = this.points.filter((_, i) => i % 6 === 0).map((p, i) => { const length = .15 + hash(i + w) * .3; return `M${p.x.toFixed(2)},${p.y.toFixed(2)}l${(p.nx * length).toFixed(2)},${(p.ny * length).toFixed(2)}`; }).join('');
    this.fibers.setAttribute('d', fibers); this.drawn = false;
    const inset = this.points.map((p,i) => ({ x:p.x-p.nx*(2.9+hash(i)*.35), y:p.y-p.ny*(2.9+hash(i+4)*.35) }));
    this.pencil.setAttribute('d', spline(inset));
    this.pencilLength = inset.reduce((sum,p,i) => { const n=inset[(i+1)%inset.length]; return sum+Math.hypot(p.x-n.x,p.y-n.y); },0);
    const bottom = this.points.filter(p=>p.ny>.65 && p.y>h*.72).filter((_,i)=>i%2===0);
    this.section.setAttribute('d',bottom.map((p,i)=>`M${(p.x-.5).toFixed(2)},${(p.y+.2).toFixed(2)}l${(.3+hash(i)*.7).toFixed(2)},${(1.1+hash(i+1)*1.3).toFixed(2)}`).join(''));
    this.transfer.setAttribute('d',`M${w-19} 4q5 -.6 11 .4m-10 1.2q4 -.2 8 .2m-7 1.3 5 .2`);
  }
  measure() { if (this.alive) this.rect = this.node.getBoundingClientRect(); }
  mark(from, to, time, pressure = .4) {
    if (!this.alive || this.quiet || !this.rect?.width || !this.rect.height) return;
    const local = p => ({ x:(p.x-this.rect.left)/this.rect.width*this.width, y:(p.y-this.rect.top)/this.rect.height*this.height });
    this.brush.add(local(from),local(to),time,pressure,false);
  }
  step(dt, pointer, light, time, running, paint) {
    if (!this.alive || !this.rect?.width || !this.rect.height || !this.width || !this.height) return;
    if (this.drawn && (this.rect.bottom < -30 || this.rect.top > innerHeight + 30)) return;
    const p = { x: (pointer.x - this.rect.left) / this.rect.width * this.width, y: (pointer.y - this.rect.top) / this.rect.height * this.height };
    const quiet = this.quiet || pointer.quiet;
    const inside = !quiet && pointer.inside && p.x >= 0 && p.y >= 0 && p.x <= this.width && p.y <= this.height;
    const blend = running ? 1 - Math.exp(-dt * 18) : 1;
    this.hover += ((inside || (!quiet && this.focused) ? 1 : 0) - this.hover) * blend; this.press += (((!quiet && this.pressed) ? 1 : 0) - this.press) * blend;
    if (paint || !this.drawn) {
      const contact = this.hover > .001 || this.press > .001;
      const key = [this.renderer.renderer,this.stock,this.width,this.height,this.rect.left.toFixed(1),this.rect.top.toFixed(1),this.hover.toFixed(3),this.press.toFixed(3),contact?p.x.toFixed(1):0,contact?p.y.toFixed(1):0].join('/');
      if (!this.drawn || this.materialKey !== key) {
        this.renderer.render(this.canvas, { width: this.width, height: this.height, origin: [this.rect.left, this.rect.top], light, pointer: [p.x, p.y], stock: this.stock, role: this.node.matches('button,.badge,.swatch,.material-object') ? 2 : 1, relief: Number(this.node.dataset.relief || .6), finish: this.node.dataset.finish === 'emboss' ? 1 : this.node.dataset.finish === 'screen' ? 2 : 0, time, hover: this.hover, press: this.press });
        this.materialKey = key; this.drawn = true;
      }
      const dx = .25, dy = 2.4 + this.hover * .8 - this.press * 1.7;
      this.shadow.setAttribute('dx', dx.toFixed(2)); this.shadow.setAttribute('dy', dy.toFixed(2)); this.shadow.setAttribute('stdDeviation', (2.1 - this.press * .9).toFixed(2));
      this.shadow.setAttribute('flood-opacity', (.10 - this.press * .035).toFixed(3));
      this.depths.forEach((node, i) => node.setAttribute('transform', `translate(0,${((i + 1) * (.85+this.hover*.25) * (1 - this.press * .68)).toFixed(2)})`));
      this.rimGradient.setAttribute('x1', '.1');
      this.rimGradient.setAttribute('y1', '0');
      this.brush.draw(time);
      this.section.setAttribute('opacity',(.35+this.hover*.25-this.press*.2).toFixed(3));
      this.section.setAttribute('transform',`translate(0,${(-this.press*.8).toFixed(2)})`);
      this.pencil.setAttribute('stroke-dasharray',`${(this.pencilLength * (.025+this.hover*.68)).toFixed(2)} ${this.pencilLength.toFixed(2)}`);
      this.pencil.setAttribute('stroke-dashoffset',(-this.pencilLength*.42).toFixed(2));
      this.pencil.setAttribute('opacity',(.15+this.hover*.7).toFixed(3));
      const hasAccent=this.node.matches('.primary,.layer-front,.material-object,.swatch');
      this.transfer.setAttribute('opacity',hasAccent ? (.18+this.hover*.48+this.press*.18).toFixed(3) : '0');
    }
    if (this.ink) this.ink.style.translate = `0 ${(this.press * 1.2).toFixed(3)}px`;
  }
  destroy() { if (!this.alive) return; this.alive = false; this.brush.destroy(); this.canvas.width = this.canvas.height = this.feedbackCanvas.width = this.feedbackCanvas.height = 0; this.svg.remove(); this.node.classList.remove('stock-ready'); if (this.ink) this.ink.replaceWith(...this.ink.childNodes); this.points.length = 0; }
}

class CottonStudy {
  constructor() {
    this.alive = true; this.life = new AbortController(); this.frame = this.time = this.last = this.lastPaint = 0; this.needsMeasure = this.forcePaint = true;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)'); this.running = !this.reduced.matches; this.pointer = { x: innerWidth * .6, y: innerHeight * .2, inside: false };
    this.renderer = new PaperRenderer(); this.background = document.querySelector('#paper-background'); this.resizeBackground();
    this.drawing = new WallpaperPaint(document.querySelector('#drawing-field'), () => { if (!this.alive) return; this.forcePaint = true; this.request(); }); this.lastContact = null; this.pointerDown = false;
    this.feedback = new PigmentFeedback();
    document.body.dataset.renderer = this.renderer.renderer; this.surfaces = [...document.querySelectorAll('[data-stock]')].map(node => new CottonSurface(node, this.renderer)); this.lookup = new Map(this.surfaces.map(surface => [surface.node, surface]));
    this.motion = document.querySelector('#motion'); this.segment = document.querySelector('.segmented'); this.skin = this.segment.querySelector('.segment-skin'); this.tab = { x: 3, width: 62 };
    this.rangeInput=document.querySelector('.range-shell input');this.rangePaint={alive:true,node:document.querySelector('.range-control'),feedbackCanvas:document.querySelector('.range-pigment'),kind:'range',active:false,fill:Number(this.rangeInput.value)/100};
    this.resizeRange();
    this.switches = [...document.querySelectorAll('.stock-switch')].map(node => ({ node, thumb: node.querySelector('.thumb'), x: 0 }));
    this.prepareOrnaments(); this.ro = new ResizeObserver(entries => { if (!this.alive) return; entries.forEach(entry => this.lookup.get(entry.target)?.resize()); this.needsMeasure = this.forcePaint = true; this.request(); }); this.surfaces.forEach(surface => this.ro.observe(surface.node));
    this.refinement = new PaperDetails(this);
    this.ro.observe(this.rangePaint.node);this.bind(); this.syncMotion(); this.request();
  }
  resizeBackground() { prepareCanvas(this.background, innerWidth, innerHeight); this.backgroundDrawn = false; }
  prepareOrnaments() {
    this.stamp = document.querySelector('.ink-stamp'); prepareCanvas(this.stamp, 92, 92); this.stampPoints = []; this.stampCoverage = .4;
    const bayer = [[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]];
    for (let row = 0; row < 48; row++) for (let col = 0; col < 48; col++) {
      const x = col * 1.8 + 3, y = row * 1.8 + 3, px = x - 46, py = y - 46;
      const a = Math.atan2(py, px), r = Math.hypot(px, py), edge = 24 + 7 * Math.cos(a * 5 + Math.PI / 2);
      if ((r < edge && r > 8) || r < 4.3) this.stampPoints.push({ x, y, threshold: bayer[row % 4][col % 4] / 16, seed: hash(row * 48 + col) });
    }
    const loader = document.querySelector('.flower-loader g'); this.loaderPetals = [];
    for (let i = 0; i < 6; i++) this.loaderPetals.push(svg('ellipse', { cx: 12, cy: 5.5, rx: 2, ry: 3.7, transform: `rotate(${i * 60} 12 12)` }, loader));
  }
  on(target, type, fn, options = {}) { target.addEventListener(type, fn, { ...options, signal: this.life.signal }); }
  request() { if (!this.alive || this.frame || document.hidden) return; this.frame = requestAnimationFrame(now => this.tick(now)); }
  resizeRange(){const surface=this.rangePaint,w=surface.node.clientWidth,h=surface.node.clientHeight;if(w===surface.width&&h===surface.height)return;this.feedback.remove(surface);surface.width=w;surface.height=h;prepareCanvas(surface.feedbackCanvas,w,h);}
  measure() { this.surfaces.forEach(surface => surface.measure()); this.resizeRange();this.refinement?.measure();this.needsMeasure = false; }
  tick(now) {
    this.frame = 0; if (!this.alive || document.hidden) return;
    const elapsed = this.last ? (now - this.last) / 1000 : 1 / 60, dt = elapsed; this.last = now;
    if (this.needsMeasure) this.measure(); if (this.running) this.time += elapsed;
    const paint = this.forcePaint || !this.running || now - this.lastPaint > 40;
    const light = this.pointer.inside ? [this.pointer.x, this.pointer.y] : [innerWidth * (.28 + Math.sin(this.time * .22) * .22), innerHeight * (.12 + Math.cos(this.time * .18) * .13)];
    this.details(dt, light);
    for (const surface of this.surfaces) surface.step(dt, this.pointer, light, this.time, this.running, paint);
    this.refinement.step(dt, this.pointer, this.time, this.running, paint);
    if (paint) {
      if (!this.backgroundDrawn) {
        this.renderer.render(this.background, { width: innerWidth, height: innerHeight, light: [-300,-400], pointer: [-1000,-1000], stock: 'ground', relief: 0, role: 0, time: 0, hover: 0, finish: 0 }); this.backgroundDrawn = true;
      }
      this.drawStamp(); this.drawing.draw(this.time); this.feedback.draw(this.time); this.lastPaint = now; this.forcePaint = false;
    }
    document.body.dataset.cotton = 'ready'; if (this.running) this.request();
  }
  drawStamp() {
    const ctx = this.stamp.getContext('2d'); if (!ctx) return;
    ctx.clearRect(0, 0, 92, 92);
    const coverage = this.stampCoverage;
    for (const p of this.stampPoints) {
      if (p.threshold > coverage) continue;
      ctx.fillStyle = `rgba(207,38,41,${(.45 + p.seed * .35).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(p.x + (p.seed - .5) * .25, p.y + (hash(p.x) - .5) * .25, .5 + p.seed * .26, 0, TAU); ctx.fill();
    }
  }
  details(dt, light) {
    const blend = this.running ? 1 - Math.exp(-dt * 19) : 1, active = this.segment.querySelector('[aria-pressed=true]');
    this.tab.x += (active.offsetLeft - this.tab.x) * blend; this.tab.width += (active.offsetWidth - this.tab.width) * blend; this.skin.style.left = this.tab.x.toFixed(2) + 'px'; this.skin.style.width = this.tab.width.toFixed(2) + 'px';
    for (const item of this.switches) {
      const travel=item.node.clientWidth-item.thumb.offsetWidth-6,checked=item.node.getAttribute('aria-checked')==='true';
      item.x += ((checked ? travel : 0) - item.x) * blend; item.thumb.style.transform = `translateX(${item.x.toFixed(2)}px)`;
      const amount=travel?item.x/travel:0;item.thumb.querySelector('.switch-check').style.opacity=amount.toFixed(3);item.thumb.querySelector('.switch-dash').style.opacity=(1-amount).toFixed(3);
      item.node.querySelector('.track-imprint').style.opacity=(.12+amount*.65).toFixed(3);
    }
    this.loaderPetals.forEach((petal, i) => petal.setAttribute('opacity', (.25 + .75 * (.5 + .5 * Math.sin(this.time * 2.1 - i * .8))).toFixed(3)));
    document.querySelectorAll('.loader-dots i').forEach((dot, i) => { dot.style.opacity = (.25 + .75 * (.5 + .5 * Math.sin(this.time * 2.1 - i * .8))).toFixed(3); });
    const line = document.querySelector('.loader-line path'); line.setAttribute('stroke-dasharray', '3 6'); line.setAttribute('stroke-dashoffset', (-this.time * 9).toFixed(2));
    for (const path of document.querySelectorAll('[data-stitch]')) { const width = path.ownerSVGElement.clientWidth; path.setAttribute('d', `M0 6Q${width*.3} 5.4 ${width*.5} 6.1T${width} 6`); path.setAttribute('stroke-dasharray', '2 1.4 7 .8'); }
    const front = this.lookup.get(document.querySelector('.layer-front'));
    this.stampCoverage += ((.38 + front.hover * .5 + front.press * .08) - this.stampCoverage) * blend;
  }
  release() { if (!this.alive) return; this.pointerDown = false;if(this.rangePaint.active)this.feedback.release(this.rangePaint,this.time);this.surfaces.forEach(surface => { surface.pressed = false; }); this.forcePaint = true; this.request(); }
  syncMotion() { document.body.classList.toggle('motion-paused', !this.running); document.body.dataset.motion = this.running ? 'on' : 'off'; this.motion.setAttribute('aria-pressed', String(this.running)); this.motion.setAttribute('aria-label', this.running ? '暂停视觉动态' : '开启视觉动态'); document.querySelector('#motion-label').textContent = this.running ? '动态开启' : '动态暂停'; }
  bind() {
    this.on(document, 'pointermove', event => {
      if (this.needsMeasure) this.measure();
      this.pointer = { x:event.clientX, y:event.clientY, inside:true, quiet:event.target instanceof Element&&!!event.target.closest('.refinement-panel,.input-shell,.paper-disclosure') }; const node = event.target instanceof Element && !event.target.closest('.range-control') ? event.target.closest('[data-stock]') : null, surface = this.lookup.get(node);
      if (this.lastContact) {
        this.drawing.add(this.lastContact.point,this.pointer,this.time,this.pointerDown ? .65 : .3);
        if (surface && !this.pointer.quiet && this.lastContact.node === node) surface.mark(this.lastContact.point,this.pointer,this.time,this.pointerDown ? .8 : .25);
      }
      this.lastContact = { node, point:{...this.pointer} }; this.forcePaint = true; this.request();
    }, { passive:true });
    this.on(document, 'pointerout', event => { if (!event.relatedTarget) { this.pointer.inside = false; this.lastContact = null; this.release(); } });
    this.on(document, 'pointerdown', event => { if (event.button !== 0) return; this.pointerDown = true; this.pointer = { x:event.clientX, y:event.clientY, inside:true, quiet:event.target instanceof Element&&!!event.target.closest('.refinement-panel,.input-shell,.paper-disclosure') }; const node=event.target instanceof Element&&!event.target.closest('.range-control')?event.target.closest('[data-stock]'):null,surface = this.lookup.get(node); if (surface && !this.pointer.quiet) { surface.pressed = true; surface.mark({x:this.pointer.x-5,y:this.pointer.y+2},{x:this.pointer.x+7,y:this.pointer.y-2},this.time,.9); } this.forcePaint = true; this.request(); }, { passive:true });
    this.on(window, 'pointerup', () => this.release()); this.on(window, 'pointercancel', () => this.release()); this.on(window, 'blur', () => { this.pointer.inside = false; this.lastContact = null; this.release(); });
    this.on(document, 'focusin', event => { const surface = this.lookup.get(event.target.closest('[data-stock]')); if (surface) surface.focused = true; this.forcePaint = true; this.request(); });
    this.on(document, 'focusout', () => { this.surfaces.forEach(surface => { surface.focused = false; }); this.forcePaint = true; this.request(); });
    this.on(document, 'keydown', event => { if (!event.repeat && [' ', 'Enter'].includes(event.key) && event.target.matches('button[data-stock]')) { this.lookup.get(event.target).pressed = true; this.forcePaint = true; this.request(); } });
    this.on(document, 'keyup', event => { if ([' ', 'Enter'].includes(event.key)) this.release(); });
    this.on(this.motion, 'click', () => { this.running = !this.running; this.last = 0; this.forcePaint = true; this.syncMotion(); this.request(); });
    this.on(this.reduced, 'change', event => { this.running = !event.matches; this.last = 0; this.forcePaint = true; this.syncMotion(); this.request(); });
    this.on(this.segment, 'click', event => { const button = event.target.closest('button'); if (!button) return; this.segment.querySelectorAll('button').forEach(sibling => sibling.setAttribute('aria-pressed', String(sibling === button))); this.forcePaint = true; this.request(); });
    this.on(document,'click',event=>{
      const button=event.target instanceof Element?event.target.closest('.ui-button,.stock-switch,.check-sample,.segmented>button,.badge'):null;if(!button)return;
      const rect=button.getBoundingClientRect(),pointer=event.detail>0?{x:event.clientX,y:event.clientY}:{x:rect.left+rect.width/2,y:rect.top+rect.height/2};
      const surface=this.lookup.get(button.parentElement===this.segment?this.skin:button);
      this.feedback.add(surface,{x:(pointer.x-rect.left)/rect.width*surface.width,y:(pointer.y-rect.top)/rect.height*surface.height},this.time,!this.running);this.forcePaint=true;this.request();
    });
    for (const item of this.switches) this.on(item.node, 'click', () => { item.node.setAttribute('aria-checked', String(item.node.getAttribute('aria-checked') !== 'true')); this.lookup.get(item.node).paint(); this.forcePaint = true; this.request(); });
    this.on(document.querySelector('.check-sample'), 'click', event => { const node = event.currentTarget; node.setAttribute('aria-checked', String(node.getAttribute('aria-checked') !== 'true')); this.request(); });
    this.on(this.rangeInput,'pointerdown',()=>{this.rangePaint.active=true;this.feedback.add(this.rangePaint,{x:this.rangePaint.width*this.rangePaint.fill,y:9},this.time,!this.running);this.forcePaint=true;this.request();},{passive:true});
    this.on(this.rangeInput, 'input', event => { const value = event.target.value; event.target.style.setProperty('--range', value + '%'); document.querySelector('#range-value').textContent = value;this.rangePaint.fill=Number(value)/100;this.feedback.add(this.rangePaint,{x:this.rangePaint.width*this.rangePaint.fill,y:9},this.time,!this.running);this.forcePaint=true;this.request(); });
    this.on(window, 'scroll', () => { this.lastContact = null;this.needsMeasure = this.forcePaint = true; this.request(); }, { passive: true });
    this.on(window, 'resize', () => { this.lastContact = null; this.renderer.resize(); this.resizeBackground(); this.drawing.resize(innerWidth,innerHeight); this.feedback.resize(innerWidth,innerHeight); this.needsMeasure = this.forcePaint = true; this.request(); }, { passive: true });
    this.on(document, 'visibilitychange', () => { this.last = 0; if (document.hidden) {this.release();cancelAnimationFrame(this.frame); this.frame = 0; } else { this.needsMeasure = this.forcePaint = true; this.request(); } });
    this.on(this.renderer.canvas, 'webglcontextlost', event => { event.preventDefault(); this.renderer.releaseGL(); this.renderer.renderer = 'canvas2d'; document.body.dataset.renderer = 'canvas2d'; this.backgroundDrawn = false; this.forcePaint = true; this.request(); });
    this.on(window, 'pagehide', event => { if (event.persisted) window.addEventListener('pageshow', restored => { if (restored.persisted) location.reload(); }, { once: true }); this.destroy(); });
  }
  destroy() { if (!this.alive) return; this.alive = false; this.life.abort(); cancelAnimationFrame(this.frame); this.frame = 0; this.ro.disconnect();this.refinement.destroy(); this.surfaces.forEach(surface => surface.destroy()); this.surfaces.length = 0; this.lookup.clear(); this.drawing.destroy();this.rangePaint.alive=false;this.rangePaint.feedbackCanvas.width=this.rangePaint.feedbackCanvas.height=0;this.feedback.destroy(); this.renderer.destroy(); this.background.width = this.background.height = 0; this.stamp.width = this.stamp.height = 0; this.stampPoints.length = 0; }
}
new CottonStudy();
