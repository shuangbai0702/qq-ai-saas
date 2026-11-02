// A transient material response, not a drawing document. No timers or independent RAF.
const hash = value => { const n = Math.sin(value * 127.1 + 19.71) * 43758.5453; return n - Math.floor(n); };
export class DryInk {
  constructor(canvas, budget = 26) { this.canvas = canvas; this.budget = budget; this.marks = []; this.serial = 0; this.alive = true; }
  resize(w, h) {
    if (!this.alive) return;
    this.w = w; this.h = h; const dpr = Math.min(devicePixelRatio || 1, 1.25);
    this.canvas.width = Math.max(1, Math.round(w * dpr)); this.canvas.height = Math.max(1, Math.round(h * dpr));
    this.ctx = this.canvas.getContext('2d'); this.ctx?.setTransform(dpr, 0, 0, dpr, 0, 0); this.marks.length = 0;
  }
  add(from, to, time, pressure = .4, red = false) {
    if (!this.alive || !this.ctx) return;
    const distance = Math.hypot(to.x - from.x, to.y - from.y);
    if (distance < .7 || distance > 120) return;
    this.marks.push({ from: { ...from }, to: { ...to }, time, pressure, red, seed: ++this.serial, life: .8 + hash(this.serial) * .55 });
    if (this.marks.length > this.budget) this.marks.splice(0, this.marks.length - this.budget);
  }
  draw(time) {
    if (!this.alive || !this.ctx) return;
    const ctx = this.ctx; ctx.clearRect(0, 0, this.w, this.h);
    this.marks = this.marks.filter(mark => time - mark.time < mark.life);
    for (const mark of this.marks) {
      const age = Math.max(0, time - mark.time) / mark.life, fade = (1 - age) ** 2;
      const dx = mark.to.x - mark.from.x, dy = mark.to.y - mark.from.y, distance = Math.hypot(dx, dy), nx = -dy / distance, ny = dx / distance;
      const width = 2.4 + mark.pressure * 6;
      for (let strand = 0; strand < 11; strand++) {
        const seed = mark.seed * 17 + strand, offset = (hash(seed) - .5) * width, start = hash(seed + 3) * .13, end = .78 + hash(seed + 4) * .22;
        const alpha = fade * (.016 + hash(seed + 1) * .033) * (1 + mark.pressure * .4);
        ctx.strokeStyle = mark.red ? `rgba(205,39,42,${alpha})` : `rgba(89,87,81,${alpha})`;
        ctx.lineWidth = .26 + hash(seed + 2) * .32; ctx.lineCap = 'round';
        ctx.setLineDash([1.5 + hash(seed) * 3.8, .6 + hash(seed + 1) * 1.7]);
        ctx.beginPath(); ctx.moveTo(mark.from.x + dx * start + nx * offset, mark.from.y + dy * start + ny * offset);
        ctx.quadraticCurveTo(mark.from.x + dx * .5 + nx * (offset + .5), mark.from.y + dy * .5 + ny * (offset + .5), mark.from.x + dx * end + nx * offset, mark.from.y + dy * end + ny * offset); ctx.stroke();
      }
    }
    ctx.setLineDash([]);
  }
  destroy() { if (!this.alive) return; this.alive = false; this.marks.length = 0; this.canvas.width = this.canvas.height = 0; this.ctx = null; }
}
