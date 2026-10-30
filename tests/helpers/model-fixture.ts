import type {Server} from 'node:http';
// Production has no implicit localhost exception. Integration tests explicitly
// trust only their own bound fixture origin via the operator-only setting.
export function permitModelFixture(server:Server){
 const address=server.address();if(!address||typeof address==='string'||address.address!=='127.0.0.1')throw new Error('Expected a local model fixture');
 const origin='http://127.0.0.1:'+address.port,key='FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS';const before=process.env[key];process.env[key]=[before,origin].filter(Boolean).join(',');
 server.once('close',()=>{const remaining=(process.env[key]||'').split(',').filter(x=>x!==origin);if(remaining.length)process.env[key]=remaining.join(',');else delete process.env[key]});
}
