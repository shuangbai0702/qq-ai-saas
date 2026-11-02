import { clamp, hash, outline, spline, ornamentPath } from './form-math.js';
import { VisualField, SurfacePrint } from './form-field.js';

const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, parent) => { const node = document.createElementNS(NS, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); parent?.append(node); return node; };
const palettes = {
  ivory: ['#f5f8f5', '#ecf2f2', '#9db3c7', '#fffffa'], cover: ['#edf3f3c9', '#e4eef2b9', '#a1b7ca', '#fafef7'],
  blue: ['#476e92', '#3f668d', '#6d94b5', '#bad6e7'], mist: ['#dbeaf1', '#ccdfec', '#95b3cc', '#f5fbf6'],
  lilac: ['#e3e5f2', '#d6dcee', '#a5b4cf', '#fafbf6'], clear: ['#f1f7f3b8', '#dceaf1b3', '#a3bcce', '#ffffff'],
  recess: ['#e5eef2', '#eef4f2', '#a7bdcd', '#fcfff8'], slate: ['#c5d9e6', '#afc7da', '#96b3ca', '#edf8f6'],
};
let serial = 0;
class FormSurface {
  constructor(node) {
    this.node = node; this.alive = true; this.phase = hash(++serial, 6) * 6.28; this.id = `form-${serial}`;
    this.hover = this.press = 0; this.pressed = this.focused = false;
    if (node.matches('.ui-button')) { this.ink = document.createElement('span'); this.ink.className = 'paper-ink'; this.ink.append(...node.childNodes); node.append(this.ink); }
    this.svg = svg('svg', { class: 'paper-art', 'aria-hidden': 'true', focusable: 'false' });
    const defs = svg('defs', {}, this.svg);
    this.shape = svg('path', { id: this.id + '-shape' }, defs);
    this.base = svg('linearGradient', { id: this.id + '-base', x2: '.2', y2: '1' }, defs);
    this.stops = [svg('stop', { offset: '0' }, this.base), svg('stop', { offset: '1' }, this.base)];
    this.light = svg('radialGradient', { id: this.id + '-light', gradientUnits: 'userSpaceOnUse' }, defs);
    svg('stop', { offset: '0', 'stop-color': '#ffffff', 'stop-opacity': '.34' }, this.light); svg('stop', { offset: '1', 'stop-color': '#ffffff', 'stop-opacity': '0' }, this.light);
    const grain = svg('pattern', { id: this.id + '-grain', width: 43, height: 43, patternUnits: 'userSpaceOnUse' }, defs);
    for (let i = 0; i < 48; i++) svg('circle', { cx: (hash(i, 2) * 43).toFixed(1), cy: (hash(i, 3) * 43).toFixed(1), r: i % 4 ? '.2' : '.37', fill: i % 2 ? '#6286a5' : '#ffffff', opacity: i % 2 ? '.12' : '.35' }, grain);
    svg('use', { href: '#' + this.id + '-shape' }, svg('clipPath', { id: this.id + '-clip' }, defs));
    const shadow = svg('filter', { id: this.id + '-shadow', x: '-30%', y: '-50%', width: '160%', height: '210%', 'color-interpolation-filters': 'sRGB' }, defs);
    this.shadow = svg('feDropShadow', { dx: '0', dy: '3', stdDeviation: '3', 'flood-color': '#6387a6', 'flood-opacity': '.12' }, shadow);
    svg('use', { href: '#' + this.id + '-shape', fill: `url(#${this.id}-base)`, filter: `url(#${this.id}-shadow)` }, this.svg);
    const layers = svg('g', { 'clip-path': `url(#${this.id}-clip)` }, this.svg);
    this.lightFill = svg('rect', { fill: `url(#${this.id}-light)` }, layers);
    this.grainFill = svg('rect', { fill: `url(#${this.id}-grain)` }, layers);
    if (node.hasAttribute('data-pool')) {
      this.foreign = svg('foreignObject', { x: 20, y: 20, 'aria-hidden': 'true' }, layers);
      const canvas = document.createElement('canvas'); canvas.className = 'surface-print'; canvas.style.cssText = 'width:100%;height:100%;display:block'; this.foreign.append(canvas);
      this.print = new SurfacePrint(canvas, node.classList.contains('cut-button') ? 'fine' : 'dots', node.dataset.paper === 'blue');
    }
    this.rim = svg('use', { href: '#' + this.id + '-shape', class: 'paper-outline' }, this.svg);
    this.inner = svg('use', { href: '#' + this.id + '-shape', class: 'paper-emboss' }, this.svg);
    this.trail = svg('use', { href: '#' + this.id + '-shape', class: 'contour-trail', fill: 'none', stroke: node.dataset.paper === 'blue' ? '#e2f5ff' : '#719dc1', 'stroke-width': '1.4', 'stroke-linecap': 'round' }, this.svg);
    this.track = svg('use', { href: '#' + this.id + '-shape', class: 'contour-track', fill: 'none', stroke: '#a8cadd', 'stroke-width': '.8', 'stroke-linecap': 'round' }, this.svg);
    node.prepend(this.svg); node.classList.add('paper-ready');
    const glyph = this.ink?.querySelector('[data-petal]');
    if (glyph) { glyph.ownerSVGElement.setAttribute('viewBox', '0 0 24 24'); glyph.setAttribute('d', ornamentPath()); this.glyph = glyph; }
    this.paint(); this.resize();
  }
  paint() {
    let key = this.node.dataset.paper;
    if (this.node.matches('[role=switch]') && this.node.getAttribute('aria-checked') === 'false') key = 'recess';
    const p = palettes[key] || palettes.ivory; this.stops.forEach((stop, i) => stop.setAttribute('stop-color', p[i]));
    this.rim.setAttribute('stroke', p[2]); this.inner.setAttribute('stroke', p[3]);
  }
  resize() {
    if (!this.alive) return;
    const w = this.node.offsetWidth, h = this.node.offsetHeight;
    if (!w || !h || (w === this.width && h === this.height)) return;
    this.width = w; this.height = h;
    const r = Number(this.node.dataset.radius || parseFloat(getComputedStyle(this.node).getPropertyValue('--radius')) || 16);
    const points = outline(w, h, r, this.node.dataset.cut).map(p => ({ x: p.x + 20, y: p.y + 20 }));
    this.shape.setAttribute('d', spline(points, true)); this.length = this.shape.getTotalLength();
    this.svg.setAttribute('viewBox', `20 20 ${w} ${h}`);
    for (const node of [this.lightFill, this.grainFill]) { node.setAttribute('width', w + 40); node.setAttribute('height', h + 40); }
    this.light.setAttribute('r', Math.max(w, h) * .9);
    this.inner.setAttribute('transform', `translate(${20 * 2 / w + 1},${20 * 2 / h + 1}) scale(${(w - 2) / w},${(h - 2) / h})`);
    this.track.setAttribute('transform', `translate(${20 * 6 / w + 3},${20 * 6 / h + 3}) scale(${Math.max(.2, (w - 6) / w)},${Math.max(.2, (h - 6) / h)})`);
    if (this.print) { this.foreign.setAttribute('width', w); this.foreign.setAttribute('height', h); this.print.resize(w, h); }
  }
  measure() { if (this.alive) this.rect = this.node.getBoundingClientRect(); }
  step(dt, pointer, time, moving, frameIndex) {
    if (!this.alive || !this.rect || this.rect.bottom < -30 || this.rect.top > innerHeight + 30) return;
    const p = { x: (pointer.x - this.rect.left) / this.rect.width * this.width, y: (pointer.y - this.rect.top) / this.rect.height * this.height };
    const inside = pointer.inside && p.x >= 0 && p.y >= 0 && p.x <= this.width && p.y <= this.height;
    const blend = moving ? 1 - Math.exp(-dt * 15) : 1;
    this.hover += ((inside || this.focused ? 1 : 0) - this.hover) * blend; this.press += ((this.pressed ? 1 : 0) - this.press) * blend;
    this.light.setAttribute('cx', (clamp(inside ? p.x : this.width * .28, 0, this.width) + 20).toFixed(2));
    this.light.setAttribute('cy', (clamp(inside ? p.y : this.height * .2, 0, this.height) + 20).toFixed(2));
    this.lightFill.setAttribute('opacity', (this.node.dataset.paper === 'blue' ? .08 + this.hover * .1 : .3 + this.hover * .3 - this.press * .1).toFixed(3));
    const dash = Math.min(this.length * .16, 15 + this.hover * 35);
    this.trail.setAttribute('stroke-dasharray', `${dash.toFixed(2)} ${(Math.max(1, this.length / 2 - dash)).toFixed(2)}`);
    this.trail.setAttribute('stroke-dashoffset', (-time * (17 + this.phase) - this.phase * 20).toFixed(2));
    this.trail.setAttribute('opacity', (.11 + this.hover * .56 + this.press * .15).toFixed(3));
    this.track.setAttribute('stroke-dasharray', `${Math.min(5, this.length / 20).toFixed(2)} ${(this.length / 8).toFixed(2)}`);
    this.track.setAttribute('stroke-dashoffset', (time * 8 + this.phase * 11).toFixed(2));
    this.track.setAttribute('opacity', (.06 + this.hover * .16).toFixed(3));
    this.shadow.setAttribute('flood-opacity', (.11 + this.hover * .035).toFixed(3));
    if (this.print && (frameIndex % 2 === 0 || !moving || this.pressed)) this.print.draw(time, p, this.hover, this.press);
    if (this.ink) this.ink.style.translate = `0 ${this.press.toFixed(3)}px`;
    if (this.glyph) this.glyph.setAttribute('transform', `rotate(${(time * 8 + this.hover * 24).toFixed(2)} 12 12)`);
  }
  destroy() {
    if (!this.alive) return; this.alive = false; this.print?.destroy(); this.svg.remove(); this.node.classList.remove('paper-ready');
    if (this.ink) { this.ink.replaceWith(...this.ink.childNodes); this.ink = null; }
  }
}

