import { MaterialField } from './ui-field.js';
import { Flourish } from './flourish.js';

const NS = 'http://www.w3.org/2000/svg';
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const el = (name, attrs = {}, parent) => {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  parent?.append(node);
  return node;
};
const palettes = {
  porcelain: ['#f6f8fc', '#edf3fa', '#b0ccee', '#ffffff'],
  paper: ['#f5f8fc', '#eff4fa', '#bdd6f4', '#ffffff'],
  white: ['#f7f9fd', '#edf3fb', '#a9c9ee', '#ffffff'],
  ceramic: ['#f6f9fe', '#e4effd', '#a5c8f0', '#ffffff'],
  glass: ['#ffffffb5', '#d7edff94', '#a7cdef', '#ffffffc9'],
  enamel: ['#e3f2ff', '#bfdeff', '#a2c9f4', '#ffffffbb'],
  blue: ['#3470c5', '#2c67bc', '#4e86d5', '#e5f1ff7a'],
  ice: ['#ffffffdb', '#e4f3ffbd', '#a7cbf4', '#ffffff'],
  mist: ['#eef7ff', '#ddecff', '#b1cdf0', '#ffffffdd'],
  recess: ['#eff6ff', '#f8fcff', '#b3cdef', '#ffffff'],
  satin: ['#eff9ff', '#c9e5ff', '#a1c7ee', '#ffffffbb'],
  switch: ['#98c2ff', '#75a8ed', '#8db7ef', '#eef7ffbb'],
  quiet: ['#f6faff', '#e5effc', '#bfd6f2', '#ffffffdd'],
  rose: ['#fff8fd', '#f8deef', '#dfb7d4', '#ffffff'],
  mint: ['#f3fffb', '#d8f0e9', '#a8d3c8', '#ffffff'],
  lemon: ['#fffdf1', '#ffedc4', '#e4ce98', '#ffffff'],
  pearl: ['#f4f4ff', '#e4e4fc', '#c1bbe9', '#ffffff'],
};

// A reproducible vector grain. No downloaded texture or character artwork.
let seed = 7369;
const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
const grain = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80" viewBox="0 0 80 80">' +
  Array.from({ length: 240 }, (_, i) => `<circle cx="${(random() * 80).toFixed(2)}" cy="${(random() * 80).toFixed(2)}" r="${(.25 + random() * .4).toFixed(2)}" fill="${i % 2 ? '#18395d' : '#ffffff'}" opacity="${(.1 + random() * .24).toFixed(2)}"/>`).join('') + '</svg>',
);

function perimeter(width, height, radius, count) {
  const r = Math.min(radius, width / 2, height / 2);
  const horizontal = Math.max(0, width - 2 * r), vertical = Math.max(0, height - 2 * r), arc = Math.PI * r / 2;
  const lengths = [horizontal, arc, vertical, arc, horizontal, arc, vertical, arc];
  const total = lengths.reduce((a, b) => a + b, 0);
  return Array.from({ length: count }, (_, i) => {
    let distance = total * i / count, segment = 0;
    while (segment < 7 && distance > lengths[segment]) distance -= lengths[segment++];
    const t = lengths[segment] ? distance / lengths[segment] : 0;
    if (segment === 0) return { x: r + horizontal * t, y: 0, nx: 0, ny: -1 };
    if (segment === 2) return { x: width, y: r + vertical * t, nx: 1, ny: 0 };
    if (segment === 4) return { x: width - r - horizontal * t, y: height, nx: 0, ny: 1 };
    if (segment === 6) return { x: 0, y: height - r - vertical * t, nx: -1, ny: 0 };
    const angle = [-Math.PI / 2, 0, Math.PI / 2, Math.PI][(segment - 1) / 2] + t * Math.PI / 2;
    const cx = segment === 1 || segment === 3 ? width - r : r;
    const cy = segment === 1 || segment === 7 ? r : height - r;
    return { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r, nx: Math.cos(angle), ny: Math.sin(angle) };
  });
}

function closedSpline(points) {
  const n = points.length, fmt = x => x.toFixed(2);
  let d = `M${fmt(points[0].x)},${fmt(points[0].y)}`;
  for (let i = 0; i < n; i++) {
    const a = points[(i - 1 + n) % n], b = points[i], c = points[(i + 1) % n], e = points[(i + 2) % n];
    d += `C${fmt(b.x + (c.x - a.x) / 6)},${fmt(b.y + (c.y - a.y) / 6)} ${fmt(c.x - (e.x - b.x) / 6)},${fmt(c.y - (e.y - b.y) / 6)} ${fmt(c.x)},${fmt(c.y)}`;
  }
  return d + 'Z';
}

