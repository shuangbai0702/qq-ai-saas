/** Child for a targeted abrupt-exit test; no external requests or production state. */
import {createKernel,installedDsh,sessionIdentity} from '../../packages/dsh/kernel.mjs';
const native=await installedDsh(),{LlmAdapter,createUserMessage}=await native.load('dsh-llm');
class Fixture extends LlmAdapter{async *stream(){yield{type:'block-start',index:0,blockType:'tool-call'};yield{type:'tool-call-delta',index:0,id:'crash-call',name:'slow_read',argumentsDelta:'{}'};yield{type:'block-end',index:0,block:{type:'tool-call',id:'crash-call',name:'slow_read',arguments:'{}'}};yield{type:'finish',reason:'tool-calls'};}}
const identity={tenantId:'fixture',botId:'crash-bot',workId:'crash-work',scope:'private:fixture',delegationId:'crash-test'},k=await createKernel({storageRoot:process.argv[2],adapter:new Fixture()}),id=sessionIdentity(identity);
k.owners.set(id,{async assertCurrent(){},async reserveModel(){return{async settle(){}};}});
const h=await k.ctx.agents.create({sessionId:id,agentOptions:{provider:'local-prototype',model:'fixture'},setup:scoped=>{scoped.tools.register({name:'slow_read',description:'isolated read',parameters:{type:'object'},output:{schema:{type:'object'},render:()=>[]},async execute(_args,exec){await k.ctx.sessions.flush(h.agent.session);process.send({ready:true,identity});return new Promise((_,reject)=>exec.signal.addEventListener('abort',()=>reject(exec.signal.reason),{once:true}));}});}});
h.agent.followup(createUserMessage({content:[{type:'text',text:JSON.stringify({goal:'recover crash'})}],source:{kind:'user'}}));
