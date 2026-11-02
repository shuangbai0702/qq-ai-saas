const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const vertex=`#version 300 es
in vec2 position;
void main(){gl_Position=vec4(position,0.0,1.0);}`;
const fragment=`#version 300 es
precision highp float;
uniform sampler2D wave;
uniform vec2 resolution;
uniform vec2 cell;
uniform vec2 pointer;
uniform vec4 stage;
uniform float time;
out vec4 color;
float field(vec2 uv){return (texture(wave,clamp(uv,vec2(0.001),vec2(0.999))).r-.5)*4.0;}
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
void main(){
 vec2 pixel=vec2(gl_FragCoord.x,resolution.y-gl_FragCoord.y);
 vec2 uv=pixel/resolution;
 float h=field(uv);
 vec2 slope=vec2(field(uv+vec2(cell.x,0.))-field(uv-vec2(cell.x,0.)),field(uv+vec2(0.,cell.y))-field(uv-vec2(0.,cell.y)));
 vec2 p=uv*vec2(resolution.x/resolution.y,1.0);
 float ambient=.045*sin(p.x*5.3+time*.19)*cos(p.y*4.7-time*.13);
 slope+=vec2(cos(p.x*5.3+time*.19)*.07,sin(p.y*4.7-time*.13)*.07);
 vec3 normal=normalize(vec3(-slope*3.1,1.0));
 vec3 light=normalize(vec3((pointer-.5)*.75,1.0));
 float spec=pow(max(dot(reflect(-light,normal),vec3(0.,0.,1.)),0.),28.);
 vec2 edge=min(pixel-stage.xy,stage.xy+stage.zw-pixel);
 float inside=smoothstep(-2.,12.,min(edge.x,edge.y));
 float band=.5+.5*sin(p.x*5.4+p.y*3.7+time*.095+(h+ambient)*1.8);
 vec3 base=mix(vec3(.969,.978,.990),vec3(.901,.940,.985),inside*(.18+band*.45));
 vec2 warped=uv+slope*.024;
 float cloud=sin(warped.x*5.2+time*.075)*.65+cos(warped.y*5.7-time*.065)*.7+sin((warped.x+warped.y)*3.5)*.35;
 float lineA=abs(sin((cloud+(h+ambient)*.36)*4.4));
 float lines=(1.-smoothstep(.02,.02+fwidth(lineA)*1.8,lineA))*.35;
 float activity=smoothstep(.025,.3,abs(h));
 base-=vec3(.17,.10,.025)*lines*(.1+inside*.32+activity*.7);
 base+=vec3(.031,.034,.04)*spec*(.18+inside*.35+activity*.6);
 base-=vec3(.085,.064,.034)*slope.x*(.2+inside*.5);
 base+=vec3(.014,.017,.021)*(hash(pixel)-.5);
 color=vec4(base,1.0);
}`;

