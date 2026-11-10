import {z} from 'zod';

export const AutonomyConfigSchema=z.object({
  mode:z.enum(['passive','active']).default('passive'),
  rollout:z.enum(['shadow','live']).default('live'),
  activityEnabled:z.boolean().default(true),
  activityIntervalMinutes:z.number().int().min(5).max(1440).default(30),
  activityModelCalls:z.number().int().min(1).max(16).default(4),
  activityToolCalls:z.number().int().min(0).max(64).default(8),
  activityTokenBudget:z.number().int().min(4000).max(2000000).default(128000),
  activityExternalActions:z.number().int().min(0).max(4).default(1),
  fatigueEnabled:z.boolean().default(true),
  fatigueWindowMinutes:z.number().int().min(10).max(1440).default(60),
  fatigueRecoveryMinutes:z.number().int().min(5).max(240).default(30),
  fatigueCapacityMinutes:z.number().int().min(1).max(240).default(20),
  initiativeAfterIdleMinutes:z.number().int().min(5).max(1440).default(30),
  cycleModelCalls:z.number().int().min(2).max(64).default(24),
  cycleToolCalls:z.number().int().min(1).max(128).default(32),
  cycleExternalActions:z.number().int().min(0).max(4).describe('逻辑对外行动次数；同一聊天行动的1–4条投递共计一次，每条仍独立记录回执').default(2),
  cycleTokenBudget:z.number().int().min(4000).max(2000000).default(500000),
  dailyTokenBudget:z.number().int().min(0).max(100000000).refine(v=>v===0||v>=4000,'每日上限为0表示不限制，否则至少4000').default(0),
  cycleSeconds:z.number().int().min(30).max(180).default(90),
  minSleepSeconds:z.number().int().min(60).max(86400).default(300),
  maxSleepSeconds:z.number().int().min(900).max(604800).default(86400),
}).strict().superRefine((v,ctx)=>{
 if(v.maxSleepSeconds<v.minSleepSeconds)ctx.addIssue({code:'custom',path:['maxSleepSeconds'],message:'最长休眠不能短于最短休眠'});
 if(v.dailyTokenBudget!==0&&v.dailyTokenBudget<v.cycleTokenBudget)ctx.addIssue({code:'custom',path:['dailyTokenBudget'],message:'每日预算不能小于单周期预算'});
});
export type AutonomyConfig=z.infer<typeof AutonomyConfigSchema>;
export const defaultAutonomyConfig=AutonomyConfigSchema.parse({});

// Runtime-produced provenance, never model-supplied identity or permissions.
export const TaskHandoffSchema=z.object({
 id:z.string().uuid(),tenantId:z.string().min(1).max(100),botId:z.string().min(1).max(100),
 generation:z.number().int().nonnegative(),revision:z.number().int().nonnegative(),epoch:z.number().int().positive(),
 eventId:z.string().min(1).max(100),scope:z.string().regex(/^(self|(private|group):[0-9]{5,20})$/),senderId:z.string().regex(/^(0|[0-9]{5,20})$/),
 at:z.number().int().positive(),objective:z.string().min(1).max(1000),delaySeconds:z.number().int().min(0).max(604800),
 kind:z.enum(['message','research']).optional(),origin:z.literal('model').optional(),parentActionId:z.string().uuid().optional(),
 agreement:z.object({id:z.string().regex(/^[a-f0-9]{24}$/),revision:z.number().int().positive()}).strict().optional(),
}).strict().superRefine((v,ctx)=>{
 if((v.scope==='self'||v.senderId==='0'||v.parentActionId)&&!(v.origin==='model'&&v.parentActionId&&v.senderId==='0'))ctx.addIssue({code:'custom',path:['scope'],message:'内部任务来源需要真实父动作与服务身份'});
 if(v.parentActionId&&v.eventId!==v.parentActionId)ctx.addIssue({code:'custom',path:['eventId'],message:'内部来源必须绑定父动作'});
 if(v.kind==='message'&&v.scope==='self')ctx.addIssue({code:'custom',path:['scope'],message:'消息任务需要真实目的会话'});
});

// The coordinator constructs one action per due handoff goal; every action,
// whatever produced it, must satisfy this same contract. evidenceRefs stay
// reserved: the platform attaches verified evidenceSources itself, and no
// planning model exists that could cite unverified refs.
export const ActionIntentSchema=z.object({goalId:z.string().uuid().optional(),kind:z.enum(['research','message','profile_signature','qzone_publish']),target:z.string().regex(/^(?:|private:[0-9]{5,20}|group:[0-9]{4,20})$/).describe('message 必须原样选择本轮 contacts[].scope，保留 private: 或 group: 前缀；其他任务留空。').default(''),objective:z.string().min(1).max(1000),
 evidenceRefs:z.array(z.string().min(1).max(120)).max(8).optional().describe('可选；最多8项，每项引用本轮已提供记录的稳定ref（如本轮recentForeground的eventId）。引用只说明内容归属，不授予任何跨会话发送或许可；伪造、过期或本轮未提供的引用会被整体拒绝。省略表示无引用。')}).strict().superRefine((value,ctx)=>{
 if(value.kind==='message'&&!value.target)ctx.addIssue({code:'custom',path:['target'],message:'聊天动作必须提供本轮允许的完整会话 scope'});
});

export interface InternalWork {
 id:string;tenantId:string;botId:string;epoch:number;generation:number;revision:number;
 cycleId:string;principal:'autonomous-service';
 feedbackVersion:number;
 expiresAt:number;budget:{modelCalls:number;toolCalls:number;tokens:number;seconds:number;externalActions:number};
}

export interface TaskFeedback {
 actionUnitVersion?:2;
 status:'completed'|'blocked'|'unknown'|'partial'|'canceled'|'budget_exhausted';
 summary:string;evidence:Array<{source:string;reference:string}>;
 response?:string; // Conversation content; delivery certainty comes from effects.
 evidenceLimits?:string[];
 effects:Array<{id:string;logicalActionId?:string;status:string;receipt?:string}>;
 unfinished:string[];
}
