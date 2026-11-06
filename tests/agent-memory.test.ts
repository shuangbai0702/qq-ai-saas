import test from 'node:test';import assert from 'node:assert/strict';
import {agentHeapArgs} from '../apps/node-agent/src/agent-memory.js';
test('Agent alone gains bounded startup headroom, preserving other flags',()=>{
 assert.deepEqual(agentHeapArgs(['--max-old-space-size=128','--enable-source-maps']),['--enable-source-maps','--max-old-space-size=192']);
 const current=['--max-old-space-size=192'];assert.equal(agentHeapArgs(current),current);
 const larger=['--max-old-space-size=256'];assert.equal(agentHeapArgs(larger),larger);
 assert.deepEqual(agentHeapArgs(larger,'128'),['--max-old-space-size=128']);
 for(const v of ['0','127','385','NaN','192.5',''])assert.throws(()=>agentHeapArgs([],v));
});
