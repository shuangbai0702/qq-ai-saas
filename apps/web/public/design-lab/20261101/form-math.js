export const TAU = Math.PI * 2;
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const mix = (a, b, t) => a + (b - a) * t;
export const hash = (x, y = 0) => { const n = Math.sin(x * 127.1 + y * 311.7 + 13.71) * 43758.5453; return n - Math.floor(n); };
export function noise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  return mix(mix(hash(ix, iy), hash(ix + 1, iy), u), mix(hash(ix, iy + 1), hash(ix + 1, iy + 1), u), v);
}
export function spline(points, closed = false) {
  if (!points.length) return '';
  const f = n => n.toFixed(2), n = points.length;
  let d = `M${f(points[0].x)},${f(points[0].y)}`;
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const a = points[closed ? (i + n - 1) % n : Math.max(0, i - 1)], b = points[i], c = points[(i + 1) % n], e = points[closed ? (i + 2) % n : Math.min(n - 1, i + 2)];
    d += `C${f(b.x + (c.x - a.x) / 6)},${f(b.y + (c.y - a.y) / 6)} ${f(c.x - (e.x - b.x) / 6)},${f(c.y - (e.y - b.y) / 6)} ${f(c.x)},${f(c.y)}`;
  }
  return d + (closed ? 'Z' : '');
}
// Generated once per size. Pointer and animation time never change this silhouette.
export function outline(w, h, radius, cut = '') {
  const r = Math.min(radius, w / 2, h / 2), a = Math.max(0, w - 2 * r), b = Math.max(0, h - 2 * r), c = Math.PI * r / 2;
  const lengths = [a, c, b, c, a, c, b, c], total = lengths.reduce((x, y) => x + y, 0);
  const count = cut === 'lace' ? Math.min(240, Math.max(112, Math.round(total / 3))) : w > 280 ? 104 : 72;
  const lobes = Math.max(6, Math.round(total / (cut === 'lace' ? 19 : 40)));
  return Array.from({ length: count }, (_, i) => {
    const u = i / count;
    if (cut === 'flower') {
      const t = u * TAU - Math.PI / 2, rr = Math.min(w, h) * (.455 + .045 * Math.cos(t * 6));
      return { x: w / 2 + Math.cos(t) * rr, y: h / 2 + Math.sin(t) * rr };
    }
    let dist = total * u, seg = 0;
    while (seg < 7 && dist > lengths[seg]) dist -= lengths[seg++];
    const t = lengths[seg] ? dist / lengths[seg] : 0;
    let x, y, nx = 0, ny = 0;
    if (seg === 0) { x = r + a * t; y = 0; ny = -1; }
    else if (seg === 2) { x = w; y = r + b * t; nx = 1; }
    else if (seg === 4) { x = w - r - a * t; y = h; ny = 1; }
    else if (seg === 6) { x = 0; y = h - r - b * t; nx = -1; }
    else { const angle = [-Math.PI / 2, 0, Math.PI / 2, Math.PI][(seg - 1) / 2] + t * Math.PI / 2; nx = Math.cos(angle); ny = Math.sin(angle); x = (seg === 1 || seg === 3 ? w - r : r) + nx * r; y = (seg === 1 || seg === 7 ? r : h - r) + ny * r; }
    let offset = cut === 'lace' ? (Math.cos(u * TAU * lobes) - 1) * 1.4 : cut === 'wave' ? Math.sin(u * TAU * lobes) * 1.5 : 0;
    if (cut === 'ticket') offset = -6 * Math.exp(-Math.pow((y - h / 2) / 7, 4)) * Math.abs(nx);
    return { x: x + nx * offset, y: y + ny * offset };
  });
}
// Symmetric arc construction, assembled from rigid pieces rather than liquid morphs.
export function ornamentPath(radius = 7, petals = 5, gap = .3, center = 12) {
  let d = '';
  for (let i = 0; i < petals; i++) {
    const a = i / petals * TAU - Math.PI / 2, b = a + TAU / petals - gap;
    const x1 = center + Math.cos(a) * radius, y1 = center + Math.sin(a) * radius;
    const x2 = center + Math.cos(b) * radius, y2 = center + Math.sin(b) * radius;
    d += `M${x1.toFixed(2)},${y1.toFixed(2)}A${(radius * .52).toFixed(2)},${(radius * .52).toFixed(2)} 0 0 1 ${x2.toFixed(2)},${y2.toFixed(2)}`;
  }
  return d;
}
