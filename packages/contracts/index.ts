import {fishSocialPreset} from './fish-social-preset.js';
export {applyFishSocialPreset,fishSocialPreset} from './fish-social-preset.js';
import {fishPrompt} from './fish-persona.js';
export {fishPrompt} from './fish-persona.js';
import { z } from 'zod';
export const LoginStates = ['created','starting','qr_ready','waiting_scan','waiting_confirm','verification_required','authenticating','online','expired','canceled','failed'] as const;
export type LoginState = typeof LoginStates[number];
export const terminalLogin = new Set<LoginState>(['online','expired','canceled','failed']);
export const BotConfigSchema = z.object({
  personaId:z.string().max(100).default('dafeiyu'), systemPrompt:z.string().min(1).max(20000),
  provider:z.enum(['deepseek','openai','custom']).default('deepseek'),
  baseUrl:z.url().max(2000).refine(x=>{const u=new URL(x);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password&&!u.search&&!u.hash},'请填写不含账号、查询参数的 HTTP(S) 地址'),
  model:z.string().min(1).max(150), trigger:z.enum(['mention','all']).default('mention'),
  groups:z.array(z.string().regex(/^\d{4,20}$/)).max(100).default([]),
  proactive:z.boolean().default(false), historyTurns:z.number().int().min(1).max(100).default(20),
  behavior:z.object({autoSignature:z.boolean().default(false),dailyAutoSignatureLimit:z.number().int().min(0).max(2).default(2),proactiveModelSchedule:z.boolean().default(false),dailyPlanLimit:z.number().int().min(0).max(20).default(6),hourlyInterjectionLimit:z.number().int().min(0).max(20).default(20),attentionEnabled:z.boolean().default(false),fatigueEnabled:z.boolean().default(true),fatigueWindowSeconds:z.number().int().min(60).max(1800).default(300),fatigueMaxReplies:z.number().int().min(1).max(20).default(4),memeSemantic:z.boolean().default(false),embeddingBaseUrl:z.union([z.literal(''),z.url().max(2000).refine(x=>{const u=new URL(x);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password&&!u.search&&!u.hash},'请填写有效模型地址')]).default(''),typingSimulation:z.boolean().default(false),typingSpeed:z.number().min(5).max(60).default(15),typingMaxDelay:z.number().min(0.25).max(5).default(3),typoEnabled:z.boolean().default(false),typoProbability:z.number().min(0).max(0.05).default(0.02),pokeReply:z.boolean().default(false),pokeProbability:z.number().min(0).max(1).default(0.25),memorySummary:z.boolean().default(false),memorySemantic:z.boolean().default(false),embeddingModel:z.string().trim().max(150).default(''),dailyEmbeddingLimit:z.number().int().min(0).max(1000).default(100),summaryRounds:z.number().int().min(5).max(50).default(10),dailySummaryLimit:z.number().int().min(0).max(50).default(12),qqProfile:z.boolean().default(false),qzone:z.boolean().default(false),qzoneVisibility:z.enum(['friends','self','public']).default('friends'),proactiveTargets:z.array(z.string().regex(/^(private|group):\d{5,20}$/)).max(50).default([]),silenceSeconds:z.number().int().min(60).max(86400).default(1800),proactiveIntervalSeconds:z.number().int().min(60).max(604800).default(3600),proactiveChance:z.number().min(0).max(1).default(0.25),proactiveRandomInterval:z.boolean().default(false),proactiveMinMinutes:z.number().int().min(30).max(600).default(30),proactiveMaxMinutes:z.number().int().min(30).max(600).default(600),maxUnanswered:z.number().int().min(1).max(4).default(1),followupReplyProbability:z.number().min(0).max(1).default(1),ambientContextMessages:z.number().int().min(1).max(50).default(12),followupSeconds:z.number().int().min(0).max(300).default(0),burstDebounceMs:z.number().int().min(0).max(3000).default(0),splitMessages:z.boolean().default(false),sendIntervalMs:z.number().int().min(300).max(3000).default(800),enabled:z.boolean().default(true),memory:z.boolean().default(true),memoryAutoCapture:z.boolean().default(false),qqFaces:z.boolean().default(false),socialAwareness:z.boolean().optional(),styleLearning:z.boolean().default(false),expressionSharing:z.enum(['local','selected','bot']).default('local'),expressionShareGroups:z.array(z.string().regex(/^\d{4,20}$/)).max(50).default([]),memePacks:z.array(z.enum(['blue-fat-fish'])).max(1).default(['blue-fat-fish']),memeProbability:z.number().min(0).max(1).default(0.7),stickerAllGroups:z.boolean().default(false),memeEnabled:z.boolean().default(true),galleryEnabled:z.boolean().default(false),stickerCollect:z.boolean().default(false),stickerGroups:z.array(z.string().regex(/^\d{4,20}$/)).max(50).default([]),dailyStickerCollectLimit:z.number().int().min(0).max(30).default(10),memeCooldownSeconds:z.number().int().min(20).max(3600).default(20),dailyMemeLimit:z.number().int().min(0).max(50).default(50),cooldownSeconds:z.number().min(0).max(3600).default(0),randomReplyProbability:z.number().min(0).max(0.2).default(0.03),quietStart:z.number().int().min(0).max(23).default(23),quietEnd:z.number().int().min(0).max(23).default(8),dailyProactiveLimit:z.number().int().min(0).max(20).default(3),maxReplyChars:z.number().int().min(80).max(2000).default(500),ownerUin:z.string().regex(/^\d{5,20}$|^$/).default('')}).refine(b=>!b.proactiveRandomInterval||b.proactiveMinMinutes<=b.proactiveMaxMinutes,{message:'随机等待最少分钟不能大于最多分钟',path:['proactiveMaxMinutes']}).optional(),
});
export type BotConfig = z.infer<typeof BotConfigSchema>;
export const ProtocolSchema=z.enum(['mock','napcat','euler']);
export type ProtocolKind=z.infer<typeof ProtocolSchema>;
export interface RuntimeContext {botId:string;tenantId:string;root:string;generation:number;revision:number;aiPort:number;protocolPort:number;token:string;config:BotConfig;apiKey:string;expectedUin:string;runtimeMode?:'dedicated'|'shared';groupId?:string;loginIntent?:'qr'|'resume'}
export interface Health {alive:boolean;ready:boolean;online?:boolean;pid?:number;rssBytes?:number;metrics?:Record<string,unknown>;detail?:string;mode?:string;memoryPolicy?:unknown;loginState?:LoginState}
export interface LoginSnapshot {state:LoginState;qr?:string;uin?:string;message?:string}
export interface AiAdapter {
  provision(ctx:RuntimeContext):Promise<void>;start(ctx:RuntimeContext):Promise<void>;stop(botId:string):Promise<void>;
  restart(ctx:RuntimeContext):Promise<void>;validateConfig(config:unknown):Promise<void>;
  applyConfig(ctx:RuntimeContext):Promise<{appliedRevision:number;restartRequired:boolean}>;
  getHealth(botId:string):Promise<Health>;
}
export interface ProtocolAdapter {
  provision(ctx:RuntimeContext):Promise<void>;beginLogin(ctx:RuntimeContext):Promise<void>;
  getLoginState(ctx:RuntimeContext):Promise<LoginSnapshot>;cancelLogin(ctx:RuntimeContext):Promise<void>;
  queryIdentity(ctx:RuntimeContext):Promise<string|null>;getCapabilities():string[];
  getHealth(ctx:RuntimeContext):Promise<Health>;stop(botId:string):Promise<void>;exportSessionRef(ctx:RuntimeContext):Promise<string>;
}
export const personas=[{id:'dafeiyu',name:'大肥鱼',description:'嘴硬心软 · 简短自然 · 群聊伙伴',prompt:fishPrompt},{id:'helpful',name:'温和助手',description:'耐心清楚 · 准确回答',prompt:'你是一位温和、清楚、可靠的聊天伙伴。简短回答，依据真实上下文，不编造记忆。'},{id:'custom',name:'自定义角色',description:'自由设定你的聊天伙伴',prompt:'请用自然、友好的中文交流，尊重真实信息与对话边界。'}];
export const defaultConfig:BotConfig=BotConfigSchema.parse({personaId:'dafeiyu',systemPrompt:fishPrompt,provider:'deepseek',baseUrl:'https://api.deepseek.com/v1',model:'deepseek-chat',trigger:'mention',groups:[],...fishSocialPreset});