let serial = 0;
class Surface {
  constructor(node) {
    this.node = node;
    this.material = node.dataset.surface;
    this.shape = node.dataset.shape;
    this.flex = Number(node.dataset.flex || 5);
    this.pressed = false;
    this.focused = false;
    this.dirty = true;
    this.id = `material-${++serial}`;
    this.phase = serial * 1.618;
    this.inkX = this.inkY = this.inkVX = this.inkVY = 0;
    if (node.matches('.ui-button')) {
      this.ink = document.createElement('span');
      this.ink.className = 'surface-ink';
      this.ink.append(...node.childNodes);
      node.append(this.ink);
    }
    this.svg = el('svg', { class: 'surface-art', 'aria-hidden': 'true', focusable: 'false' });
    const defs = el('defs', {}, this.svg);
    this.base = el('linearGradient', { id: this.id + '-base', x1: '0', y1: '0', x2: '.25', y2: '1' }, defs);
    this.stops = [el('stop', { offset: '0' }, this.base), el('stop', { offset: '1' }, this.base)];
    this.light = el('radialGradient', { id: this.id + '-light', gradientUnits: 'userSpaceOnUse' }, defs);
    el('stop', { offset: '0', 'stop-color': '#ffffff', 'stop-opacity': this.material === 'blue' ? '0' : '.36' }, this.light);
    el('stop', { offset: '.48', 'stop-color': '#ffffff', 'stop-opacity': '.06' }, this.light);
    el('stop', { offset: '1', 'stop-color': '#ffffff', 'stop-opacity': '0' }, this.light);
    const sheen = el('linearGradient', { id: this.id + '-sheen', gradientUnits: 'userSpaceOnUse' }, defs);
    el('stop', { offset: '0', 'stop-color': '#ffffff', 'stop-opacity': '0' }, sheen);
    el('stop', { offset: '.45', 'stop-color': '#ffffff', 'stop-opacity': '.025' }, sheen);
    el('stop', { offset: '.53', 'stop-color': '#ffffff', 'stop-opacity': '.15' }, sheen);
    el('stop', { offset: '.64', 'stop-color': '#ffffff', 'stop-opacity': '.03' }, sheen);
    el('stop', { offset: '1', 'stop-color': '#ffffff', 'stop-opacity': '0' }, sheen);
    this.sheen = sheen;
    this.prism = el('linearGradient', { id: this.id + '-prism', gradientUnits: 'userSpaceOnUse' }, defs);
    for (const [offset, color] of [[0, '#a4cdfa'], [.3, '#c7bef4'], [.55, '#f2c6df'], [.78, '#bce8dd'], [1, '#d7e9ff']]) el('stop', { offset, 'stop-color': color }, this.prism);
    const pattern = el('pattern', { id: this.id + '-grain', width: 80, height: 80, patternUnits: 'userSpaceOnUse' }, defs);
    el('image', { href: grain, width: 80, height: 80 }, pattern);
    this.clip = el('path', {}, el('clipPath', { id: this.id + '-clip' }, defs));
    const shadow = el('filter', { id: this.id + '-shadow', x: '-30%', y: '-60%', width: '160%', height: '230%', 'color-interpolation-filters': 'sRGB' }, defs);
    el('feDropShadow', { dx: '0', dy: '3', stdDeviation: '3.5', 'flood-color': '#86afe4', 'flood-opacity': this.material === 'blue' ? '.22' : '.12' }, shadow);
    this.fill = el('path', { fill: `url(#${this.id}-base)`, filter: `url(#${this.id}-shadow)` }, this.svg);
    this.layers = el('g', { 'clip-path': `url(#${this.id}-clip)` }, this.svg);
    this.highlight = el('rect', { fill: `url(#${this.id}-light)` }, this.layers);
    this.sheenFill = el('rect', { fill: `url(#${this.id}-sheen)`, opacity: ['paper', 'blue'].includes(this.material) ? '.2' : '.8' }, this.layers);
    this.prismFill = el('rect', { class: 'surface-prism-film', fill: `url(#${this.id}-prism)`, opacity: this.material === 'blue' ? '.03' : ['glass', 'satin', 'enamel', 'pearl'].includes(this.material) ? '.24' : '.09' }, this.layers);
    this.texture = el('rect', { class: 'surface-grain', fill: `url(#${this.id}-grain)`, opacity: this.material === 'paper' ? '.06' : '.12' }, this.layers);
    this.threads = el('g', { class: 'surface-threads', stroke: '#8bb8e8', opacity: this.material === 'satin' ? '.1' : '.035' }, this.layers);
    this.rim = el('path', { class: 'surface-rim' }, this.svg);
    this.inner = el('path', { class: 'surface-inner' }, this.svg);
    this.prismRim = el('path', { class: 'surface-prism-rim', fill: 'none', stroke: `url(#${this.id}-prism)`, 'stroke-width': 1.1, opacity: this.material === 'blue' ? '.35' : '.8' }, this.svg);
    node.prepend(this.svg);
    node.classList.add('material-ready');
    this.paintPalette();
    this.resize();
  }

