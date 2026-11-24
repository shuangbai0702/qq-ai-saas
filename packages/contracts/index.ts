import {BotConfigSchema,ModelBaseUrlSchema,type BotConfig} from './bot-config.js';
export {BotConfigSchema,ModelBaseUrlSchema,type BotConfig} from './bot-config.js';
export type {ProcessHostLike,ProcessInspection,ProcessRecord} from './process-host.js';
export {behaviorDefaults,behaviorDefaultsVersion} from './behavior-defaults.js';
import {fishSocialPreset} from './fish-social-preset.js';
export {applyFishSocialPreset,fishSocialPreset} from './fish-social-preset.js';
import {fishPrompt,quoteReplyPolicy} from './fish-persona.js';
export {fishPrompt} from './fish-persona.js';
import { z } from 'zod';
export const LoginStates = ['created','starting','qr_ready','waiting_scan','waiting_confirm','verification_required','authenticating','online','expired','canceled','failed'] as const;
export type LoginState = typeof LoginStates[number];
export const terminalLogin = new Set<LoginState>(['online','expired','canceled','failed']);
export const ProtocolSchema=z.enum(['mock','napcat','llbot','euler']);
export const supportsQQFavorites=(protocol:string)=>protocol==='napcat'||protocol==='llbot';
export const hasManagedQQLogin=(protocol:string)=>protocol==='napcat'||protocol==='llbot';
export type ProtocolKind=z.infer<typeof ProtocolSchema>;
export interface ProtocolContext {botId:string;tenantId:string;root:string;generation:number;aiPort:number;protocolPort:number;token:string;expectedUin:string;loginIntent?:'qr'|'resume'}
export interface AiProbeContext {botId:string;generation:number;revision:number;aiPort:number;token:string}
export interface AiRuntimeContext extends ProtocolContext {revision:number;systemChangePermitId?:string;characterCardsEdit?:{target:string;permitId:string;origins:string[]};personaEdit?:{target:string;origins:string[]};toolsEdit?:{target:string;permitId:string};config:BotConfig;apiKey:string;runtimeMode?:'dedicated'|'shared';groupId?:string}
export function toProtocolContext(ctx:ProtocolContext,loginIntent?:ProtocolContext['loginIntent']):ProtocolContext{return{botId:ctx.botId,tenantId:ctx.tenantId,root:ctx.root,generation:ctx.generation,aiPort:ctx.aiPort,protocolPort:ctx.protocolPort,token:ctx.token,expectedUin:ctx.expectedUin,...(loginIntent!==undefined?{loginIntent}:ctx.loginIntent!==undefined?{loginIntent:ctx.loginIntent}:{})}}
export interface Health {alive:boolean;ready:boolean;online?:boolean;pid?:number;rssBytes?:number;metrics?:Record<string,unknown>;detail?:string;mode?:string;memoryPolicy?:unknown;loginState?:LoginState}
export interface LoginSnapshot {state:LoginState;qr?:string;uin?:string;message?:string;restartRequested?:boolean}
export interface ProtocolAdapter {
  provision(ctx:ProtocolContext):Promise<void>;beginLogin(ctx:ProtocolContext):Promise<void>;
  getLoginState(ctx:ProtocolContext):Promise<LoginSnapshot>;cancelLogin(ctx:ProtocolContext):Promise<void>;
  queryIdentity(ctx:ProtocolContext):Promise<string|null>;getCapabilities():string[];
  getHealth(ctx:ProtocolContext):Promise<Health>;stop(botId:string):Promise<void>;
}
export const personas=[{id:'dafeiyu',name:'大肥鱼',description:'嘴硬心软 · 简短自然 · 群聊伙伴',prompt:fishPrompt},{id:'helpful',name:'温和助手',description:'耐心清楚 · 准确回答',prompt:'你是一位温和、清楚、可靠的聊天伙伴。简短回答，依据真实上下文，不编造记忆。\n'+quoteReplyPolicy},{id:'custom',name:'自定义角色',description:'自由设定你的聊天伙伴',prompt:'请用自然、友好的中文交流，尊重真实信息与对话边界。\n'+quoteReplyPolicy}];
export const defaultConfig:BotConfig=BotConfigSchema.parse({personaId:'dafeiyu',systemPrompt:fishPrompt,provider:'deepseek',baseUrl:'https://api.deepseek.com/v1',model:'',trigger:'mention',groups:[],...fishSocialPreset});
