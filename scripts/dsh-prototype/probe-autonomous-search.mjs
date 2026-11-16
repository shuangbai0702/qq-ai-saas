/** Opt-in, bounded real-model experiment. No production, QQ or global DSH config. */
import {mkdtemp} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {createKernel,delegateLocal} from '../../packages/dsh/kernel.mjs';
import {nativeModel} from '../../packages/dsh/native-model.mjs';
import {mcpSource} from '../../packages/dsh/mcp-source.mjs';

if(process.env.FISHBOT_DSH_REAL_MODEL!=='1')throw Error('Explicit real-model opt-in required');
let adapter,source,kernel;const usage=[],calls=[];let modelCalls=0;
try{
 source=await mcpSource({serverName:'exa',url:'https://mcp.exa.ai/mcp',allowedReadTools:['mcp__exa__web_search_exa','mcp__exa__web_fetch_exa']});
 if(!(await source.discover()).length)throw Error('No authorized schema-compatible search tools');
 adapter=await nativeModel({baseURL:'https://nai.lunarbyte.pw/v1',model:'deepseek-v4.1-flash'});
 kernel=await createKernel({storageRoot:await mkdtemp(path.join(os.tmpdir(),'fishbot-real-dsh-')),adapter});
 const host={identity:{tenantId:'local-fixture',botId:'isolated-fixture',workId:'local-experiment',scope:'private:local-fixture',delegationId:crypto.randomUUID()},assertCurrent:async()=>{},authorize:async item=>item.capability==='web_search'&&item.effect==='read',discover:source.discover,validateArguments:source.validateArguments,
  async reserveModel(){if(++modelCalls>4)throw Error('Local experiment four-request cap reached');return{settle:async value=>usage.push(value)};},
  async execute(item,args,exec){calls.push(item.id);return source.execute(item,args,exec);},
 };
 const final=await delegateLocal(kernel,host,{goal:'联网查证 DeepSeek 官方 API 是否提供网页搜索能力，用中文简短回答并给出官方来源链接。只读。',constraints:['只根据实际读取到的证据回答；失败或不确定必须如实说明。']},{timeoutMs:120000});
 console.log(JSON.stringify({nativeModel:true,modelCalls,usage,toolsCalled:calls,final,productionAccess:false,qqAccess:false,authoritativeBudgetIntegrated:false}));
 if(!calls.length||!final.evidence.length||!usage.some(v=>v.state==='completed'))process.exitCode=1;
}catch(error){console.log(JSON.stringify({success:false,error:error.message,modelCalls,usage,toolsCalled:calls}));process.exitCode=1;}
finally{await kernel?.close();await adapter?.close();await source?.close();}
