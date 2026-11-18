import test from 'node:test';
import assert from 'node:assert/strict';
import {installProtocolLifecycleRuntime} from '../apps/node-agent/src/maintenance.js';
import {ProcessHost} from '../apps/node-agent/src/process-host.js';

test('lifecycle hot install preserves existing process owners and rejects a partial boundary',()=>{
 const old=()=>{},agentPrototype={shutdownOnce:old},hostPrototype={helper:old,start:old,stop:old};
 const children=new Map([['online:protocol',{pid:123}]]),host=Object.assign(Object.create(hostPrototype),{children});
 const agent=Object.assign(Object.create(agentPrototype),{closed:false,controlIdle:()=>true,host});
 Object.defineProperty(hostPrototype,'stop',{value:old,configurable:false});
 assert.throws(()=>installProtocolLifecycleRuntime(agent,'a'.repeat(64)),/contract changed/);
 assert.equal(agentPrototype.shutdownOnce,old);assert.equal(hostPrototype.start,old);
 const mutable={helper:old,start:old,stop:old};Object.setPrototypeOf(host,mutable);
 const result=installProtocolLifecycleRuntime(agent,'a'.repeat(64));
 assert.deepEqual(result.preservedChildren,[{id:'online:protocol',pid:123}]);
 assert.equal(host.children,children);assert.equal(mutable.start,ProcessHost.prototype.start);
 assert.equal(mutable.stop,ProcessHost.prototype.stop);assert.notEqual(agentPrototype.shutdownOnce,old);
});