class FormStudy {
  constructor() {
    this.alive = true; this.life = new AbortController(); this.frame = this.frameIndex = this.time = this.last = 0; this.needsMeasure = this.forceDraw = true;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)'); this.running = !this.reduced.matches;
    this.pointer = { x: innerWidth * .7, y: innerHeight * .3, inside: false };
    this.field = new VisualField(document.querySelector('#paper-field'));
    this.surfaces = [...document.querySelectorAll('[data-paper]')].map(node => new FormSurface(node)); this.lookup = new Map(this.surfaces.map(surface => [surface.node, surface]));
    this.motion = document.querySelector('#motion'); this.segment = document.querySelector('.segmented'); this.skin = this.segment.querySelector('.segment-skin'); this.tab = { x: 3, width: 62 };
    this.switches = [...document.querySelectorAll('.paper-switch')].map(node => ({ node, thumb: node.querySelector('span'), x: node.getAttribute('aria-checked') === 'true' ? 19 : 0 }));
    this.ro = new ResizeObserver(entries => { if (!this.alive) return; entries.forEach(entry => this.lookup.get(entry.target)?.resize()); this.needsMeasure = this.forceDraw = true; this.request(); });
    this.surfaces.forEach(surface => this.ro.observe(surface.node)); this.bind(); this.syncMotion(); this.request();
  }
  on(target, type, fn, options = {}) { target.addEventListener(type, fn, { ...options, signal: this.life.signal }); }
  request() { if (!this.alive || this.frame || document.hidden) return; this.frame = requestAnimationFrame(now => this.tick(now)); }
  measure() { this.surfaces.forEach(surface => surface.measure()); this.field.measure(); this.needsMeasure = false; }
  tick(now) {
    this.frame = 0; if (!this.alive || document.hidden) return;
    const dt = this.last ? Math.min((now - this.last) / 1000, .034) : 1 / 60; this.last = now;
    if (this.needsMeasure) this.measure(); if (this.running) this.time += dt;
    this.details(dt); this.surfaces.forEach(surface => surface.step(dt, this.pointer, this.time, this.running, this.frameIndex));
    this.field.draw(this.time, this.pointer, this.forceDraw || !this.running); this.forceDraw = false;
    this.frameIndex++; document.body.dataset.notebook = 'ready'; if (this.running) this.request();
  }
  details(dt) {
    const blend = this.running ? 1 - Math.exp(-dt * 17) : 1, active = this.segment.querySelector('[aria-pressed=true]');
    this.tab.x += (active.offsetLeft - this.tab.x) * blend; this.tab.width += (active.offsetWidth - this.tab.width) * blend;
    this.skin.style.left = this.tab.x.toFixed(2) + 'px'; this.skin.style.width = this.tab.width.toFixed(2) + 'px';
    for (const item of this.switches) { item.x += ((item.node.getAttribute('aria-checked') === 'true' ? 19 : 0) - item.x) * blend; item.thumb.style.transform = `translateX(${item.x.toFixed(2)}px)`; }
    const loader = document.querySelector('.morph-loader path'); loader.setAttribute('d', ornamentPath(8, 6, .28)); loader.setAttribute('transform', `rotate(${(this.time * 27).toFixed(2)} 12 12)`);
    document.querySelectorAll('.breathing-dots i').forEach((dot, i) => { dot.style.opacity = (.35 + .65 * (Math.sin(this.time * 2.7 + i * .8) + 1) / 2).toFixed(3); });
    const wave = document.querySelector('.little-wave path'); wave.setAttribute('d', 'M1 7h6m4 0h6m4 0h6m4 0h6m4 0h6'); wave.setAttribute('stroke-dasharray', '2 5'); wave.setAttribute('stroke-dashoffset', (-this.time * 10).toFixed(2));
    document.querySelectorAll('[data-divider]').forEach(path => { const w = path.ownerSVGElement.clientWidth; path.setAttribute('d', `M0 5H${w}`); path.setAttribute('stroke-dasharray', '2 5'); path.setAttribute('stroke-dashoffset', (-this.time * 5).toFixed(2)); });
  }
  release() { if (!this.alive) return; this.surfaces.forEach(surface => { surface.pressed = false; }); this.forceDraw = true; this.request(); }
  syncMotion() {
    document.body.classList.toggle('motion-paused', !this.running); document.body.dataset.motion = this.running ? 'on' : 'off';
    this.motion.setAttribute('aria-pressed', String(this.running)); this.motion.setAttribute('aria-label', this.running ? '暂停视觉动态' : '开启视觉动态'); document.querySelector('#motion-label').textContent = this.running ? '动态开启' : '动态暂停';
  }
  bind() {
    this.on(document, 'pointermove', event => { this.pointer = { x: event.clientX, y: event.clientY, inside: true }; this.forceDraw = true; this.request(); }, { passive: true });
    this.on(document, 'pointerout', event => { if (!event.relatedTarget) { this.pointer.inside = false; this.release(); } });
    this.on(document, 'pointerdown', event => { if (event.button !== 0) return; this.pointer = { x: event.clientX, y: event.clientY, inside: true }; const surface = this.lookup.get(event.target.closest('[data-paper]')); if (surface) surface.pressed = true; this.forceDraw = true; this.request(); }, { passive: true });
    this.on(window, 'pointerup', () => this.release()); this.on(window, 'pointercancel', () => this.release()); this.on(window, 'blur', () => { this.pointer.inside = false; this.release(); });
    this.on(document, 'focusin', event => { const surface = this.lookup.get(event.target.closest('[data-paper]')); if (surface) surface.focused = true; this.request(); });
    this.on(document, 'focusout', () => { this.surfaces.forEach(surface => { surface.focused = false; }); this.request(); });
    this.on(document, 'keydown', event => { if (!event.repeat && [' ', 'Enter'].includes(event.key) && event.target.matches('button[data-paper]')) { this.lookup.get(event.target).pressed = true; this.request(); } });
    this.on(document, 'keyup', event => { if ([' ', 'Enter'].includes(event.key)) this.release(); });
    this.on(this.motion, 'click', () => { this.running = !this.running; this.last = 0; this.syncMotion(); this.forceDraw = true; this.request(); });
    this.on(this.reduced, 'change', event => { this.running = !event.matches; this.last = 0; this.syncMotion(); this.forceDraw = true; this.request(); });
    this.on(this.segment, 'click', event => { const button = event.target.closest('button'); if (!button) return; this.segment.querySelectorAll('button').forEach(sibling => sibling.setAttribute('aria-pressed', String(sibling === button))); this.request(); });
    for (const item of this.switches) this.on(item.node, 'click', () => { item.node.setAttribute('aria-checked', String(item.node.getAttribute('aria-checked') !== 'true')); this.lookup.get(item.node).paint(); this.request(); });
    this.on(document.querySelector('.check-sample'), 'click', event => { const node = event.currentTarget; node.setAttribute('aria-checked', String(node.getAttribute('aria-checked') !== 'true')); this.request(); });
    this.on(document.querySelector('.paper-range'), 'input', event => { const value = event.target.value; event.target.style.setProperty('--range', value + '%'); document.querySelector('#range-value').textContent = value; this.request(); });
    this.on(window, 'scroll', () => { this.needsMeasure = this.forceDraw = true; this.request(); }, { passive: true });
    this.on(window, 'resize', () => { this.field.resize(); this.needsMeasure = this.forceDraw = true; this.request(); }, { passive: true });
    this.on(document, 'visibilitychange', () => { this.last = 0; if (document.hidden) { cancelAnimationFrame(this.frame); this.frame = 0; } else { this.needsMeasure = this.forceDraw = true; this.request(); } });
    this.on(window, 'pagehide', event => { if (event.persisted) window.addEventListener('pageshow', restored => { if (restored.persisted) location.reload(); }, { once: true }); this.destroy(); });
  }
  destroy() { if (!this.alive) return; this.alive = false; this.life.abort(); cancelAnimationFrame(this.frame); this.frame = 0; this.ro.disconnect(); this.field.destroy(); this.surfaces.forEach(surface => surface.destroy()); this.surfaces.length = 0; this.lookup.clear(); }
}
new FormStudy();
