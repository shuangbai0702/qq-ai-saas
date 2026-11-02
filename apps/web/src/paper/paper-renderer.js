// One material recipe for the whole family. Texture maps are generated here, never fetched.
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fract = value => value - Math.floor(value);
const hash = (x, y) => fract(Math.sin(x * 127.1 + y * 311.7 + 19.7) * 43758.5453);
const mix = (a, b, t) => a + (b - a) * t;
function noise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  return mix(mix(hash(ix, iy), hash(ix + 1, iy), u), mix(hash(ix, iy + 1), hash(ix + 1, iy + 1), u), v);
}
const SIZE = 256;
function normalMap(height, gain) {
  const data = new Uint8Array(SIZE * SIZE * 4), at = (x, y) => height[((y + SIZE) % SIZE) * SIZE + (x + SIZE) % SIZE];
  for (let y = 0; y < SIZE; y++) for (let x = 0; x < SIZE; x++) {
    const p = (y * SIZE + x) * 4;
    data[p] = clamp(128 + (at(x + 1, y) - at(x - 1, y)) * gain, 0, 255);
    data[p + 1] = clamp(128 + (at(x, y + 1) - at(x, y - 1)) * gain, 0, 255);
    data[p + 2] = clamp(height[y * SIZE + x] * 255, 0, 255); data[p + 3] = 255;
  }
  return data;
}
function makeMaps() {
  const fiber = new Float32Array(SIZE * SIZE), relief = new Float32Array(SIZE * SIZE), screen = new Float32Array(SIZE * SIZE);
  for (let y = 0; y < SIZE; y++) for (let x = 0; x < SIZE; x++) {
    const p = y * SIZE + x;
    fiber[p] = .47 + hash(x, y) * .035 + noise(x * .22, y * .22) * .017 + noise(x * .065, y * .065) * .008;
    const qx = (x % 64) - 32, qy = (y % 64) - 32;
    let d = 999;
    for (let i = 0; i < 5; i++) {
      const a = i * Math.PI * 2 / 5 - Math.PI / 2;
      d = Math.min(d, Math.abs(Math.hypot(qx - Math.cos(a) * 7.7, qy - Math.sin(a) * 7.7) - 5.8));
    }
    const center = Math.abs(Math.hypot(qx, qy) - 3.7), ring = Math.abs(Math.hypot(qx, qy) - 22);
    relief[p] = .36 + Math.exp(-d * d * 1.8) * .22 + Math.exp(-center * center * 2.4) * .14 + Math.exp(-ring * ring * 3.5) * .05;
    const dotX = (x % 8) - 4, dotY = (y % 8) - 4;
    screen[p] = .4 + Math.exp(-(dotX * dotX + dotY * dotY) * .8) * .20 + .035 * Math.cos(x * Math.PI / 8) * Math.cos(y * Math.PI / 8);
  }
  // Several hundred short, slightly curved fibers with a reproducible seed.
  for (let i = 0; i < 1500; i++) {
    const x0 = hash(i, 2) * SIZE, y0 = hash(i, 3) * SIZE, angle = hash(i, 4) * Math.PI * 2;
    const length = 5 + hash(i, 5) * 22, amp = (hash(i, 6) - .48) * .040, bend = (hash(i, 7) - .5) * .65;
    for (let s = 0; s < length; s += .65) {
      const t = s / length, x = x0 + Math.cos(angle) * s - Math.sin(angle) * Math.sin(t * Math.PI) * bend, y = y0 + Math.sin(angle) * s + Math.cos(angle) * Math.sin(t * Math.PI) * bend;
      for (let yy = -1; yy <= 1; yy++) for (let xx = -1; xx <= 1; xx++) {
        const ix = Math.round(x) + xx, iy = Math.round(y) + yy, weight = Math.exp(-((ix - x) ** 2 + (iy - y) ** 2) * 2.6);
        fiber[((iy + SIZE * 2) % SIZE) * SIZE + (ix + SIZE * 2) % SIZE] += amp * weight;
      }
    }
  }
  return [normalMap(fiber, 800), normalMap(relief, 430), normalMap(screen, 600)];
}