/** CPU wave equation + GPU normals, interference and reflected material light. No own clock. */
export class MaterialField{
 constructor(canvas){
  this.canvas=canvas;this.alive=true;this.w=1;this.h=1;
  try{this.initGL();}catch(error){
   this.disposeGL();
   const fallback=document.createElement('canvas');fallback.id=canvas.id;fallback.setAttribute('aria-hidden','true');canvas.replaceWith(fallback);this.canvas=fallback;this.ctx=fallback.getContext('2d');this.canvas.dataset.renderer='canvas2d';
  }
  this.resize();
 }
 initGL(){
  const gl=this.canvas.getContext('webgl2',{alpha:false,antialias:false,preserveDrawingBuffer:true,powerPreference:'low-power'});if(!gl)throw Error('WebGL2 unavailable');
  this.gl=gl;this.shaders=[];
  const compile=(type,source)=>{const shader=gl.createShader(type);this.shaders.push(shader);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));return shader;};
  const vs=compile(gl.VERTEX_SHADER,vertex),fs=compile(gl.FRAGMENT_SHADER,fragment),program=gl.createProgram();this.program=program;gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));for(const shader of this.shaders)gl.deleteShader(shader);this.shaders=[];gl.useProgram(program);
  this.buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);const position=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
  this.texture=gl.createTexture();gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.pixelStorei(gl.UNPACK_ALIGNMENT,1);
  this.uniforms=Object.fromEntries(['wave','resolution','cell','pointer','stage','time'].map(key=>[key,gl.getUniformLocation(program,key)]));gl.uniform1i(this.uniforms.wave,0);this.canvas.dataset.renderer='webgl2';
 }
 resize(){
  const r=this.canvas.getBoundingClientRect();this.w=r.width;this.h=r.height;this.dpr=Math.min(devicePixelRatio||1,1.5);this.canvas.width=Math.round(r.width*this.dpr);this.canvas.height=Math.round(r.height*this.dpr);
  this.nx=this.w<700?48:88;this.ny=clamp(Math.round(this.nx*this.h/this.w),40,160);this.height=new Float32Array(this.nx*this.ny);this.velocity=new Float32Array(this.nx*this.ny);this.accel=new Float32Array(this.nx*this.ny);this.pixels=new Uint8Array(this.nx*this.ny);this.pixels.fill(128);
  if(this.gl){const g=this.gl;g.viewport(0,0,this.canvas.width,this.canvas.height);g.bindTexture(g.TEXTURE_2D,this.texture);g.texImage2D(g.TEXTURE_2D,0,g.R8,this.nx,this.ny,0,g.RED,g.UNSIGNED_BYTE,this.pixels);}
 }
 disturb(x,y,strength=1,radius=2.4){
  if(!this.alive)return;const cx=x/this.w*(this.nx-1),cy=y/this.h*(this.ny-1),reach=Math.ceil(radius*3);
  for(let iy=Math.max(1,Math.floor(cy-reach));iy<=Math.min(this.ny-2,Math.ceil(cy+reach));iy++)for(let ix=Math.max(1,Math.floor(cx-reach));ix<=Math.min(this.nx-2,Math.ceil(cx+reach));ix++){const d=((ix-cx)**2+(iy-cy)**2)/(radius*radius),weight=Math.exp(-d*.85)-.27*Math.exp(-d*.2),index=iy*this.nx+ix;this.height[index]=clamp(this.height[index]+strength*weight*.19,-1.5,1.5);this.velocity[index]+=strength*weight*3.9;}
 }
 advance(dt){
  const {height:h,velocity:v,accel:a,nx,ny}=this,step=Math.min(dt,.034)/2;
  for(let iteration=0;iteration<2;iteration++){
   for(let y=1;y<ny-1;y++)for(let x=1;x<nx-1;x++){const i=y*nx+x;a[i]=(h[i-1]+h[i+1]+h[i-nx]+h[i+nx]-h[i]*4)*175-v[i]*2.1;}
   for(let i=nx;i<h.length-nx;i++){v[i]+=a[i]*step;h[i]=clamp(h[i]+v[i]*step,-1.7,1.7);}
  }
 }
 draw(time,pointer,stage){
  if(!this.alive)return;
  if(this.gl){
   const gl=this.gl,u=this.uniforms;for(let i=0;i<this.height.length;i++)this.pixels[i]=Math.round(clamp(this.height[i]/4+.5,0,1)*255);
   gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,this.nx,this.ny,gl.RED,gl.UNSIGNED_BYTE,this.pixels);gl.uniform2f(u.resolution,this.canvas.width,this.canvas.height);gl.uniform2f(u.cell,1/this.nx,1/this.ny);gl.uniform2f(u.pointer,pointer.x/this.w,pointer.y/this.h);gl.uniform4f(u.stage,stage.x*this.dpr,stage.y*this.dpr,stage.width*this.dpr,stage.height*this.dpr);gl.uniform1f(u.time,time);gl.drawArrays(gl.TRIANGLES,0,6);
  }else{
   const c=this.ctx;c.setTransform(this.dpr,0,0,this.dpr,0,0);c.fillStyle='#f7f9fc';c.fillRect(0,0,this.w,this.h);const gradient=c.createLinearGradient(0,stage.y,this.w,stage.y+stage.height);gradient.addColorStop(0,'#eaf2fe');gradient.addColorStop(.5,'#f4f8fe');gradient.addColorStop(1,'#e0edfc');c.fillStyle=gradient;c.fillRect(stage.x,stage.y,stage.width,stage.height);
   for(let y=1;y<this.ny-1;y+=3){c.beginPath();for(let x=0;x<this.nx;x++){const xx=x/(this.nx-1)*this.w,yy=y/(this.ny-1)*this.h+this.height[y*this.nx+x]*25+Math.sin(x*.1+time*.15)*2;c[x?'lineTo':'moveTo'](xx,yy);}c.strokeStyle='#adc5e51d';c.lineWidth=.65;c.stroke();}
  }
 }
 disposeGL(){if(!this.gl)return;const gl=this.gl;for(const shader of this.shaders||[])gl.deleteShader(shader);if(this.texture)gl.deleteTexture(this.texture);if(this.buffer)gl.deleteBuffer(this.buffer);if(this.program)gl.deleteProgram(this.program);this.shaders=[];this.gl=null;}
 destroy(){if(!this.alive)return;this.alive=false;this.disposeGL();this.height=this.velocity=this.accel=this.pixels=null;}
}
