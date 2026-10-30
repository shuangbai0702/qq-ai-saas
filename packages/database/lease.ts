import lockfile from 'proper-lockfile';import path from 'node:path';
// A heartbeat directory is shared across PID namespaces. A recycled container
// PID cannot claim the database, and a crashed process releases after 20s.
export async function acquireLease(root:string,name:'database'|'agent'){
 const wait=Math.min(60000,Math.max(0,Number(process.env.DB_LOCK_WAIT_MS)||0));
 return lockfile.lock(path.join(root,name+'-owner'),{realpath:false,lockfilePath:path.join(root,name+'.lease'),stale:20000,update:5000,retries:{retries:Math.ceil(wait/1000),minTimeout:1000,maxTimeout:1000,factor:1}});
}
