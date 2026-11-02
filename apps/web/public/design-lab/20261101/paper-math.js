// Geometry only. The page owns time, pointer state and resource lifetime.
export const TAU = Math.PI * 2;
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const mix = (a, b, t) => a + (b - a) * t;
export const hash = (x, y = 0) => {
  const n = Math.sin(x * 127.1 + y * 311.7 + 13.71) * 43758.5453;
  return n - Math.floor(n);
};
export function noise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  return mix(mix(hash(ix, iy), hash(ix + 1, iy), u), mix(hash(ix, iy + 1), hash(ix + 1, iy + 1), u), v);
}
export function fbm(x, y) {
  return noise(x, y) * .57 + noise(x * 2.03 + 8, y * 2.03) * .28 + noise(x * 4.11, y * 4.11 + 13) * .15;
}
export function spline(points, closed = false) {
  if (!points.length) return '';
  const f = n => n.toFixed(2), n = points.length;
  let d = `M${f(points[0].x)},${f(points[0].y)}`;
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const a = points[closed ? (i + n - 1) % n : Math.max(0, i - 1)];
    const b = points[i], c = points[(i + 1) % n];
    const e = points[closed ? (i + 2) % n : Math.min(n - 1, i + 2)];
    d += `C${f(b.x + (c.x - a.x) / 6)},${f(b.y + (c.y - a.y) / 6)} ${f(c.x - (e.x - b.x) / 6)},${f(c.y - (e.y - b.y) / 6)} ${f(c.x)},${f(c.y)}`;
  }
  return d + (closed ? 'Z' : '');
}

// Uniform arc-length sampling keeps scallops equally sized around a capsule.
export function perimeter(w, h, radius, count, cut = '') {
  const r = Math.min(radius, w / 2, h / 2), a = Math.max(0, w - 2 * r), b = Math.max(0, h - 2 * r), c = Math.PI * r / 2;
  const lengths = [a, c, b, c, a, c, b, c], total = lengths.reduce((x, y) => x + y, 0);
  const lobes = Math.max(6, Math.round(total / (cut === 'lace' ? 18 : 38)));
  return Array.from({ length: count }, (_, i) => {
    const u = i / count;
    if (cut === 'flower') {
      const t = u * TAU - Math.PI / 2, rr = Math.min(w, h) * (.455 + .045 * Math.cos(t * 6));
      return { x: w / 2 + Math.cos(t) * rr, y: h / 2 + Math.sin(t) * rr, nx: Math.cos(t), ny: Math.sin(t), u };
    }
    let dist = total * u, seg = 0;
    while (seg < 7 && dist > lengths[seg]) dist -= lengths[seg++];
    const t = lengths[seg] ? dist / lengths[seg] : 0;
    let x, y, nx = 0, ny = 0;
    if (seg === 0) { x = r + a * t; y = 0; ny = -1; }
    else if (seg === 2) { x = w; y = r + b * t; nx = 1; }
    else if (seg === 4) { x = w - r - a * t; y = h; ny = 1; }
    else if (seg === 6) { x = 0; y = h - r - b * t; nx = -1; }
    else {
      const angle = [-Math.PI / 2, 0, Math.PI / 2, Math.PI][(seg - 1) / 2] + t * Math.PI / 2;
      nx = Math.cos(angle); ny = Math.sin(angle);
      x = (seg === 1 || seg === 3 ? w - r : r) + nx * r;
      y = (seg === 1 || seg === 7 ? r : h - r) + ny * r;
    }
    let offset = 0;
    if (cut === 'lace') offset = (Math.cos(u * TAU * lobes) - 1) * 1.7;
    if (cut === 'wave') offset = Math.sin(u * TAU * lobes) * 2;
    if (cut === 'ticket') offset = -8 * Math.exp(-Math.pow((y - h / 2) / 8, 4)) * Math.abs(nx);
    return { x: x + nx * offset, y: y + ny * offset, nx, ny, u };
  });
}

// A shared spatial mapping: the paper ruling and the printed cells bend together.
export function warp(x, y, time, pointer, strength = 1, phase = 0) {
  const t = time * .11;
  const qx = fbm(x * .0025 + t, y * .0025 + phase) - .5;
  const qy = fbm(x * .0025 - 7, y * .0025 - t) - .5;
  let dx = (fbm(x * .0027 + qx * 2.2 + phase, y * .0027 + qy * 2) - .5) * 37;
  let dy = (fbm(x * .0027 + qy * 2, y * .0027 + qx * 2 - t) - .5) * 37;
  if (pointer?.inside) {
    const px = x - pointer.x, py = y - pointer.y, rr = px * px + py * py;
    const weight = Math.exp(-rr / 22000), twist = .22 + .12 * Math.sin(time * .8);
    dx += (px * .19 - py * twist) * weight;
    dy += (py * .19 + px * twist) * weight;
    const pressure = pointer.down ? 1.7 : 1;
    dx *= pressure; dy *= pressure;
  }
  return { x: x + dx * strength, y: y + dy * strength };
}

