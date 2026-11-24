import 'dotenv/config';import {createApp} from './app.js';
const externalAgent=process.env.EXTERNAL_AGENT==='true';
const {app}=await createApp({logger:true,externalAgent,agent:!externalAgent});await app.listen({port:Number(process.env.PORT||8787),host:process.env.HOST||'127.0.0.1'});
let shutdown:Promise<void>|undefined;
function close(){
 shutdown??=app.close().then(()=>process.exit(0),()=>{
  app.log.error('control-api shutdown failed');
  process.exit(1);
 });
}
for(const sig of ['SIGINT','SIGTERM'])process.on(sig,close);
process.on('message',message=>{if(message==='shutdown')close()});
