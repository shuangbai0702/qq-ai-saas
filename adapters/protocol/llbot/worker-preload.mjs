// Loaded only in the owned LLBot child, never the Agent or QQNT process.
const timer=setInterval(()=>process.send?.({type:'llbot-memory',pid:process.pid,...process.memoryUsage()}),5000);timer.unref();
process.on('message',value=>{if(value?.type==='reclaim-js')globalThis.gc?.()});
process.on('disconnect',()=>process.exit(0));