// Marching squares with edge identity stitching. The result changes topology as ink joins.
export function inkContour(w, h, time, pointer, pressed = false, phase = 0) {
  const nx = 34, ny = Math.max(12, Math.round(34 * h / w)), stride = nx + 1;
  const sx = (w + 20) / nx, sy = (h + 20) / ny, values = new Float32Array(stride * (ny + 1));
  const px = pointer?.inside ? clamp(pointer.x, 0, w) : w * (.62 + .17 * Math.sin(time * .6 + phase));
  const py = pointer?.inside ? clamp(pointer.y, 0, h) : h * (.5 + .25 * Math.cos(time * .7 + phase));
  const radius = Math.min(w, h) * (pressed ? .35 : .28);
  const balls = [
    [w * (.16 + .11 * Math.sin(time * .65 + phase)), h * (.5 + .22 * Math.cos(time * .5)), radius],
    [w * (.64 + .18 * Math.cos(time * .42 + phase)), h * (.43 + .21 * Math.sin(time * .7)), radius * 1.22],
    [px, py, radius * (pointer?.inside ? 1.12 : .66)],
  ];
  for (let y = 0; y <= ny; y++) for (let x = 0; x <= nx; x++) {
    if (!x || !y || x === nx || y === ny) { values[y * stride + x] = 0; continue; }
    const xx = x * sx - 10, yy = y * sy - 10;
    values[y * stride + x] = balls.reduce((sum, ball) => sum + ball[2] ** 2 / ((xx - ball[0]) ** 2 + (yy - ball[1]) ** 2 + 3), 0);
  }
  const segments = [], nodes = new Map();
  const add = (a, b) => { const index = segments.push([a, b]) - 1; for (const p of [a, b]) { if (!nodes.has(p.id)) nodes.set(p.id, []); nodes.get(p.id).push(index); } };
  const lookup = [[], [[3, 0]], [[0, 1]], [[3, 1]], [[1, 2]], [[3, 2], [0, 1]], [[0, 2]], [[3, 2]], [[2, 3]], [[2, 0]], [[0, 3], [1, 2]], [[2, 1]], [[1, 3]], [[1, 0]], [[0, 3]], []];
  for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
    const p = y * stride + x, vals = [values[p], values[p + 1], values[p + stride + 1], values[p + stride]];
    const code = vals.reduce((v, f, i) => v | (f >= 1 ? 1 << i : 0), 0);
    if (code === 0 || code === 15) continue;
    const corners = [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]];
    const edge = index => {
      const a = index, b = (index + 1) % 4, t = clamp((1 - vals[a]) / (vals[b] - vals[a]), 0, 1);
      const id = index === 0 ? `h${x},${y}` : index === 1 ? `v${x + 1},${y}` : index === 2 ? `h${x},${y + 1}` : `v${x},${y}`;
      return { id, x: mix(corners[a][0], corners[b][0], t) * sx - 10, y: mix(corners[a][1], corners[b][1], t) * sy - 10 };
    };
    let pairs = lookup[code];
    if ((code === 5 || code === 10) && vals.reduce((a, b) => a + b, 0) > 4) pairs = code === 5 ? [[3, 0], [1, 2]] : [[0, 1], [2, 3]];
    for (const pair of pairs) add(edge(pair[0]), edge(pair[1]));
  }
  const used = new Set(); let d = '';
  for (let s = 0; s < segments.length; s++) {
    if (used.has(s)) continue;
    const start = segments[s][0]; let current = start, index = s, points = [];
    for (let guard = 0; guard <= segments.length; guard++) {
      used.add(index); points.push(current);
      const segment = segments[index], next = segment[0].id === current.id ? segment[1] : segment[0];
      if (next.id === start.id) break;
      const nextIndex = nodes.get(next.id)?.find(value => !used.has(value));
      if (nextIndex === undefined) { points.push(next); break; }
      current = next; index = nextIndex;
    }
    if (points.length > 2) d += spline(points, true);
  }
  return d;
}

export function petalPath(time, amount = 0, lobes = 5, radius = 8, center = 12) {
  const points = Array.from({ length: 64 }, (_, i) => {
    const a = i / 64 * TAU - Math.PI / 2;
    const r = radius * (.87 + (.13 + amount * .13) * Math.cos(a * lobes + time * .45));
    return { x: center + Math.cos(a) * r, y: center + Math.sin(a) * r };
  });
  return spline(points, true);
}
