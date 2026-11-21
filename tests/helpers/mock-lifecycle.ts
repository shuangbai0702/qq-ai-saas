import {readdir} from 'node:fs/promises';
import path from 'node:path';
import type {NodeAgent} from '../../apps/node-agent/src/agent.js';

// Production Agent shutdown deliberately preserves QQ. Fixtures own their
// detached mock protocols and stop them using the normal identity-checked host.
export async function stopMockProtocols(agent:NodeAgent){
 const entries=await readdir(path.join(agent.host.root,'bots'),{withFileTypes:true}).catch(error=>{if(error.code==='ENOENT')return [];throw error});
 for(const entry of entries)if(entry.isDirectory()&&await agent.host.record(entry.name,'protocol'))await agent.protocols.mock.stop(entry.name);
}
