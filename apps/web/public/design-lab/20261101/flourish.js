const NS = 'http://www.w3.org/2000/svg';
const colors = ['#91bafb', '#e5b2d2', '#a1d6c9', '#f1d28c', '#b8b6eb'];
const make = (tag, attrs, parent) => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  parent?.append(node);
  return node;
};
const sparkle = 'M0-5Q.7-.7 5 0Q.7.7 0 5Q-.7.7-5 0Q-.7-.7 0-5Z';

/** Decorative geometry only; the page owns the clock, events and lifetime. */
export class Flourish {
  constructor() {
    this.alive = true;
    this.particles = [];
    this.sequence = 0;
    this.backdrop = make('svg', { class: 'flourish-backdrop', 'aria-hidden': 'true', focusable: 'false' }, document.body);
    this.backgroundForms = Array.from({ length: 4 }, (_, index) => make('path', { fill: colors[index], 'fill-opacity': '.055', stroke: colors[index], 'stroke-opacity': '.35', 'stroke-width': 1.1 }, this.backdrop));
    this.backgroundDots = Array.from({ length: 20 }, (_, index) => {
      const group = make('g', { opacity: '.45' }, this.backdrop);
      if (index % 5 === 0) make('path', { d: sparkle, fill: colors[index % 4], 'fill-opacity': '.3', stroke: colors[(index + 1) % 4], 'stroke-width': '.7' }, group);
      else make('circle', { r: index % 3 === 0 ? 2 : 1.2, fill: colors[index % colors.length] }, group);
      return group;
    });
    this.layer = make('svg', { id: 'flourish-layer', 'aria-hidden': 'true', focusable: 'false' }, document.body);
    this.panels = [...document.querySelectorAll('.show-panel')].map((node, index) => {
      const svg = make('svg', { class: 'panel-ornament', 'aria-hidden': 'true', focusable: 'false' }, node);
      const gradient = make('linearGradient', { id: `ribbon-${index}`, x1: '0', y1: '0', x2: '1', y2: '1' }, make('defs', {}, svg));
      for (const [i, color] of [colors[index], colors[(index + 1) % 5], colors[(index + 2) % 5]].entries()) make('stop', { offset: i / 2, 'stop-color': color }, gradient);
      const loop = make('path', { class: 'ribbon-loop', fill: 'none', stroke: `url(#ribbon-${index})`, 'stroke-width': 1.6 }, svg);
      const dotted = make('path', { fill: 'none', stroke: '#a8c9ef', 'stroke-width': 1.7, 'stroke-dasharray': '.2 8', 'stroke-linecap': 'round', opacity: '.8' }, svg);
      const pieces = Array.from({ length: 7 }, (_, i) => {
        const group = make('g', {}, svg);
        const color = colors[(i + index) % colors.length];
        if (i % 3 === 0) make('path', { d: sparkle, fill: color, 'fill-opacity': '.65', stroke: color, 'stroke-width': '.6' }, group);
        else if (i % 3 === 1) make('circle', { r: 2.7, fill: color }, group);
        else {
          for (const [x, y] of [[-2, -2], [2, -2], [2, 2], [-2, 2]]) make('circle', { cx: x, cy: y, r: 2.6, fill: color, opacity: '.65' }, group);
          make('circle', { r: 1.1, fill: '#fff' }, group);
        }
        return group;
      });
      return { node, svg, loop, dotted, pieces, index, object: node.querySelector('.porcelain-object,.glass-stack,.enamel-object') };
    });
    this.borders = [...document.querySelectorAll('.border-sample')];
    this.measure();
  }

  measure() {
    if (!this.alive) return;
    this.layer.setAttribute('viewBox', `0 0 ${innerWidth} ${innerHeight}`);
    this.backdrop.setAttribute('viewBox', `0 0 ${innerWidth} ${innerHeight}`);
    for (const item of this.panels) {
      const panel = item.node.getBoundingClientRect(), object = item.object.getBoundingClientRect();
      item.svg.setAttribute('viewBox', `0 0 ${item.node.offsetWidth} ${item.node.offsetHeight}`);
      item.cx = object.x + object.width / 2 - panel.x;
      item.cy = object.y + object.height / 2 - panel.y;
      item.rx = Math.min(item.node.offsetWidth * .33, 96);
      item.ry = item.node.offsetHeight < 200 ? 48 : 57;
    }
  }

