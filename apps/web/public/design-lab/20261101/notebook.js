import { TAU, clamp, hash, perimeter, spline, inkContour, petalPath } from './paper-math.js';
import { NotebookField } from './notebook-field.js';

const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, parent) => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  parent?.append(node); return node;
};
const palettes = {
  ivory: ['#f6f7f1', '#eef2ef', '#9eafc1', '#fffff7'],
  cover: ['#eef2ef', '#e6edf0', '#91a7bd', '#f8faf1'],
  blue: ['#6489a9', '#587c9d', '#6f91af', '#a8c5da'],
  mist: ['#d9e7ee', '#cfdfe9', '#93adc3', '#f3f8f4'],
  lilac: ['#e2e2ed', '#d6d9e8', '#a4aec7', '#f8f7f4'],
  clear: ['#edf4f1d9', '#dce9f0c9', '#9db5c8', '#fafff3'],
  recess: ['#e8eef0', '#edf2ef', '#a7b9c8', '#fafcf3'],
  slate: ['#bed0dd', '#aebfd0', '#93a9bd', '#e5edf0'],
};
let serial = 0;

class PaperSurface {
  constructor(node) {
    this.node = node; this.alive = true;
    this.id = `paper-${++serial}`; this.phase = serial * .79;
    this.flex = Number(node.dataset.flex || 5); this.cut = node.dataset.cut || '';
    this.foldSize = Number(node.dataset.fold || 0); this.pressed = this.focused = false;
    this.inkX = this.inkY = this.inkVX = this.inkVY = 0;
    if (node.matches('.ui-button')) {
      this.ink = document.createElement('span'); this.ink.className = 'paper-ink';
      this.ink.append(...node.childNodes); node.append(this.ink);
    }
    this.svg = svg('svg', { class: 'paper-art', 'aria-hidden': 'true', focusable: 'false' });
    const defs = svg('defs', {}, this.svg);
    this.shape = svg('path', { id: this.id + '-shape' }, defs);
    this.base = svg('linearGradient', { id: this.id + '-base', x2: '.15', y2: '1' }, defs);
    this.stops = [svg('stop', { offset: '0' }, this.base), svg('stop', { offset: '1' }, this.base)];
    this.light = svg('radialGradient', { id: this.id + '-light', gradientUnits: 'userSpaceOnUse' }, defs);
    svg('stop', { offset: '0', 'stop-color': '#ffffff', 'stop-opacity': '.24' }, this.light);
    svg('stop', { offset: '1', 'stop-color': '#ffffff', 'stop-opacity': '0' }, this.light);
    const texture = svg('pattern', { id: this.id + '-grain', width: 48, height: 48, patternUnits: 'userSpaceOnUse' }, defs);
    for (let i = 0; i < 47; i++) svg('circle', { cx: (hash(i, 1) * 48).toFixed(2), cy: (hash(i, 2) * 48).toFixed(2), r: i % 3 ? '.24' : '.4', fill: i % 2 ? '#526f8a' : '#ffffff', opacity: i % 2 ? '.14' : '.38' }, texture);
    svg('use', { href: '#' + this.id + '-shape' }, svg('clipPath', { id: this.id + '-clip' }, defs));
    const shadow = svg('filter', { id: this.id + '-shadow', x: '-30%', y: '-55%', width: '160%', height: '220%', 'color-interpolation-filters': 'sRGB' }, defs);
    svg('feDropShadow', { dx: '1', dy: this.foldSize ? '4' : '2', stdDeviation: this.foldSize ? '4' : '2', 'flood-color': '#64839f', 'flood-opacity': this.foldSize ? '.13' : '.1' }, shadow);
    this.fill = svg('use', { href: '#' + this.id + '-shape', fill: `url(#${this.id}-base)`, filter: `url(#${this.id}-shadow)` }, this.svg);
    this.layers = svg('g', { 'clip-path': `url(#${this.id}-clip)` }, this.svg);
    this.lightFill = svg('rect', { fill: `url(#${this.id}-light)` }, this.layers);
    this.grainFill = svg('rect', { fill: `url(#${this.id}-grain)` }, this.layers);
    if (node.hasAttribute('data-pool')) this.pool = svg('path', { class: 'paper-pool', fill: node.dataset.paper === 'blue' ? '#ecf8f7' : '#7eaccd' }, this.layers);
    this.rim = svg('use', { href: '#' + this.id + '-shape', class: 'paper-outline' }, this.svg);
    this.emboss = svg('use', { href: '#' + this.id + '-shape', class: 'paper-emboss' }, this.svg);
    if (node.hasAttribute('data-seam')) this.seam = svg('use', { href: '#' + this.id + '-shape', class: 'paper-perforation', stroke: '#849db5' }, this.svg);
    if (this.foldSize) {
      this.foldShadow = svg('path', { class: 'paper-fold-shadow' }, this.svg);
      this.fold = svg('path', { class: 'paper-fold', fill: '#e3eaf0' }, this.svg);
    }
    node.prepend(this.svg); node.classList.add('paper-ready');
    this.paint(); this.resize();
  }
  paint() {
    let key = this.node.dataset.paper;
    if (this.node.matches('[role=switch]') && this.node.getAttribute('aria-checked') === 'false') key = 'recess';
    const palette = palettes[key] || palettes.ivory;
    this.stops.forEach((stop, i) => stop.setAttribute('stop-color', palette[i]));
    this.rim.setAttribute('stroke', palette[2]); this.emboss.setAttribute('stroke', palette[3]);
  }
  resize() {
    if (!this.alive) return;
    const w = this.node.offsetWidth, h = this.node.offsetHeight;
    if (!w || !h || (w === this.width && h === this.height)) return;
    this.width = w; this.height = h;
    const count = this.cut === 'lace' ? Math.min(240, Math.max(112, Math.round((w + h) / 2))) : w > 280 ? 112 : 64;
    const radius = Number(this.node.dataset.radius || parseFloat(getComputedStyle(this.node).getPropertyValue('--radius')) || 16);
    this.points = perimeter(w, h, radius, count, this.cut);
    this.normal = new Float32Array(count); this.tangent = new Float32Array(count);
    this.nv = new Float32Array(count); this.tv = new Float32Array(count);
    this.nt = new Float32Array(count); this.tt = new Float32Array(count);
    this.svg.setAttribute('viewBox', `0 0 ${w + 40} ${h + 40}`);
    for (const node of [this.lightFill, this.grainFill]) { node.setAttribute('width', w + 40); node.setAttribute('height', h + 40); }
    this.light.setAttribute('r', Math.max(w, h) * .85);
    this.emboss.setAttribute('transform', `translate(${20 * 2 / w + 1},${20 * 2 / h + 1}) scale(${(w - 2) / w},${(h - 2) / h})`);
    if (this.seam) this.seam.setAttribute('transform', `translate(${20 * 10 / w + 5},${20 * 10 / h + 5}) scale(${Math.max(.2, (w - 10) / w)},${Math.max(.2, (h - 10) / h)})`);
    this.drawShape(0, 0);
  }
  measure() { if (this.alive) this.rect = this.node.getBoundingClientRect(); }
  local(pointer) {
    return { x: (pointer.x - this.rect.left) / this.rect.width * this.width, y: (pointer.y - this.rect.top) / this.rect.height * this.height, inside: pointer.inside };
  }
  impulse(pointer, strength = 1) {
    if (!this.points || !this.rect) return;
    const p = this.local(pointer), range = clamp(Math.min(this.width, this.height) * 1.7, 35, 120);
    this.points.forEach((v, i) => {
      const q = Math.exp(-((p.x - v.x) ** 2 + (p.y - v.y) ** 2) / range ** 2);
      this.nv[i] -= q * this.flex * 19 * strength;
      this.tv[i] += q * Math.sin(v.u * TAU + this.phase) * this.flex * 7 * strength;
    });
  }
  step(dt, pointer, time, moving, frameIndex) {
    if (!this.alive || !this.rect || !this.points) return;
    if (this.rect.bottom < -30 || this.rect.top > innerHeight + 30) return;
    const p = this.local(pointer), range = clamp(Math.min(this.width, this.height) * 1.9, 40, 140);
    const near = pointer.inside && p.x > -50 && p.y > -50 && p.x < this.width + 50 && p.y < this.height + 50;
    let hover = 0;
    for (let i = 0; i < this.points.length; i++) {
      const v = this.points[i], dx = p.x - v.x, dy = p.y - v.y;
      const q = near ? Math.exp(-(dx * dx + dy * dy) / range ** 2) : 0;
      const wave = this.cut ? Math.sin(v.u * TAU * 3 - time * 1.3 + this.phase) * .38 : 0;
      this.nt[i] = q * this.flex * (this.pressed ? -.8 : .56) + wave + (this.focused ? .5 : 0);
      this.tt[i] = q * clamp((-dx * v.ny + dy * v.nx) * .03, -3.3, 3.3) * (this.pressed ? 1.8 : 1);
      hover = Math.max(hover, q);
    }
    const count = this.points.length, step = Math.min(dt, .034) / 2;
    if (moving) {
      for (let pass = 0; pass < 2; pass++) {
        for (let i = 0; i < count; i++) {
          const before = (i + count - 1) % count, after = (i + 1) % count;
          this.nv[i] += ((this.nt[i] - this.normal[i]) * 215 + (this.normal[before] + this.normal[after] - this.normal[i] * 2) * 130 - this.nv[i] * 17) * step;
          this.tv[i] += ((this.tt[i] - this.tangent[i]) * 185 + (this.tangent[before] + this.tangent[after] - this.tangent[i] * 2) * 95 - this.tv[i] * 18) * step;
        }
        for (let i = 0; i < count; i++) {
          this.normal[i] = clamp(this.normal[i] + this.nv[i] * step, -this.flex * 1.9, this.flex * 1.9);
          this.tangent[i] = clamp(this.tangent[i] + this.tv[i] * step, -this.flex, this.flex);
        }
      }
    } else { this.normal.set(this.nt); this.tangent.set(this.tt); this.nv.fill(0); this.tv.fill(0); }
    const cornerNear = near ? Math.exp(-((p.x - this.width) ** 2 + p.y ** 2) / 12000) : 0;
    this.drawShape(time, cornerNear);
    this.light.setAttribute('cx', ((near ? p.x : this.width * .35) + 20).toFixed(2));
    this.light.setAttribute('cy', ((near ? p.y : this.height * .2) + 20).toFixed(2));
    if (this.pool && (frameIndex % 2 === 0 || !moving || this.pressed)) {
      const inside = near && p.x >= 0 && p.y >= 0 && p.x <= this.width && p.y <= this.height;
      this.pool.setAttribute('d', inkContour(this.width, this.height, time, { ...p, inside }, this.pressed, this.phase));
      this.pool.setAttribute('transform', 'translate(20,20)');
    }
    if (this.ink) {
      const tx = near ? clamp((p.x / this.width - .5) * 6, -3, 3) : 0;
      const ty = (near ? clamp((p.y / this.height - .5) * 3, -1.5, 1.5) : 0) + (this.pressed ? 1.2 : 0);
      if (moving) {
        this.inkVX += ((tx - this.inkX) * 180 - this.inkVX * 19) * dt;
        this.inkVY += ((ty - this.inkY) * 180 - this.inkVY * 19) * dt;
        this.inkX += this.inkVX * dt; this.inkY += this.inkVY * dt;
      } else { this.inkX = tx; this.inkY = ty; this.inkVX = this.inkVY = 0; }
      this.ink.style.translate = `${this.inkX.toFixed(2)}px ${this.inkY.toFixed(2)}px`;
      const glyph = this.ink.querySelector('[data-petal]');
      if (glyph) glyph.setAttribute('d', petalPath(time + this.phase, hover * .7, 5, 8));
    }
  }
  drawShape(time, cornerNear) {
    const fold = this.foldSize ? this.foldSize + cornerNear * 16 + Math.sin(time * .55 + this.phase) * .6 : 0;
    const vertices = this.points.map((v, i) => {
      let x = v.x + v.nx * this.normal[i] - v.ny * this.tangent[i], y = v.y + v.ny * this.normal[i] + v.nx * this.tangent[i];
      if (fold) { const excess = x - y - (this.width - fold); if (excess > 0) { x -= excess / 2; y += excess / 2; } }
      return { x: x + 20, y: y + 20 };
    });
    this.shape.setAttribute('d', spline(vertices, true));
    if (this.fold) {
      const x = this.width + 20, y = 20, f = fold;
      this.fold.setAttribute('d', `M${x - f},${y}Q${x - f * .57},${y + f * .5} ${x},${y + f}Q${x - f * .49},${y + f * 1.04} ${x - f * .83},${y + f * .82}Q${x - f * .96},${y + f * .42} ${x - f},${y}Z`);
      this.foldShadow.setAttribute('d', `M${x - f},${y + 1}Q${x - f * 1.1},${y + f * .8} ${x},${y + f}L${x - f * 1.02},${y + f * 1.08}Z`);
    }
  }
  destroy() {
    if (!this.alive) return;
    this.alive = false; this.svg.remove(); this.node.classList.remove('paper-ready');
    if (this.ink) { this.ink.replaceWith(...this.ink.childNodes); this.ink = null; }
    this.points = []; this.normal = this.tangent = this.nv = this.tv = this.nt = this.tt = null;
  }
}