const VERTEX = `#version 300 es
in vec2 position;
void main(){gl_Position=vec4(position,0.,1.);}`;
const FRAGMENT = `#version 300 es
precision highp float;
uniform sampler2D u_fiber;
uniform sampler2D u_reliefMap;
uniform sampler2D u_screen;
uniform vec2 u_size;
uniform vec2 u_origin;
uniform vec2 u_light;
uniform vec2 u_pointer;
uniform vec3 u_color;
uniform float u_scale;
uniform float u_time;
uniform float u_relief;
uniform float u_role;
uniform float u_finish;
uniform float u_hover;
uniform float u_press;
out vec4 outColor;
void main(){
  vec2 p=vec2(gl_FragCoord.x,u_size.y*u_scale-gl_FragCoord.y)/u_scale;
  vec2 world=p+u_origin;
  vec4 fiber=texture(u_fiber,world/(u_finish<.5&&u_role>1.5?370.:138.));
  float patternScale=u_role<.5?640.:u_role>1.5?168.:300.;
  vec2 uv=(world+vec2(23.,17.))/patternScale;
  vec4 flower=texture(u_reliefMap,uv);
  vec4 screen=texture(u_screen,world/170.);
  vec2 textureSlope=(fiber.rg*2.-1.)*.31;
  float mask=1.;
  if(u_role<.5){
    float center=abs(world.x-u_size.x*.5)/max(1.,u_size.x*.5);
    mask=smoothstep(.46,.92,center)*.48+.06;
  }else if(u_role<1.5){
    mask=.15+.5*smoothstep(.35,.98,p.x/max(1.,u_size.x))*smoothstep(.15,.92,p.y/max(1.,u_size.y));
  }
  vec2 decorSlope=mix(flower.rg*2.-1.,screen.rg*2.-1.,step(1.5,u_finish));
  float decorHeight=mix(flower.b,screen.b,step(1.5,u_finish));
  float finish=u_finish<.5?.25:1.;
  vec2 slope=textureSlope+decorSlope*u_relief*mask*finish*.43;
  vec2 pointerDelta=p-u_pointer;
  float pressure=exp(-dot(pointerDelta,pointerDelta)/730.);
  slope+=pointerDelta*.008*pressure*u_press;
  vec3 normal=normalize(vec3(-slope.x,-slope.y,1.));
  vec2 lightDelta=u_role<.5?(u_light-world)/max(390.,max(u_size.x,u_size.y)):vec2(-.15,-.18);
  vec3 light=normalize(vec3(lightDelta*.8+vec2(-.45,-.5),1.15));
  float diffuse=dot(normal,light);
  float ink=step(u_color.g*1.8,u_color.r);
  float matte=mix(.984,.95,ink)+(diffuse-.82)*.15;
  float grain=(fiber.b-.49)*.29;
  vec3 color=u_color*matte+grain;
  color-=vec3(.016,.014,.014)*(decorHeight-.36)*u_relief*mask*finish;
  if(u_role>.5){
    // Graphite catches on the paper tooth in broken diagonal dry-brush strands.
    // This deposits pigment: no moving light, bright spot, or reflective sheen.
    float along=abs(pointerDelta.x*.94-pointerDelta.y*.34);
    float across=abs(pointerDelta.y*.94+pointerDelta.x*.34);
    float bristle=step(.36,fract((p.y*.94+p.x*.34)*.64+fiber.b*17.));
    float contact=(1.-smoothstep(22.,72.,along))*(1.-smoothstep(5.,24.,across));
    float tooth=smoothstep(.473,.516,fiber.b);
    color-=vec3(.12,.114,.105)*contact*bristle*tooth*u_hover;
  }
  float localLight=u_role<.5?exp(-dot(pointerDelta,pointerDelta)/11000.)*u_hover:0.;
  color+=vec3(.019,.015,.011)*localLight;
  float edge=min(min(p.x,u_size.x-p.x),min(p.y,u_size.y-p.y));
  float bevel=exp(-max(0.,edge)/.85);
  float edgeSide=p.y<u_size.y*.5?1.:-.7;
  color+=bevel*edgeSide*vec3(.029,.024,.021);
  color-=u_press*.012*(.3+pressure);
  if(u_role<.5){
    float tint=.5+.5*sin(world.x*.002+world.y*.001+u_time*.075);
    color=mix(color,color*vec3(1.,.997,.989),tint*.2);
  }
  outColor=vec4(clamp(color,0.,1.),1.);
}`;

export const colors = {
  ground: ['#f4f4f0', '#c8c8c1', '#ffffff'], backing: ['#f8f8f4', '#c9c8c1', '#ffffff'], cream: ['#fffefb', '#c6c4bc', '#ffffff'],
  powder: ['#fcfbf7', '#c9c4bc', '#ffffff'], berry: ['#d32a2f', '#b72329', '#f7b4ab'], rose: ['#fcf8f4', '#d6c6bd', '#ffffff'],
  lilac: ['#f3f3ef', '#c7c7c1', '#ffffff'], recess: ['#edeee9', '#c8c8bf', '#ffffff'],
};
const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);