  burst(pointer, time) {
    if (!this.alive) return;
    while (this.particles.length > 41) this.particles.shift().node.remove();
    const phase = ++this.sequence * 2.399;
    for (let i = 0; i < 7; i++) {
      const angle = i / 7 * Math.PI * 2 + phase;
      const speed = 28 + (i * 17 % 37);
      const node = make('g', { class: 'flourish-particle' }, this.layer);
      const color = colors[(i + this.sequence) % colors.length];
      if (i % 2) make('circle', { r: 2.7, fill: color, stroke: '#fff', 'stroke-width': 1 }, node);
      else make('path', { d: sparkle, fill: color, 'fill-opacity': '.8', stroke: '#fff', 'stroke-width': '.6' }, node);
      this.particles.push({ node, born: time, x: pointer.x + scrollX, y: pointer.y + scrollY, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed - 18, life: .64 + i * .035, angle });
    }
  }

  draw(time, pointer) {
    if (!this.alive) return;
    for (let index = 0; index < this.backgroundForms.length; index++) {
      const cx = innerWidth * [.05, .94, .83, .2][index], cy = innerHeight * [.32, .15, .86, .92][index];
      const radius = Math.min(innerWidth * .22, 125 + index * 18), phase = time * .09 + index;
      let path = '';
      for (let i = 0; i <= 112; i++) {
        const t = i / 112 * Math.PI * 2, r = radius * (1 + Math.sin(t * 4 + phase) * .075);
        const x = cx + Math.cos(t) * r, y = cy + Math.sin(t) * r * .8;
        path += `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
      }
      this.backgroundForms[index].setAttribute('d', path + 'Z');
    }
    for (let i = 0; i < this.backgroundDots.length; i++) {
      const side = i % 2 ? .97 : .03, spread = Math.sin(i * 2.399) * innerWidth * .035;
      const x = innerWidth * side + spread + Math.sin(time * .12 + i) * 4;
      const y = innerHeight * ((i * .137 + .06) % 1) + Math.cos(time * .1 + i) * 5;
      this.backgroundDots[i].setAttribute('transform', `translate(${x.toFixed(2)},${y.toFixed(2)}) rotate(${(time * 3 + i * 19).toFixed(2)})`);
    }
    for (const item of this.panels) {
      const { cx, cy, rx, ry, index } = item;
      const phase = time * .26 + index * 1.8;
      const shape = t => {
        const swell = 1 + Math.sin(t * 3 + phase) * .045;
        return [cx + Math.cos(t) * rx * swell, cy + Math.sin(t) * ry * swell + Math.sin(t * 2 + phase) * 4];
      };
      let path = '';
      for (let i = 0; i <= 84; i++) {
        const [x, y] = shape(i / 84 * Math.PI * 2);
        path += `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
      }
      item.loop.setAttribute('d', path + 'Z');
      item.dotted.setAttribute('d', `M${cx - rx - 8},${cy - 18}Q${cx},${cy + ry + 35} ${cx + rx + 8},${cy - 4}`);
      item.loop.style.opacity = String(.58 + Math.sin(phase) * .1);
      for (let i = 0; i < item.pieces.length; i++) {
        const angle = i / item.pieces.length * Math.PI * 2 + phase * .24;
        const [x, y] = shape(angle);
        const scale = .75 + Math.sin(phase * 1.4 + i) * .16;
        item.pieces[i].setAttribute('transform', `translate(${x.toFixed(2)},${y.toFixed(2)}) rotate(${(phase * 10 + i * 27).toFixed(2)}) scale(${scale.toFixed(3)})`);
      }
    }
    for (let index = 0; index < this.borders.length; index++) {
      const path = this.borders[index].querySelector('path');
      let d = '';
      for (let x = 0; x <= 65; x += 2.5) d += `${x ? 'L' : 'M'}${x},${(9 + Math.sin(x * .18 + time * .75 + index) * (index === 1 ? 1.3 : 2.2)).toFixed(2)}`;
      path.setAttribute('d', d);
    }
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i], age = time - p.born;
      if (age >= p.life) { p.node.remove(); this.particles.splice(i, 1); continue; }
      const progress = age / p.life, travel = (1 - Math.exp(-age * 3.5)) / 3.5;
      const x = p.x + p.vx * travel - scrollX, y = p.y + p.vy * travel + age * age * 14 - scrollY;
      const scale = Math.sin(Math.PI * Math.min(1, progress * 1.5)) * .65 + .15;
      p.node.setAttribute('transform', `translate(${x.toFixed(2)},${y.toFixed(2)}) rotate(${(p.angle * 40 + age * 70).toFixed(2)}) scale(${scale.toFixed(3)})`);
      p.node.setAttribute('opacity', (Math.sin(Math.PI * progress) * .85).toFixed(3));
    }
  }

  destroy() {
    if (!this.alive) return;
    this.alive = false;
    this.layer.remove();
    this.backdrop.remove();
    for (const item of this.panels) item.svg.remove();
    this.panels.length = this.particles.length = this.borders.length = this.backgroundForms.length = this.backgroundDots.length = 0;
  }
}
