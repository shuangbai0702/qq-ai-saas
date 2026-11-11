import assert from 'node:assert/strict';
import test from 'node:test';
import {modelImageTargetEdge} from '../packages/shared/model-media.js';

test('model image tiers use original longest edge and exact boundaries',()=>{
 for(const [original,expected] of [[1,1],[127,127],[128,128],[129,128],[255,128],[256,128],[257,192],[511,192],[512,256],[513,256],[1024,256],[1025,512],[1920,512]]){
  assert.equal(modelImageTargetEdge(original),expected,`original longest edge ${original}`);
 }
});
