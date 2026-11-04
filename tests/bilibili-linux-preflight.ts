import assert from 'node:assert/strict';
import {managedProcess} from '../apps/node-agent/src/media/lifecycle.js';
assert.equal(process.platform,'linux');
await assert.rejects(()=>managedProcess(process.execPath,['--version'],{cwd:'/tmp',signal:new AbortController().signal,timeoutMs:1000}), (e:any)=>e.code==='media_cgroup_required');
console.log('PASS Linux media fails closed before spawn without cgroup ownership');