export class PaperRenderer {
  constructor() {
    this.alive = true; this.canvas = document.createElement('canvas'); this.textures = []; this.shaders = [];
    this.maps = makeMaps(); this.renderer = 'canvas2d';
    try {
      const gl = this.canvas.getContext('webgl2', { alpha: false, antialias: false, preserveDrawingBuffer: true, depth: false, stencil: false });
      if (!gl) throw new Error('WebGL2 unavailable'); this.gl = gl;
      const compile = (type, source) => { const shader = gl.createShader(type); this.shaders.push(shader); gl.shaderSource(shader, source); gl.compileShader(shader); if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader)); return shader; };
      const vs = compile(gl.VERTEX_SHADER, VERTEX), fs = compile(gl.FRAGMENT_SHADER, FRAGMENT);
      const program = gl.createProgram(); this.program = program; gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
      gl.useProgram(program); this.buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
      const attr = gl.getAttribLocation(program, 'position'); gl.enableVertexAttribArray(attr); gl.vertexAttribPointer(attr, 2, gl.FLOAT, false, 0, 0);
      this.uniforms = Object.fromEntries(['size','origin','light','pointer','color','scale','time','relief','role','finish','hover','press'].map(name => [name, gl.getUniformLocation(program, 'u_' + name)]));
      this.maps.forEach((data, i) => {
        const texture = gl.createTexture(); this.textures.push(texture); gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, SIZE, SIZE, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
        gl.uniform1i(gl.getUniformLocation(program, ['u_fiber','u_reliefMap','u_screen'][i]), i);
      });
      this.renderer = 'webgl2';
    } catch (error) { this.failure = String(error.message).slice(0, 160); this.releaseGL(); }
    this.fallbackTile = document.createElement('canvas'); this.fallbackTile.width = this.fallbackTile.height = SIZE;
    const tileCtx = this.fallbackTile.getContext('2d');
    if (tileCtx) { const image = tileCtx.createImageData(SIZE, SIZE); for (let i = 0; i < SIZE * SIZE; i++) { const p = i * 4, value = this.maps[0][p + 2]; image.data[p] = 118; image.data[p + 1] = 87; image.data[p + 2] = 94; image.data[p + 3] = Math.abs(value - 123) * 1.2 + 3; } tileCtx.putImageData(image, 0, 0); }
    this.resize();
  }
  resize() {
    if (!this.alive) return;
    this.scale = Math.min(devicePixelRatio || 1, 1.25); this.width = Math.ceil(innerWidth * this.scale); this.height = Math.ceil(innerHeight * this.scale);
    if (this.renderer === 'webgl2') { this.canvas.width = this.width; this.canvas.height = this.height; }
  }
  render(target, params) {
    if (!this.alive) return;
    const ctx = target.getContext('2d'); if (!ctx) return;
    const { width: w, height: h, origin = [0, 0], light, pointer, stock = 'cream', relief = .5, role = 1, hover = 0, press = 0, time = 0, finish = 1 } = params;
    const color = colors[stock]?.[0] || colors.cream[0];
    if (this.renderer !== 'webgl2') { this.fallback(ctx, w, h, color, pointer, hover, press, relief, time, role); return; }
    const gl = this.gl, scale = this.scale, pw = Math.max(1, Math.min(this.width, Math.round(w * scale))), ph = Math.max(1, Math.min(this.height, Math.round(h * scale)));
    gl.viewport(0, 0, pw, ph); gl.enable(gl.SCISSOR_TEST); gl.scissor(0, 0, pw, ph); gl.useProgram(this.program);
    const u = this.uniforms; gl.uniform2f(u.size, w, h); gl.uniform2f(u.origin, ...origin); gl.uniform2f(u.light, ...light); gl.uniform2f(u.pointer, ...pointer); gl.uniform3f(u.color, ...rgb(color));
    for (const [name, value] of Object.entries({ scale, time, relief, role, finish, hover, press })) gl.uniform1f(u[name], value);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    ctx.clearRect(0, 0, w, h); ctx.drawImage(this.canvas, 0, this.height - ph, pw, ph, 0, 0, w, h);
  }
  fallback(ctx, w, h, color, pointer, hover, press, relief, time, role) {
    ctx.clearRect(0, 0, w, h); ctx.fillStyle = color; ctx.fillRect(0, 0, w, h);
    if (this.fallbackTile.width) { const pattern = ctx.createPattern(this.fallbackTile, 'repeat'); if (pattern) { ctx.fillStyle = pattern; ctx.globalAlpha = .38; ctx.fillRect(0, 0, w, h); ctx.globalAlpha = 1; } }
    const wash = ctx.createLinearGradient(0, 0, w * .25, h); wash.addColorStop(0, '#ffffff08'); wash.addColorStop(1, '#74746c03'); ctx.fillStyle = wash; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = `rgba(155,94,112,${.018 * relief})`;
    for (let yy = 8; yy < h; yy += 11) for (let xx = 8; xx < w; xx += 11) { ctx.beginPath(); ctx.arc(xx, yy, .6 + .2 * Math.sin(xx * .05 - (role < .5 ? time * .4 : 0)), 0, Math.PI * 2); ctx.fill(); }
    if (press > .01) { ctx.fillStyle = `rgba(100,60,70,${press * .025})`; ctx.fillRect(0, 0, w, h); }
  }
  releaseGL() {
    if (!this.gl) return;
    const gl = this.gl; this.textures.forEach(texture => gl.deleteTexture(texture)); this.textures.length = 0;
    this.shaders.forEach(shader => gl.deleteShader(shader)); this.shaders.length = 0;
    if (this.buffer) gl.deleteBuffer(this.buffer); if (this.program) gl.deleteProgram(this.program); this.buffer = this.program = null;
    this.gl = null;
  }
  destroy() { if (!this.alive) return; this.alive = false; this.releaseGL(); this.canvas.width = this.canvas.height = 0; this.fallbackTile.width = this.fallbackTile.height = 0; this.maps.length = 0; }
}
