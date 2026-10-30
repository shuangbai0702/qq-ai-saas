import {crc32,createInflate} from 'node:zlib';

// Validate chunks and the complete bounded scanline stream, not just a PNG header.
// No raster copy or image SDK is loaded. Includes the seven Adam7 passes.
export async function validatePng(bytes:Buffer){
 const fail=()=>{throw Object.assign(new Error('PNG结构或压缩像素数据无效（最大256KiB、2048×2048）'),{statusCode:400})};
 if(bytes.length<57||bytes.length>262144||bytes.subarray(0,8).toString('hex')!=='89504e470d0a1a0a')fail();
 let offset=8,width=0,height=0,depth=0,color=0,interlace=0,palette=0,ended=false,idatEnded=false;const chunks:Buffer[]=[];
 while(offset<bytes.length){
  if(offset+12>bytes.length)fail();const length=bytes.readUInt32BE(offset),type=bytes.subarray(offset+4,offset+8).toString('ascii');
  if(offset+12+length>bytes.length||! /^[A-Za-z]{4}$/.test(type))fail();
  const value=bytes.subarray(offset+8,offset+8+length);
  if(crc32(bytes.subarray(offset+4,offset+8+length))!==bytes.readUInt32BE(offset+8+length))fail();
  if(offset===8&&type!=='IHDR')fail();
  if(type==='IHDR'){
   if(offset!==8||length!==13)fail();width=value.readUInt32BE(0);height=value.readUInt32BE(4);depth=value[8];color=value[9];interlace=value[12];
   if(!width||!height||width>2048||height>2048||value[10]||value[11]||interlace>1)fail();
   const depths:Record<number,number[]>={0:[1,2,4,8,16],2:[8,16],3:[1,2,4,8],4:[8,16],6:[8,16]};if(!depths[color]?.includes(depth))fail();
  }else if(type==='PLTE'){
   if(palette||chunks.length||color===0||color===4||!length||length%3||length>768||(color===3&&length/3>2**depth))fail();palette=length/3;
  }else if(type==='IDAT'){
   if(idatEnded||(color===3&&!palette))fail();chunks.push(value);
  }else if(type==='IEND'){
   if(length||!chunks.length||offset+12!==bytes.length)fail();ended=true;
  }else if(type[0]===type[0].toUpperCase())fail();
  if(chunks.length&&type!=='IDAT')idatEnded=true;
  offset+=12+length;
 }
 if(!ended)fail();
 const channels=({0:1,2:3,3:1,4:2,6:4}as Record<number,number>)[color];const bits=channels*depth;
 const passes=interlace?[[0,0,8,8],[4,0,8,8],[0,4,4,8],[2,0,4,4],[0,2,2,4],[1,0,2,2],[0,1,1,2]]:[[0,0,1,1]];
 const scans=passes.map(([x,y,dx,dy])=>{const w=Math.max(0,Math.ceil((width-x)/dx)),h=Math.max(0,Math.ceil((height-y)/dy));return {row:Math.ceil(w*bits/8),height:w?h:0}});
 const expected=scans.reduce((n,s)=>n+(s.row+1)*s.height,0);if(expected>34*1024*1024)fail();
 const filters:number[]=[];let cursor=0;for(const scan of scans)for(let y=0;y<scan.height;y++){filters.push(cursor);cursor+=scan.row+1}
 const compressed=Buffer.concat(chunks),inflater=createInflate({chunkSize:65536});let decoded=0,nextFilter=0;
 try{
  inflater.end(compressed);
  for await(const part of inflater){const buffer=part as Buffer;if(decoded+buffer.length>expected)fail();while(nextFilter<filters.length&&filters[nextFilter]<decoded+buffer.length){if(buffer[filters[nextFilter]-decoded]>4)fail();nextFilter++}decoded+=buffer.length}
  if(decoded!==expected||inflater.bytesWritten!==compressed.length)fail();
 }catch{fail()}finally{inflater.destroy()}
 return {width,height,decodedBytes:expected};
}