  paintPalette() {
    const key = this.material === 'switch' && this.node.getAttribute('aria-checked') === 'false' ? 'quiet' : this.material;
    const [top, bottom, rim, inner] = palettes[key] || palettes.white;
    this.stops[0].setAttribute('stop-color', top);
    this.stops[1].setAttribute('stop-color', bottom);
    this.rim.setAttribute('stroke', rim);
    this.inner.setAttribute('stroke', inner);
  }

  resize() {
    const width = this.node.offsetWidth, height = this.node.offsetHeight;
    if (!width || !height || (width === this.width && height === this.height)) return;
    this.width = width;
    this.height = height;
    const count = this.shape ? 96 : width > 250 ? 72 : 48;
    this.points = perimeter(width, height, Number(this.node.dataset.radius || 16), count);
    if (this.shape === 'cloud') this.points = this.points.map((p, i) => {
      const offset = (1 + Math.cos(i / count * Math.PI * 16)) * 1.15;
      return { ...p, x: p.x + p.nx * offset, y: p.y + p.ny * offset };
    });
    if (this.shape === 'flower') this.points = this.points.map((_, i) => {
      const angle = i / count * Math.PI * 2 - Math.PI / 2, radius = Math.min(width, height) / 2 - 2 + Math.cos(angle * 5) * 2;
      return { x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius, nx: Math.cos(angle), ny: Math.sin(angle) };
    });
    this.displacement = new Float32Array(count);
    this.velocity = new Float32Array(count);
    this.targets = new Float32Array(count);
    this.svg.setAttribute('viewBox', `0 0 ${width + 28} ${height + 28}`);
    for (const rect of [this.highlight, this.sheenFill, this.prismFill, this.texture]) {
      rect.setAttribute('width', width + 28);
      rect.setAttribute('height', height + 28);
    }
    this.light.setAttribute('r', Math.max(width, height) * .95);
    this.threads.replaceChildren();
    // Broad, airy curves rather than machined or metallic surface scratches.
    if (['glass', 'satin'].includes(this.material)) {
      const spacing = this.material === 'satin' ? 11 : 22;
      for (let y = -height * .5; y < height * 1.8; y += spacing) {
        let d = '';
        for (let x = -10; x <= width + 40; x += 7) {
          const yy = y + Math.sin(x / Math.max(60, width) * 3.3 + y * .011) * 11 + x * .14;
          d += `${x === -10 ? 'M' : 'L'}${x.toFixed(1)},${yy.toFixed(1)}`;
        }
        el('path', { d }, this.threads);
      }
    }
    this.dirty = true;
    this.drawShape();
  }

  measure() { this.rect = this.node.getBoundingClientRect(); }

  local(pointer) {
    const r = this.rect;
    return { x: (pointer.x - r.left) * this.width / r.width, y: (pointer.y - r.top) * this.height / r.height };
  }

  impulse(pointer, strength = 1) {
    if (!this.points || !this.rect) return;
    const p = this.local(pointer), radius = clamp(Math.min(this.width, this.height) * .85, 27, 85);
    for (let i = 0; i < this.points.length; i++) {
      const v = this.points[i], d = ((v.x - p.x) ** 2 + (v.y - p.y) ** 2) / (radius * radius);
      this.velocity[i] -= Math.exp(-d * 1.3) * this.flex * 12 * strength;
    }
    this.dirty = true;
  }

