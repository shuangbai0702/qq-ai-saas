import path from 'node:path';import {mkdir,writeFile,rename} from 'node:fs/promises';
import {BotConfigSchema,type AiAdapter,type RuntimeContext,type Health} from '../../packages/contracts/index.js';
import {ProcessHost} from '../../apps/node-agent/src/process-host.js';
export class AstrBotAdapter implements AiAdapter {
  constructor(private host:ProcessHost,private source:string,private python:string){}
  async validateConfig(config:unknown){BotConfigSchema.parse(config)}
  async provision(ctx:RuntimeContext){
    await this.validateConfig(ctx.config);
    for(const dir of ['data','tmp','data/plugins','data/plugin_data','data/temp','data/config'])await mkdir(path.join(ctx.root,'ai',dir),{recursive:true});
    await this.applyConfig(ctx);
  }
  async applyConfig(ctx:RuntimeContext){
    const cfg=ctx.config;
    const value={config_version:5,dashboard:{enable:false,host:'127.0.0.1'},admins_id:[],wake_prefix:[''],
      platform_settings:{unique_session:false,enable_id_white_list:false,id_whitelist:[],wl_ignore_admin_on_group:false,friend_message_needs_wake_prefix:false},
      platform:[{id:'qq',type:'aiocqhttp',enable:true,ws_reverse_host:'127.0.0.1',ws_reverse_port:ctx.aiPort,ws_reverse_token:ctx.token}],
      provider_sources:[],provider:ctx.apiKey?[{id:'platform-model',type:'openai_chat_completion',provider_type:'chat_completion',enable:true,key:['$FISHBOT_MODEL_KEY'],api_base:cfg.baseUrl,model:cfg.model,timeout:60}]:[],
      provider_settings:{enable:!!ctx.apiKey,identifier:true,group_name_display:true,datetime_system_prompt:true,computer_use_runtime:'none',proactive_capability:{add_cron_tools:false}},
      agent_runner:{runner_type:'local',config:{model:{provider_id:ctx.apiKey?'platform-model':''},persona:{persona_id:'platform',safety_mode:true},compression:{max_turns:cfg.historyTurns,overflow_strategy:'truncate_by_turns'},misc:{max_steps:5}}},
      provider_ltm_settings:{group_icl_enable:false,group_message_history_enable:true,group_message_history_max_cnt:100,active_reply:{enable:false,method:'possibility_reply',possibility_reply:0.02,whitelist:cfg.groups}},
      t2i:false,log_level:'INFO',plugin_set:[],disable_builtin_commands:true,
    };
    const root=path.join(ctx.root,'ai');await mkdir(path.join(root,'data'),{recursive:true});
    await writeFile(path.join(root,'data/cmd_config.json.tmp'),JSON.stringify(value,null,2),{mode:0o600});await rename(path.join(root,'data/cmd_config.json.tmp'),path.join(root,'data/cmd_config.json'));
    await writeFile(path.join(root,'persona.txt'),cfg.systemPrompt,{mode:0o600});
    await writeFile(path.join(root,'runtime.json'),JSON.stringify({generation:ctx.generation,revision:ctx.revision,token:ctx.token,aiPort:ctx.aiPort,expectedUin:ctx.expectedUin,groups:cfg.groups,botId:ctx.botId,config:cfg}),{mode:0o600});
    return {appliedRevision:ctx.revision,restartRequired:!!(await this.host.inspect(ctx.botId,'ai'))};
  }
  async start(ctx:RuntimeContext){await this.provision(ctx);const root=path.join(ctx.root,'ai');
    const env:NodeJS.ProcessEnv={...process.env,ASTRBOT_ROOT:root,TMP: path.join(root,'tmp'),TEMP:path.join(root,'tmp'),TMPDIR:path.join(root,'tmp'),FISHBOT_MODEL_KEY:ctx.apiKey,PYTHONUNBUFFERED:'1',PYTHONIOENCODING:'utf-8'};
    delete env.MASTER_KEY;delete env.DATABASE_URL;
    await this.host.start(ctx.botId,'ai',this.python,[path.resolve('adapters/astrbot/headless.py'),this.source,root],root,env,[ctx.apiKey,ctx.token]);
  }
  async stop(botId:string){await this.host.stop(botId,'ai')}
  async restart(ctx:RuntimeContext){await this.stop(ctx.botId);await this.start(ctx)}
  async getHealth(botId:string):Promise<Health>{const p=await this.host.inspect(botId,'ai');return {alive:!!p,ready:false,...(p?{pid:p.pid,rssBytes:p.rssBytes,memoryPolicy:p.memoryPolicy}:{})}}
  async probe(ctx:RuntimeContext):Promise<Health>{const h=await this.getHealth(ctx.botId);if(!h.alive)return h;try{const r=await fetch(`http://127.0.0.1:${ctx.aiPort+1}/health`,{headers:{Authorization:`Bearer ${ctx.token}`},signal:AbortSignal.timeout(1500)});const data:any=await r.json();return {...h,ready:r.ok&&data.generation===ctx.generation&&data.revision===ctx.revision,metrics:data.metrics?.qq,detail:data.modelConfigured?'模型已配置，API可用性需实际对话验证':'尚未配置模型Key'}}catch{return h}}
}
