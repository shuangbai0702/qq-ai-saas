/** One file is shared with the Python model-image and annotation adapters. */
import {readFileSync} from 'node:fs';
import path from 'node:path';
const policy=JSON.parse(readFileSync(path.resolve('templates/model-media.json'),'utf8'));
if(!Number.isInteger(policy.maxEdge)||policy.maxEdge<1||policy.maxEdge>384)throw new Error('Invalid model image limit');
export const MODEL_IMAGE_EDGE:number=policy.maxEdge;
