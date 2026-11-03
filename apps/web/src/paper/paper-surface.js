import { PaperRenderer, colors } from './paper-renderer.js';
import { DryInk } from './dry-ink.js';

const NS = 'http://www.w3.org/2000/svg', TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hash = value => { const n = Math.sin(value * 127.1 + 23.1) * 43758.5453; return n - Math.floor(n); };
const svg = (tag, attrs = {}, parent) => { const node = document.createElementNS(NS, tag); for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value); parent?.append(node); return node; };
// Reassigning even an identical SVG filter/transform invalidates browser paint.
const attribute = (node, name, value) => { if (node.getAttribute(name) !== value) node.setAttribute(name, value); };
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

export class PaperSurface {
  constructor(node, renderer) {
    this.node = node; this.renderer = renderer; this.alive = true; this.id = `cotton-${++serial}`; this.phase = hash(serial) * TAU; this.hover = this.press = 0; this.pressed = this.focused = false; this.drawn = false;
    // Real console elements can begin inside a closed details/dialog.
    this.points = []; this.pencilLength = 0;
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
    if (!this.alive || this.quiet || !this.rect || !this.rect.width || !this.rect.height || !this.width || !this.height) return;
    const local = p => ({ x:(p.x-this.rect.left)/this.rect.width*this.width, y:(p.y-this.rect.top)/this.rect.height*this.height });
    this.brush.add(local(from),local(to),time,pressure,false);
  }
  step(dt, pointer, light, time, running, paint) {
    if (!this.alive || !this.rect || !this.rect.width || !this.rect.height || !this.width || !this.height) return;
    if (this.drawn && (this.rect.bottom < -30 || this.rect.top > innerHeight + 30)) return;
    const p = { x: (pointer.x - this.rect.left) / this.rect.width * this.width, y: (pointer.y - this.rect.top) / this.rect.height * this.height };
    const quiet = this.quiet || (pointer.quiet && this.node.classList.contains('paper-panel'));
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
      attribute(this.shadow,'dx', dx.toFixed(2)); attribute(this.shadow,'dy', dy.toFixed(2)); attribute(this.shadow,'stdDeviation', (2.1 - this.press * .9).toFixed(2));
      attribute(this.shadow,'flood-opacity', (.10 - this.press * .035).toFixed(3));
      this.depths.forEach((node, i) => attribute(node,'transform', `translate(0,${((i + 1) * (.85+this.hover*.25) * (1 - this.press * .68)).toFixed(2)})`));
      attribute(this.rimGradient,'x1', '.1');
      attribute(this.rimGradient,'y1', '0');
      this.brush.draw(time);
      attribute(this.section,'opacity',(.35+this.hover*.25-this.press*.2).toFixed(3));
      attribute(this.section,'transform',`translate(0,${(-this.press*.8).toFixed(2)})`);
      attribute(this.pencil,'stroke-dasharray',`${(this.pencilLength * (.025+this.hover*.68)).toFixed(2)} ${this.pencilLength.toFixed(2)}`);
      attribute(this.pencil,'stroke-dashoffset',(-this.pencilLength*.42).toFixed(2));
      attribute(this.pencil,'opacity',(.15+this.hover*.7).toFixed(3));
      const hasAccent=this.node.matches('.primary,.layer-front,.material-object,.swatch');
      attribute(this.transfer,'opacity',hasAccent ? (.18+this.hover*.48+this.press*.18).toFixed(3) : '0');
    }
  }
  destroy() { if (!this.alive) return; this.alive = false; this.brush.destroy(); this.canvas.width = this.canvas.height = this.feedbackCanvas.width = this.feedbackCanvas.height = 0; this.svg.remove(); this.node.classList.remove('stock-ready'); this.points.length = 0; }
}

