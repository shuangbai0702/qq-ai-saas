import {atomicJson} from '../../packages/shared/fs-atomic.js';
import path from 'node:path';import {mkdir,readFile,writeFile,rename} from 'node:fs/promises';
import {BotConfigSchema,type RuntimeContext,type Health} from '../../packages/contracts/index.js';
import {ProcessHost} from '../../apps/node-agent/src/process-host.js';
export class AstrBotAdapter {
  constructor(private host:ProcessHost,private source:string,private python:string){}
  async provision(ctx:RuntimeContext){
    BotConfigSchema.parse(ctx.config);
    for(const dir of ['data','tmp','data/plugins','data/plugin_data','data/temp','data/config'])await mkdir(path.join(ctx.root,'ai',dir),{recursive:true});
    await this.applyConfig(ctx);
  }
  async applyConfig(ctx:RuntimeContext){return this.updateConfig(ctx,()=>this.applyConfigLocked(ctx))}
  private async applyConfigLocked(ctx:RuntimeContext){
    try{const prior=JSON.parse(await readFile(path.join(ctx.root,'ai/runtime.json'),'utf8'));if(prior.generation>ctx.generation||prior.generation===ctx.generation&&prior.revision>ctx.revision)throw Object.assign(Error('Runtime configuration superseded'),{superseded:true})}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error}
    const cfg=ctx.config,modelConfigured=!!ctx.apiKey&&!!cfg.model.trim();
    const value={config_version:5,dashboard:{enable:false,host:'127.0.0.1'},admins_id:[],wake_prefix:[''],
      platform_settings:{empty_mention_waiting:false,unique_session:false,enable_id_white_list:false,id_whitelist:[],wl_ignore_admin_on_group:false,friend_message_needs_wake_prefix:false},
      platform:[{id:'qq',type:'aiocqhttp',enable:true,ws_reverse_host:'127.0.0.1',ws_reverse_port:ctx.aiPort,ws_reverse_token:ctx.token}],
      provider_sources:[],provider:modelConfigured?[{id:'platform-model',type:'openai_chat_completion',provider_type:'chat_completion',modalities:['text','image','tool_use'],enable:true,key:['$FISHBOT_MODEL_KEY'],api_base:cfg.baseUrl,model:cfg.model,timeout:60,...(cfg.reasoningEffort?{custom_extra_body:{reasoning_effort:cfg.reasoningEffort}}:{})}]:[],
      provider_settings:{enable:modelConfigured,identifier:true,group_name_display:true,datetime_system_prompt:true,computer_use_runtime:'none',proactive_capability:{add_cron_tools:false}},
      agent_runner:{runner_type:'local',config:{model:{provider_id:modelConfigured?'platform-model':''},persona:{persona_id:'platform',safety_mode:true},compression:{max_turns:cfg.historyTurns,overflow_strategy:'truncate_by_turns'},misc:{max_steps:5}}},
      provider_ltm_settings:{group_icl_enable:false,group_message_history_enable:true,group_message_history_max_cnt:100,active_reply:{enable:false,method:'possibility_reply',possibility_reply:0.02,whitelist:cfg.groups}},
      t2i:false,log_level:'INFO',plugin_set:[],disable_builtin_commands:true,
    };
    const root=path.join(ctx.root,'ai');await mkdir(path.join(root,'data'),{recursive:true});
    await atomicJson(path.join(root,'data/cmd_config.json'),value);
    await writeFile(path.join(root,'persona.txt'),cfg.systemPrompt,{mode:0o600});
    await atomicJson(path.join(root,'runtime.json'),{generation:ctx.generation,revision:ctx.revision,systemChangePermitId:ctx.systemChangePermitId,personaEdit:ctx.personaEdit,characterCardsEdit:ctx.characterCardsEdit,token:ctx.token,aiPort:ctx.aiPort,expectedUin:ctx.expectedUin,groups:cfg.groups,botId:ctx.botId,tenantId:ctx.tenantId,config:cfg});
    return {appliedRevision:ctx.revision,restartRequired:!!(await this.host.inspect(ctx.botId,'ai'))};
  }
  async start(ctx:RuntimeContext){await this.provision(ctx);const root=path.join(ctx.root,'ai');
    const env:NodeJS.ProcessEnv={...process.env,ASTRBOT_ROOT:root,TMP: path.join(root,'tmp'),TEMP:path.join(root,'tmp'),TMPDIR:path.join(root,'tmp'),FISHBOT_MODEL_KEY:ctx.apiKey,PYTHONUNBUFFERED:'1',PYTHONIOENCODING:'utf-8'};
    delete env.MASTER_KEY;delete env.DATABASE_URL;
    await this.host.start(ctx.botId,'ai',this.python,[path.resolve('adapters/astrbot/headless.py'),this.source,root],root,env,[ctx.apiKey,ctx.token]);
  }
  async stop(botId:string){await this.host.stop(botId,'ai')}
  private configUpdates=new Map<string,Promise<unknown>>();
  private async updateConfig<T>(ctx:RuntimeContext,run:()=>Promise<T>):Promise<T>{
    const previous=this.configUpdates.get(ctx.botId);
    const update=(async()=>{if(previous)await previous.catch(()=>{});return run()})();
    this.configUpdates.set(ctx.botId,update);try{return await update}finally{if(this.configUpdates.get(ctx.botId)===update)this.configUpdates.delete(ctx.botId)}
  }
  async persistApplied(ctx:RuntimeContext){return this.updateConfig(ctx,()=>this.persistAppliedLocked(ctx))}
  private async persistAppliedLocked(ctx:RuntimeContext){
    const file=path.join(ctx.root,'ai','runtime.json'),prior=JSON.parse(await readFile(file,'utf8'));
    if(prior.generation!==ctx.generation||prior.revision>ctx.revision)return false;
    const value={...prior,revision:ctx.revision,config:ctx.config,characterCardsEdit:ctx.characterCardsEdit,personaEdit:ctx.personaEdit,toolsEdit:ctx.toolsEdit,systemChangePermitId:ctx.systemChangePermitId};
    await atomicJson(file,value);return true;
  }
  async applyCards(ctx:RuntimeContext){return this.updateConfig(ctx,()=>this.applyCardsLocked(ctx))}
  private async applyCardsLocked(ctx:RuntimeContext){
    if(!ctx.characterCardsEdit||!await this.host.inspect(ctx.botId,'ai'))return false;
    const r=await fetch(`http://127.0.0.1:${ctx.aiPort+1}/base/character-cards`,{method:'PUT',headers:{Authorization:`Bearer ${ctx.token}`,'Content-Type':'application/json'},body:JSON.stringify({botId:ctx.botId,tenantId:ctx.tenantId,generation:ctx.generation,revision:ctx.revision,config:ctx.config,characterCardsEdit:ctx.characterCardsEdit,systemChangePermitId:ctx.systemChangePermitId,apiKey:ctx.apiKey}),signal:AbortSignal.timeout(5000)});
    if(r.status===404||r.status===409)return false;if(!r.ok)throw Error('Character cards update failed');
    const ack:any=await r.json();if(ack.generation!==ctx.generation||ack.revision!==ctx.revision||ack.target!==ctx.characterCardsEdit.target)throw Error('Character cards acknowledgement mismatch');
    return this.persistAppliedLocked(ctx);
  }
  async applyTools(ctx:RuntimeContext){return this.updateConfig(ctx,()=>this.applyToolsLocked(ctx))}
  private async applyToolsLocked(ctx:RuntimeContext){
    if(!ctx.toolsEdit||!await this.host.inspect(ctx.botId,'ai'))return false;
    const r=await fetch(`http://127.0.0.1:${ctx.aiPort+1}/base/tools`,{method:'PUT',headers:{Authorization:`Bearer ${ctx.token}`,'Content-Type':'application/json'},body:JSON.stringify({botId:ctx.botId,tenantId:ctx.tenantId,generation:ctx.generation,revision:ctx.revision,config:ctx.config,toolsEdit:ctx.toolsEdit,apiKey:ctx.apiKey}),signal:AbortSignal.timeout(5000)});
    if(r.status===404||r.status===409)return false;if(!r.ok)throw Error('Tool update failed');
    return this.persistAppliedLocked(ctx);
  }
  async applyPersona(ctx:RuntimeContext){return this.updateConfig(ctx,()=>this.applyPersonaLocked(ctx))}
  private async applyPersonaLocked(ctx:RuntimeContext){
    if(!ctx.personaEdit||!await this.host.inspect(ctx.botId,'ai'))return false;
    const r=await fetch(`http://127.0.0.1:${ctx.aiPort+1}/base/persona`,{method:'PUT',headers:{Authorization:`Bearer ${ctx.token}`,'Content-Type':'application/json'},body:JSON.stringify({botId:ctx.botId,tenantId:ctx.tenantId,generation:ctx.generation,revision:ctx.revision,config:ctx.config,personaEdit:ctx.personaEdit,systemChangePermitId:ctx.systemChangePermitId,apiKey:ctx.apiKey}),signal:AbortSignal.timeout(5000)});
    if(r.status===404||r.status===409)return false;
    if(!r.ok)throw Error('Persona update failed');
    if(!await this.persistAppliedLocked(ctx))return false;
    await writeFile(path.join(ctx.root,'ai','persona.txt'),ctx.config.systemPrompt,{mode:0o600});return true;
  }
  async probe(ctx:RuntimeContext):Promise<Health>{const p=await this.host.inspect(ctx.botId,'ai');const h:Health={alive:!!p,ready:false,...(p?{pid:p.pid,rssBytes:p.rssBytes,memoryPolicy:p.memoryPolicy}:{})};if(!h.alive)return h;try{const r=await fetch(`http://127.0.0.1:${ctx.aiPort+1}/health`,{headers:{Authorization:`Bearer ${ctx.token}`},signal:AbortSignal.timeout(1500)});const data:any=await r.json();return {...h,ready:r.ok&&data.generation===ctx.generation&&data.revision===ctx.revision,metrics:data.metrics?.qq,detail:data.modelConfigured?'模型已配置，API可用性需实际对话验证':'尚未配置模型Key'}}catch{return h}}
}