  step(dt, pointer, time, moving) {
    if (!this.points || !this.rect) return;
    const r = this.rect;
    if (r.bottom < -30 || r.top > innerHeight + 30) return;
    const p = this.local(pointer);
    const near = pointer.inside && p.x > -55 && p.y > -55 && p.x < this.width + 55 && p.y < this.height + 55;
    const range = clamp(Math.min(this.width, this.height) * 1.65, 45, 140);
    let energy = 0;
    const n = this.points.length;
    for (let i = 0; i < n; i++) {
      const v = this.points[i], dx = p.x - v.x, dy = p.y - v.y;
      const weight = near ? Math.exp(-(dx * dx + dy * dy) / (range * range) * 1.4) : 0;
      const puff = weight * this.flex * .34;
      this.targets[i] = puff - (this.pressed ? this.flex * (.16 + weight * .36) : 0);
      if (this.focused) this.targets[i] += Math.sin(i / n * Math.PI * 2) * .45;
    }
    if (moving) {
      const step = Math.min(dt, .034) / 2;
      for (let pass = 0; pass < 2; pass++) {
        for (let i = 0; i < n; i++) {
          const d = this.displacement[i];
          const coupling = this.displacement[(i + n - 1) % n] + this.displacement[(i + 1) % n] - d * 2;
          this.velocity[i] += ((this.targets[i] - d) * 235 + coupling * 95 - this.velocity[i] * 18) * step;
        }
        for (let i = 0; i < n; i++) {
          this.displacement[i] = clamp(this.displacement[i] + this.velocity[i] * step, -this.flex * 1.4, this.flex * 1.4);
          energy += Math.abs(this.velocity[i]) + Math.abs(this.displacement[i] - this.targets[i]);
        }
      }
    } else {
      this.displacement.set(this.targets);
      this.velocity.fill(0);
      this.dirty = true;
    }
    if (energy > .015 || this.dirty) this.drawShape();
    const lx = near ? p.x : this.width * (.25 + Math.sin(time * .23 + serial) * .12);
    const ly = near ? p.y : this.height * .15;
    this.light.setAttribute('cx', (lx + 14).toFixed(2));
    this.light.setAttribute('cy', (ly + 14).toFixed(2));
    const band = near ? p.x - this.width * .45 : (Math.sin(time * .16 + this.width * .01) * .3 + .1) * this.width;
    this.sheen.setAttribute('x1', band.toFixed(2));
    this.sheen.setAttribute('y1', '0');
    this.sheen.setAttribute('x2', (band + this.width * 1.6 + 50).toFixed(2));
    this.sheen.setAttribute('y2', this.height.toFixed(2));
    const iridescence = Math.sin(time * .29 + this.phase) * this.width * .25;
    this.prism.setAttribute('x1', (near ? p.x - this.width * .4 : iridescence).toFixed(2));
    this.prism.setAttribute('y1', '0');
    this.prism.setAttribute('x2', (this.width + 30 + iridescence).toFixed(2));
    this.prism.setAttribute('y2', (this.height + 28).toFixed(2));
    if (this.ink) {
      const tx = near ? clamp((p.x / this.width - .5) * 5, -2.6, 2.6) : 0;
      const ty = (near ? clamp((p.y / this.height - .5) * 3.5, -1.5, 1.5) : 0) + (this.pressed ? 1.2 : 0);
      if (moving) {
        const step = Math.min(dt, .034);
        this.inkVX += ((tx - this.inkX) * 200 - this.inkVX * 19) * step;
        this.inkVY += ((ty - this.inkY) * 200 - this.inkVY * 19) * step;
        this.inkX += this.inkVX * step;
        this.inkY += this.inkVY * step;
      } else { this.inkX = tx; this.inkY = ty; this.inkVX = this.inkVY = 0; }
      this.ink.style.translate = `${this.inkX.toFixed(2)}px ${this.inkY.toFixed(2)}px`;
    }
  }

  drawShape() {
    const vertices = this.points.map((p, i) => ({ x: p.x + 14 + p.nx * this.displacement[i], y: p.y + 14 + p.ny * this.displacement[i] }));
    const d = closedSpline(vertices);
    this.fill.setAttribute('d', d);
    this.clip.setAttribute('d', d);
    this.rim.setAttribute('d', d);
    this.inner.setAttribute('d', d);
    this.prismRim.setAttribute('d', d);
    const sx = (this.width - 3) / this.width, sy = (this.height - 3) / this.height;
    this.inner.setAttribute('transform', `translate(${14 * (1 - sx) + 1.5},${14 * (1 - sy) + 1.5}) scale(${sx},${sy})`);
    this.dirty = false;
  }
}