// Single owner: no decorative module can keep drawing after this page exits.
class NotebookStudy {
  constructor() {
    this.alive = true; this.life = new AbortController(); this.frame = 0; this.frameIndex = 0; this.last = 0; this.time = 0;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)'); this.running = !this.reduced.matches;
    this.needsMeasure = this.forceDraw = true;
    this.pointer = { x: innerWidth * .7, y: innerHeight * .35, inside: false, down: false };
    this.target = { ...this.pointer };
    this.field = new NotebookField(document.querySelector('#paper-field'));
    this.surfaces = [...document.querySelectorAll('[data-paper]')].map(node => new PaperSurface(node));
    this.lookup = new Map(this.surfaces.map(surface => [surface.node, surface]));
    this.motion = document.querySelector('#motion');
    this.segment = document.querySelector('.segmented'); this.skin = this.segment.querySelector('.segment-skin');
    this.tab = { x: 3, v: 0, width: 62, wv: 0 };
    this.switches = [...document.querySelectorAll('.paper-switch')].map(node => ({ node, thumb: node.querySelector('span'), x: node.getAttribute('aria-checked') === 'true' ? 19 : 0, v: 0 }));
    this.ro = new ResizeObserver(entries => {
      if (!this.alive) return;
      for (const entry of entries) this.lookup.get(entry.target)?.resize();
      this.needsMeasure = this.forceDraw = true; this.request();
    });
    this.surfaces.forEach(surface => this.ro.observe(surface.node));
    this.bind(); this.syncMotion(); this.request();
  }
  on(target, type, fn, options = {}) { target.addEventListener(type, fn, { ...options, signal: this.life.signal }); }
  request() {
    if (!this.alive || this.frame || document.hidden) return;
    this.frame = requestAnimationFrame(now => this.tick(now));
  }
  measure() {
    if (!this.alive) return;
    this.surfaces.forEach(surface => surface.measure()); this.field.measure(); this.needsMeasure = false;
  }
  tick(now) {
    this.frame = 0;
    if (!this.alive || document.hidden) return;
    const dt = this.last ? Math.min((now - this.last) / 1000, .034) : 1 / 60; this.last = now;
    if (this.needsMeasure) this.measure();
    if (this.running) this.time += dt;
    const blend = this.running ? 1 - Math.exp(-dt * 22) : 1;
    this.pointer.x += (this.target.x - this.pointer.x) * blend; this.pointer.y += (this.target.y - this.pointer.y) * blend;
    this.pointer.inside = this.target.inside; this.pointer.down = this.target.down;
    this.stepDetails(dt);
    this.surfaces.forEach(surface => surface.step(dt, this.pointer, this.time, this.running, this.frameIndex));
    this.field.draw(this.time, this.pointer, this.forceDraw || !this.running); this.forceDraw = false;
    this.frameIndex++; document.body.dataset.notebook = 'ready';
    if (this.running) this.request();
  }
  stepDetails(dt) {
    const active = this.segment.querySelector('[aria-pressed=true]'), tx = active.offsetLeft, tw = active.offsetWidth;
    if (this.running) {
      this.tab.v += ((tx - this.tab.x) * 255 - this.tab.v * 22) * dt;
      this.tab.wv += ((tw - this.tab.width) * 255 - this.tab.wv * 22) * dt;
      this.tab.x += this.tab.v * dt; this.tab.width += this.tab.wv * dt;
    } else { this.tab.x = tx; this.tab.width = tw; this.tab.v = this.tab.wv = 0; }
    this.skin.style.left = `${this.tab.x.toFixed(2)}px`; this.skin.style.width = `${Math.max(10, this.tab.width).toFixed(2)}px`;
    for (const item of this.switches) {
      const tx = item.node.getAttribute('aria-checked') === 'true' ? 19 : 0;
      if (this.running) { item.v += ((tx - item.x) * 300 - item.v * 21) * dt; item.x += item.v * dt; }
      else { item.x = tx; item.v = 0; }
      const stretch = 1 + Math.min(.26, Math.abs(item.v) * .003);
      item.thumb.style.transform = `translateX(${item.x.toFixed(2)}px) scale(${stretch.toFixed(3)},${(1 / stretch).toFixed(3)})`;
    }
    document.querySelector('.morph-loader path').setAttribute('d', petalPath(this.time, Math.sin(this.time * .8) * .25, 5, 8.4));
    document.querySelectorAll('.breathing-dots i').forEach((dot, i) => { dot.style.transform = `translateY(${(Math.sin(this.time * 3 + i * .8) * 2).toFixed(2)}px)`; });
    const points = Array.from({ length: 30 }, (_, i) => ({ x: i * 50 / 29, y: 6 + Math.sin(i * .5 - this.time * 2.2) * 2.7 }));
    document.querySelector('.little-wave path').setAttribute('d', spline(points));
    document.querySelectorAll('[data-divider]').forEach((path, i) => {
      const w = path.ownerSVGElement.clientWidth;
      const points = Array.from({ length: 55 }, (_, j) => ({ x: j * w / 54, y: 5 + Math.sin(j * .55 + this.time * .7 + i) * .9 }));
      path.setAttribute('d', spline(points));
    });
  }
  release() {
    if (!this.alive) return;
    this.target.down = false;
    for (const surface of this.surfaces) if (surface.pressed) { surface.pressed = false; surface.impulse(this.pointer, -.36); }
    this.forceDraw = true; this.request();
  }
  syncMotion() {
    document.body.classList.toggle('motion-paused', !this.running); document.body.dataset.motion = this.running ? 'on' : 'off';
    this.motion.setAttribute('aria-pressed', String(this.running));
    this.motion.setAttribute('aria-label', this.running ? '暂停视觉动态' : '开启视觉动态');
    document.querySelector('#motion-label').textContent = this.running ? '纸页在呼吸' : '暂停动态';
  }
  bind() {
    this.on(document, 'pointermove', event => { this.target = { x: event.clientX, y: event.clientY, inside: true, down: this.target.down }; this.forceDraw = true; this.request(); }, { passive: true });
    this.on(document, 'pointerout', event => { if (!event.relatedTarget) { this.target.inside = false; this.release(); } });
    this.on(document, 'pointerdown', event => {
      if (event.button !== 0) return;
      this.target = { x: event.clientX, y: event.clientY, inside: true, down: true };
      this.pointer = { ...this.target };
      if (this.needsMeasure) this.measure();
      const surface = this.lookup.get(event.target.closest('[data-paper]'));
      if (surface) { surface.pressed = true; surface.impulse(this.pointer); }
      this.forceDraw = true; this.request();
    }, { passive: true });
    this.on(window, 'pointerup', () => this.release()); this.on(window, 'pointercancel', () => this.release());
    this.on(window, 'blur', () => { this.target.inside = false; this.release(); });
    this.on(document, 'focusin', event => { const surface = this.lookup.get(event.target.closest('[data-paper]')); if (surface) surface.focused = true; this.request(); });
    this.on(document, 'focusout', () => { this.surfaces.forEach(surface => { surface.focused = false; }); this.request(); });
    this.on(document, 'keydown', event => {
      if (event.repeat || ![' ', 'Enter'].includes(event.key) || !event.target.matches('button[data-paper]')) return;
      const surface = this.lookup.get(event.target), r = surface.rect;
      surface.pressed = true; surface.impulse({ x: r.left + r.width / 2, y: r.top + r.height / 2, inside: true }); this.request();
    });
    this.on(document, 'keyup', event => { if ([' ', 'Enter'].includes(event.key)) this.release(); });
    this.on(this.motion, 'click', () => { this.running = !this.running; this.last = 0; this.syncMotion(); this.forceDraw = true; this.request(); });
    this.on(this.reduced, 'change', event => { this.running = !event.matches; this.last = 0; this.syncMotion(); this.forceDraw = true; this.request(); });
    this.on(this.segment, 'click', event => {
      const button = event.target.closest('button'); if (!button) return;
      this.segment.querySelectorAll('button').forEach(sibling => sibling.setAttribute('aria-pressed', String(sibling === button))); this.request();
    });
    for (const item of this.switches) this.on(item.node, 'click', () => { item.node.setAttribute('aria-checked', String(item.node.getAttribute('aria-checked') !== 'true')); this.lookup.get(item.node).paint(); this.request(); });
    for (const node of document.querySelectorAll('.check-sample')) this.on(node, 'click', () => { node.setAttribute('aria-checked', String(node.getAttribute('aria-checked') !== 'true')); this.request(); });
    this.on(document.querySelector('.paper-range'), 'input', event => { const value = event.target.value; event.target.style.setProperty('--range', value + '%'); document.querySelector('#range-value').textContent = value; this.request(); });
    this.on(window, 'scroll', () => { this.needsMeasure = this.forceDraw = true; this.request(); }, { passive: true });
    this.on(window, 'resize', () => { this.field.resize(); this.needsMeasure = this.forceDraw = true; this.request(); }, { passive: true });
    this.on(document, 'visibilitychange', () => {
      this.last = 0;
      if (document.hidden) { cancelAnimationFrame(this.frame); this.frame = 0; }
      else { this.needsMeasure = this.forceDraw = true; this.request(); }
    });
    this.on(window, 'pagehide', event => {
      if (event.persisted) window.addEventListener('pageshow', restored => { if (restored.persisted) location.reload(); }, { once: true });
      this.destroy();
    });
  }
  destroy() {
    if (!this.alive) return;
    this.alive = false; this.life.abort(); cancelAnimationFrame(this.frame); this.frame = 0; this.ro.disconnect();
    this.field.destroy(); this.surfaces.forEach(surface => surface.destroy()); this.surfaces.length = 0; this.lookup.clear();
  }
}

new NotebookStudy();
