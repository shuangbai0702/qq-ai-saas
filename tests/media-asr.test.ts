import test from 'node:test';import assert from 'node:assert/strict';
import {asrSegments} from '../apps/node-agent/src/media/asr.js';
test('ASR evidence rejects malformed, out-of-range, overlapping and empty text segments',()=>{
 for(const value of [null,{}, {segments:[null]}, {segments:[{start:0,end:1,text:' '}]},{segments:[{start:0,end:11,text:'越界'}]},{segments:[{start:2,end:3,text:'甲'},{start:1,end:2,text:'乙'}]},{segments:[{start:0,end:2,text:'甲'},{start:1,end:3,text:'乙'}]},{segments:[{start:0,end:0,text:'甲'}]}])assert.throws(()=>asrSegments(value,10),(e:any)=>e.code==='asr_invalid');
 assert.deepEqual(asrSegments({segments:[]},10),[]);
 assert.deepEqual(asrSegments({segments:[{start:.37,end:5.37,text:'開放時間早上9點至下午5點'}]},5.616),[{start:.37,end:5.37,text:'開放時間早上9點至下午5點'}]);
});
