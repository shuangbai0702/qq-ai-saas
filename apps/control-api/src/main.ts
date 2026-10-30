import 'dotenv/config';import {createApp} from './app.js';
const {app}=await createApp({logger:true,agent:process.env.EXTERNAL_AGENT!=='true'});await app.listen({port:Number(process.env.PORT||8787),host:process.env.HOST||'127.0.0.1'});
for(const sig of ['SIGINT','SIGTERM'])process.on(sig,async()=>{await app.close();process.exit()});
process.on('message',async message=>{if(message==='shutdown'){await app.close();process.exit()}});
