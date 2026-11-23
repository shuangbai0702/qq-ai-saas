import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium} from 'playwright';

// Real browser encoders and decoders, independent of the canvas implementation.
const bundle=await build({entryPoints:['apps/web/src/character-card-cover.ts'],bundle:true,write:false,format:'iife',globalName:'CardCover'});
const browser=await chromium.launch();
try{
 const page=await browser.newPage();await page.addScriptTag({content:bundle.outputFiles[0].text});
 const proof=await page.evaluate(async()=>{
  const api=(window as any).CardCover,source=document.createElement('canvas');source.width=600;source.height=1800;
  const ctx=source.getContext('2d')!;ctx.fillStyle='#885599';ctx.fillRect(0,0,600,1800);
  const blob=await new Promise<Blob>(resolve=>source.toBlob(blob=>resolve(blob!),'image/png'));
  const original=createImageBitmap;let closed=0;
  // Mobile-compatible image-element path also accepts files without MIME data.
  (window as any).createImageBitmap=()=>Promise.reject(Error('decoder unavailable'));
  const create=URL.createObjectURL,revoke=URL.revokeObjectURL;let created=0,revoked=0;
  URL.createObjectURL=(blob)=>{created++;return create(blob)};URL.revokeObjectURL=url=>{revoked++;revoke(url)};
  const fallback=await api.coverFromFile(new File([blob],'portrait.png',{type:''}));
  let invalid='',oversized='';
  try{await api.coverFromFile(new File(['broken'],'broken.png',{type:'image/png'}))}catch(error){invalid=(error as Error).message}
  try{await api.coverFromFile(new File([new Uint8Array(8*1024*1024+1)],'large.png'))}catch(error){oversized=(error as Error).message}
  (window as any).createImageBitmap=async(file:Blob)=>{const bitmap=await original(file),close=bitmap.close.bind(bitmap);bitmap.close=()=>{closed++;close()};return bitmap};
  const normal=await api.coverFromFile(new File([blob],'portrait.png',{type:'image/png'}));
  const decoded=await original(await(await fetch(normal)).blob());const dimensions={width:decoded.width,height:decoded.height};decoded.close();
  source.width=32;source.height=16;ctx.fillStyle='#112233';ctx.fillRect(0,0,32,16);
  const small=await api.coverFromFile(new File([await new Promise<Blob>(resolve=>source.toBlob(blob=>resolve(blob!)))],'small.png'));
  const smallBitmap=await original(await(await fetch(small)).blob());const smallSize=[smallBitmap.width,smallBitmap.height];smallBitmap.close();
  const nativeEncode=HTMLCanvasElement.prototype.toBlob,jpegFallback=[];
  source.width=600;source.height=1800;ctx.fillStyle='#885599';ctx.fillRect(100,100,400,1600);
  const transparent=await new Promise<Blob>(resolve=>nativeEncode.call(source,blob=>resolve(blob!),'image/png'));
  for(const mode of ['png','null','throw']){
   HTMLCanvasElement.prototype.toBlob=function(callback,type,quality){
    if(type==='image/webp'){
     if(mode==='throw')throw Error('unsupported encoding');
     if(mode==='null'){callback(null);return}
     nativeEncode.call(this,callback,'image/png');return;
    }
    nativeEncode.call(this,callback,type,quality);
   };
   const url=await api.coverFromFile(new File([transparent],'transparent.png'));
   const result=await(await fetch(url)).blob(),bitmap=await original(result),preview=document.createElement('canvas');preview.width=bitmap.width;preview.height=bitmap.height;
   const pixels=preview.getContext('2d')!;pixels.drawImage(bitmap,0,0);
   jpegFallback.push({type:result.type,bytes:result.size,width:bitmap.width,height:bitmap.height,corner:[...pixels.getImageData(0,0,1,1).data]});bitmap.close();
  }
  HTMLCanvasElement.prototype.toBlob=nativeEncode;
  return{fallback: fallback.startsWith('data:image/webp;base64,'),dimensions,smallSize,created,revoked,closed,invalid,oversized,jpegFallback};
 });
 assert.equal(proof.fallback,true);assert.deepEqual(proof.dimensions,{width:341,height:1024});assert.deepEqual(proof.smallSize,[32,16]);assert.equal(proof.created,proof.revoked);assert.equal(proof.closed,5);assert.match(proof.invalid,/无法读取/);assert.match(proof.oversized,/8MB/);
 for(const jpeg of proof.jpegFallback){assert.equal(jpeg.type,'image/jpeg');assert.ok(jpeg.bytes<=128*1024);assert.deepEqual([jpeg.width,jpeg.height],[341,1024]);assert.deepEqual(jpeg.corner,[255,255,255,255])}
 console.log('Cover browser passed:',proof);
}finally{await browser.close()}
