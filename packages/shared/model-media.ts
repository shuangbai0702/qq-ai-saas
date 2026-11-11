/** One file is shared with the Python model-image and annotation adapters. */
import {readFileSync} from 'node:fs';
import path from 'node:path';
const policy=JSON.parse(readFileSync(path.resolve('templates/model-media.json'),'utf8'));
if(!Number.isInteger(policy.maxEdge)||policy.maxEdge<1||policy.maxEdge>384)throw new Error('Invalid model image limit');
export const MODEL_IMAGE_EDGE:number=policy.maxEdge;

/** Apply source dimensions once. Legacy preview caps do not select model tiers. */
export function modelImageTargetEdge(longest:number):number {
 if(!Number.isSafeInteger(longest)||longest<1)throw new RangeError('Invalid original image dimensions');
 if(longest>1024)return 512;
 if(longest>=512)return 256;
 if(longest>256)return 192;
 if(longest>=128)return 128;
 return longest;
}