// One owner and one clock for all geometry, lights, springs and the wave field.
class MaterialStudy {
  constructor() {
    this.alive = true;
    this.life = new AbortController();
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)');
    this.running = !this.reduced.matches;
    this.time = 0;
    this.frame = 0;
    this.last = 0;
    this.needsMeasure = true;
    this.pointer = { x: innerWidth * .5, y: innerHeight * .42, inside: false };
    this.field = new MaterialField(document.querySelector('#field'));
    this.surfaces = [...document.querySelectorAll('[data-surface]')].map(node => new Surface(node));
    this.lookup = new Map(this.surfaces.map(surface => [surface.node, surface]));
    this.stage = document.querySelector('#surfaces');
    this.flourish = new Flourish();
    this.motion = document.querySelector('#motion');
    this.switches = [...document.querySelectorAll('.jelly-switch')].map(node => ({ node, thumb: node.querySelector('span'), x: node.getAttribute('aria-checked') === 'true' ? 18 : 0, v: 0 }));
    this.segment = document.querySelector('.segmented');
    this.skin = this.segment.querySelector('.segment-skin');
    this.tab = { x: 4, v: 0, width: 61, wv: 0 };
    this.ro = new ResizeObserver(entries => {
      if (!this.alive) return;
      for (const entry of entries) this.lookup.get(entry.target)?.resize();
      this.needsMeasure = true;
      this.request();
    });
    for (const surface of this.surfaces) this.ro.observe(surface.node);
    this.bind();
    this.syncMotion();
    this.request();
  }

  on(target, type, callback, options = {}) { target.addEventListener(type, callback, { ...options, signal: this.life.signal }); }

  request() {
    if (!this.alive || this.frame || document.hidden) return;
    this.frame = requestAnimationFrame(now => this.tick(now));
  }

  measure() {
    for (const surface of this.surfaces) surface.measure();
    this.stageRect = this.stage.getBoundingClientRect();
    this.flourish.measure();
    this.needsMeasure = false;
  }

  tick(now) {
    this.frame = 0;
    if (!this.alive || document.hidden) return;
    const dt = this.last ? Math.min((now - this.last) / 1000, .034) : 1 / 60;
    this.last = now;
    if (this.needsMeasure) this.measure();
    if (this.running) { this.time += dt; this.field.advance(dt); }
    this.stepDetails(dt);
    for (const surface of this.surfaces) surface.step(dt, this.pointer, this.time, this.running);
    this.field.draw(this.time, this.pointer, this.stageRect);
    this.flourish.draw(this.time, this.pointer);
    document.body.dataset.materials = 'ready';
    if (this.running) this.request();
  }

  stepDetails(dt) {
    const active = this.segment.querySelector('[aria-pressed=true]');
    const tx = active.offsetLeft, tw = active.offsetWidth;
    if (this.running) {
      const step = Math.min(dt, .034);
      this.tab.v += ((tx - this.tab.x) * 260 - this.tab.v * 22) * step;
      this.tab.wv += ((tw - this.tab.width) * 260 - this.tab.wv * 22) * step;
      this.tab.x += this.tab.v * step;
      this.tab.width += this.tab.wv * step;
    } else { this.tab.x = tx; this.tab.width = tw; this.tab.v = this.tab.wv = 0; }
    this.skin.style.left = `${this.tab.x.toFixed(2)}px`;
    this.skin.style.width = `${Math.max(10, this.tab.width).toFixed(2)}px`;
    for (const item of this.switches) {
      const target = item.node.getAttribute('aria-checked') === 'true' ? 18 : 0;
      if (this.running) {
        const step = Math.min(dt, .034);
        item.v += ((target - item.x) * 330 - item.v * 20) * step;
        item.x += item.v * step;
      } else { item.x = target; item.v = 0; }
      const stretch = 1 + Math.min(.26, Math.abs(item.v) * .003);
      item.thumb.style.transform = `translateX(${item.x.toFixed(2)}px) scale(${stretch.toFixed(3)},${(1 / stretch).toFixed(3)})`;
    }
  }

  syncMotion() {
    document.body.classList.toggle('motion-paused', !this.running);
    document.body.dataset.motion = this.running ? 'on' : 'off';
    this.motion.setAttribute('aria-pressed', String(this.running));
    this.motion.setAttribute('aria-label', this.running ? '暂停视觉动态' : '开启视觉动态');
    document.querySelector('#motion-label').textContent = this.running ? '动态开启' : '动态暂停';
  }

  release() {
    for (const surface of this.surfaces) {
      if (surface.pressed) { surface.pressed = false; surface.impulse(this.pointer, -.45); }
    }
    this.request();
  }

  bind() {
    this.on(document, 'pointermove', event => {
      const distance = Math.hypot(event.clientX - this.pointer.x, event.clientY - this.pointer.y);
      if (this.running && distance > 1 && distance < 180) this.field.disturb(event.clientX, event.clientY, Math.min(distance / 40, .65), 1.6);
      this.pointer = { x: event.clientX, y: event.clientY, inside: true };
      this.request();
    }, { passive: true });
    this.on(document, 'pointerout', event => {
      if (!event.relatedTarget) { this.pointer.inside = false; this.release(); }
    });
    this.on(document, 'pointerdown', event => {
      if (event.button !== 0) return;
      this.pointer = { x: event.clientX, y: event.clientY, inside: true };
      if (this.needsMeasure) this.measure();
      const surface = this.lookup.get(event.target.closest('[data-surface]'));
      if (surface) { surface.pressed = true; surface.impulse(this.pointer); }
      if (this.running) this.field.disturb(event.clientX, event.clientY, 2.4, 2.5);
      if (this.running && !event.target.closest('#motion')) this.flourish.burst(this.pointer, this.time);
      this.request();
    }, { passive: true });
    this.on(window, 'pointerup', () => this.release());
    this.on(window, 'pointercancel', () => this.release());
    this.on(window, 'blur', () => { this.pointer.inside = false; this.release(); });
    this.on(document, 'focusin', event => {
      const surface = this.lookup.get(event.target.closest('[data-surface]'));
      if (surface) { surface.focused = true; this.request(); }
    });
    this.on(document, 'focusout', () => {
      for (const surface of this.surfaces) surface.focused = false;
      this.request();
    });
    this.on(document, 'keydown', event => {
      if (event.repeat || ![' ', 'Enter'].includes(event.key) || !event.target.matches('button[data-surface]')) return;
      const surface = this.lookup.get(event.target);
      const r = surface.rect;
      const p = { x: r.left + r.width * .5, y: r.top + r.height * .5 };
      surface.pressed = true;
      surface.impulse(p);
      this.request();
    });
    this.on(document, 'keyup', event => { if ([' ', 'Enter'].includes(event.key)) this.release(); });
    this.on(this.motion, 'click', () => {
      this.running = !this.running;
      this.last = 0;
      this.syncMotion();
      this.request();
    });
    this.on(this.reduced, 'change', event => {
      this.running = !event.matches;
      this.last = 0;
      this.syncMotion();
      this.request();
    });
    this.on(this.segment, 'click', event => {
      const button = event.target.closest('button');
      if (!button) return;
      for (const sibling of this.segment.querySelectorAll('button')) sibling.setAttribute('aria-pressed', String(sibling === button));
      this.request();
    });
    for (const item of this.switches) this.on(item.node, 'click', () => {
      item.node.setAttribute('aria-checked', String(item.node.getAttribute('aria-checked') !== 'true'));
      this.lookup.get(item.node).paintPalette();
      this.request();
    });
    const check = document.querySelector('.check-sample');
    this.on(check, 'click', () => {
      check.setAttribute('aria-checked', String(check.getAttribute('aria-checked') !== 'true'));
      this.request();
    });
    this.on(window, 'scroll', () => { this.needsMeasure = true; this.request(); }, { passive: true });
    this.on(window, 'resize', () => {
      this.field.resize();
      this.needsMeasure = true;
      this.request();
    }, { passive: true });
    this.on(document, 'visibilitychange', () => {
      this.last = 0;
      if (document.hidden) { cancelAnimationFrame(this.frame); this.frame = 0; }
      else { this.needsMeasure = true; this.request(); }
    });
    // A fresh page is the owner after a history-cache restore; old callbacks stay dead.
    this.on(window, 'pagehide', event => {
      if (event.persisted) window.addEventListener('pageshow', restored => { if (restored.persisted) location.reload(); }, { once: true });
      this.destroy();
    });
  }

  destroy() {
    if (!this.alive) return;
    this.alive = false;
    this.life.abort();
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.ro.disconnect();
    this.flourish.destroy();
    this.field.destroy();
    this.lookup.clear();
    this.surfaces.length = 0;
  }
}

new MaterialStudy();
