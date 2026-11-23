import {characterCardCover as limits} from '../../../packages/contracts/character-cards';

async function decodeCover(file:File){
 if(typeof createImageBitmap==='function')try{
  const bitmap=await createImageBitmap(file);
  return{source:bitmap,width:bitmap.width,height:bitmap.height,close:()=>bitmap.close()};
 }catch{/* Some mobile decoders support an image element but not ImageBitmap. */}
 const url=URL.createObjectURL(file),image=new Image();
 try{
  await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(Error('无法读取这张图片，请确认图片完整且浏览器支持此格式'));image.src=url});
  return{source:image,width:image.naturalWidth,height:image.naturalHeight,close:()=>{image.src='';URL.revokeObjectURL(url)}};
 }catch(error){URL.revokeObjectURL(url);throw error}
}

function encode(canvas:HTMLCanvasElement,quality:number){
 return new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>{
  if(!blob||blob.type!==limits.mime)reject(Error('当前浏览器无法生成封面，请更新浏览器后重试'));
  else resolve(blob);
 },limits.mime,quality));
}

export async function coverFromFile(file:File){
 if(file.size>limits.sourceMaxBytes)throw Error('请选择8MB以内的图片');
 const image=await decodeCover(file),canvas=document.createElement('canvas');
 try{
  if(!image.width||!image.height)throw Error('图片尺寸无效，请重新选择');
  const ctx=canvas.getContext('2d');if(!ctx)throw Error('无法处理图片，请重新选择');
  let scale=Math.min(1,limits.maxEdge/Math.max(image.width,image.height));
  for(;;){
   canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));
   ctx.drawImage(image.source,0,0,canvas.width,canvas.height);
   // Detail-heavy originals need adaptive quality, then smaller dimensions.
   // Compare actual encoded bytes against the same contract as the API.
   for(const quality of [.84,.72,.60,.48,.36]){
    const blob=await encode(canvas,quality);
    if(blob.size<=limits.maxBytes)return await new Promise<string>((resolve,reject)=>{
     const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error('封面处理失败，请重新选择图片'));reader.readAsDataURL(blob);
    });
   }
   if(Math.max(canvas.width,canvas.height)===1)throw Error('封面处理失败，请重新选择图片');
   scale*=.75;
  }
 }finally{image.close();canvas.width=0;canvas.height=0}
}
