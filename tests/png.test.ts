import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {crc32,deflateSync} from 'node:zlib';import {validatePng} from '../apps/control-api/src/png.js';
function chunk(type:string,data:Buffer){const out=Buffer.alloc(data.length+12);out.writeUInt32BE(data.length);out.write(type,4);data.copy(out,8);out.writeUInt32BE(crc32(out.subarray(4,-4)),out.length-4);return out}
function png(width:number,height:number,pixels:Buffer,interlace=0){const header=Buffer.alloc(13);header.writeUInt32BE(width);header.writeUInt32BE(height,4);header[8]=8;header[9]=6;header[12]=interlace;return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))])}
test('PNG validates approved images, CRC, complete pixels, filter bytes and bounded inflate',async()=>{
 for(const name of ['laugh','think','angry']){const data=await readFile('templates/reactions/'+name+'.png').catch(()=>null);if(data)assert.ok((await validatePng(data))!.decodedBytes>0)}
 const valid=png(1,1,Buffer.from([0,255,0,0,255]));assert.deepEqual(await validatePng(valid),{width:1,height:1,decodedBytes:5});
 const corrupt=Buffer.from(valid);corrupt[45]^=1;await assert.rejects(()=>validatePng(corrupt),/PNG/);
 await assert.rejects(()=>validatePng(valid.subarray(0,-1)),/PNG/);
 await assert.rejects(()=>validatePng(Buffer.concat([valid,Buffer.from('trailing')])),/PNG/);
 await assert.rejects(()=>validatePng(Buffer.concat([valid.subarray(0,33),chunk('IDAT',Buffer.concat([deflateSync(Buffer.alloc(5)),Buffer.from([0])])),chunk('IEND',Buffer.alloc(0))])),/PNG/);
 await assert.rejects(()=>validatePng(png(1,1,Buffer.alloc(4))),/PNG/);
 await assert.rejects(()=>validatePng(png(1,1,Buffer.from([5,1,2,3,4]))),/PNG/);
 await assert.rejects(()=>validatePng(png(1,1,Buffer.alloc(8*1024*1024))),/PNG/,'tiny declared image cannot inflate megabytes');
 await assert.rejects(()=>validatePng(png(100000,100000,Buffer.alloc(1))),/PNG/);
 assert.ok(await validatePng(png(1,1,Buffer.alloc(5),1)),'Adam7 single pixel');
});
