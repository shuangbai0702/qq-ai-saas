import {z} from 'zod';

export const biliScopes=['public.read','private.read','media.analyze','like.write','coin.write','favorite.write','watch_later.write','folder.manage','comment.write','follow.write','dynamic.write','dm.read','dm.write','notification.ack','profile.write'] as const;
const video=z.string().regex(/^BV[A-Za-z0-9]{10}$/);
export const BilibiliConfigSchema=z.object({
 enabled:z.boolean().default(false),allowAnonymous:z.boolean().default(false),scopes:z.array(z.enum(biliScopes)).max(biliScopes.length).default(['public.read']),collectionsEnabled:z.boolean().default(true),
 allowAutonomous:z.boolean().default(false),dmRecipients:z.array(z.string().regex(/^\d{1,20}$/)).max(50).default([]),
 videoWriteAllowlist:z.array(video).max(50).default([]),
 model:z.object({provider:z.enum(['none','bot','openai','gemini']).default('none'),baseUrl:z.string().max(2000).default('').refine(value=>{if(!value)return true;try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password&&!u.search&&!u.hash}catch{return false}},'模型地址必须是不含凭证和查询参数的HTTP(S)地址'),model:z.string().max(120).default(''),region:z.string().max(60).default('')}).prefault({}),
 resources:z.object({maxDownloadMiB:z.number().int().min(8).max(256).default(64),maxClipSeconds:z.number().int().min(5).max(120).default(30),cacheMiB:z.number().int().min(64).max(1024).default(256),concurrency:z.number().int().min(1).max(2).default(1)}).prefault({}),
}).strict().refine(v=>v.model.provider!=='openai'||!!(v.model.baseUrl.trim()&&v.model.model.trim()),'独立图片模型必须填写服务地址和模型ID');
export type BilibiliConfig=z.infer<typeof BilibiliConfigSchema>;
const uid=z.string().regex(/^\d{1,20}$/),limit=z.number().int().min(1).max(20).default(5),page=z.number().int().min(1).max(100).default(1);
const text=z.string().trim().min(1).max(1000);
// Only enumerated operations. Credentials, paths, arbitrary URLs and HTTP methods
// are deliberately absent from model arguments.
export const biliOperations={
 search:{scope:'public.read',write:false,args:z.object({query:z.string().trim().min(1).max(80),page,limit,fresh:z.boolean().optional().describe('确需重新查询最新结果时设为true；否则可复用两分钟内相同参数的成功结果，不绕过风控冷却')}).strict()},
 resolve_video:{scope:'public.read',write:false,args:z.object({url:z.string().max(2048).url().refine(value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&(['www.bilibili.com','bilibili.com'].includes(u.hostname)&&/^\/video\/BV[A-Za-z0-9]{10}(?:\/|$)/.test(u.pathname)||u.hostname==='b23.tv'&&/^\/[A-Za-z0-9]+\/?$/.test(u.pathname))}catch{return false}},'仅支持B站HTTPS视频链接或b23.tv短链')}).strict()},
 search_users:{scope:'public.read',write:false,args:z.object({query:z.string().trim().min(1).max(80),page,limit}).strict()},
 popular:{scope:'public.read',write:false,args:z.object({page,limit}).strict()},
 ranking:{scope:'public.read',write:false,args:z.object({rid:z.number().int().min(0).max(1000).default(0),limit}).strict()},
 video_info:{scope:'public.read',write:false,args:z.object({bvid:video}).strict()},
 video_tags:{scope:'public.read',write:false,args:z.object({bvid:video}).strict()},
 user_profile:{scope:'public.read',write:false,args:z.object({uid}).strict()},
 user_videos:{scope:'public.read',write:false,args:z.object({uid,page,limit,order:z.enum(['pubdate','click','stow']).default('pubdate')}).strict()},
 collections:{scope:'public.read',write:false,args:z.object({uid,page,limit}).strict()},
 collection_videos:{scope:'public.read',write:false,args:z.object({uid,collectionId:uid,kind:z.enum(['season','series']),page,limit}).strict()},
 dynamic_info:{scope:'private.read',write:false,args:z.object({dynamicId:uid}).strict()},
 related:{scope:'public.read',write:false,args:z.object({bvid:video,limit}).strict()},
 comments:{scope:'public.read',write:false,args:z.object({aid:uid,root:uid.optional(),page,limit}).strict()},
 transcript:{scope:'public.read',write:false,args:z.object({bvid:video,part:z.number().int().min(1).max(100).default(1),language:z.string().max(20).default('zh-CN'),offset:z.number().int().min(0).max(100000).default(0),segmentLimit:z.number().int().min(1).max(100).default(50),start:z.number().min(0).max(86400).optional(),end:z.number().min(0).max(86400).optional()}).strict().refine(v=>v.end===undefined||v.start===undefined||v.end>v.start,'字幕结束时间须晚于开始时间')},
 transcript_search:{scope:'public.read',write:false,args:z.object({bvid:video,part:z.number().int().min(1).max(100).default(1),query:z.string().trim().min(1).max(80),language:z.string().max(20).default('zh-CN'),offset:z.number().int().min(0).max(100000).default(0),limit}).strict()},
 analysis_history:{scope:'media.analyze',write:false,args:z.object({bvid:video.optional(),limit}).strict()},
 task_result:{scope:'media.analyze',write:false,args:z.object({actionId:z.string().uuid()}).strict()},
 analyze:{scope:'media.analyze',write:false,args:z.object({bvid:video,part:z.number().int().min(1).max(100).default(1),question:text,start:z.number().min(0).max(86400).default(0),seconds:z.number().min(5).max(120).default(30),frameCount:z.number().int().min(4).max(12).optional().describe('默认4；精确时间点列表存在时由列表长度决定'),timestamps:z.array(z.number().min(0).max(86400)).min(4).max(12).optional().describe('源视频秒，必须递增并处于start/seconds范围内；局部补看可用新范围再次请求'),crop:z.object({x:z.number().min(0).max(1),y:z.number().min(0).max(1),width:z.number().positive().max(1),height:z.number().positive().max(1)}).strict().refine(v=>v.x+v.width<=1&&v.y+v.height<=1,'裁切矩形须位于源画面内').optional().describe('源画面归一化ROI；x/y为左上角，width/height为范围，先裁切再按共享图片合同压缩，不放大'),audio:z.enum(['none','transcribe']).default('none')}).strict().refine(v=>!v.timestamps||((v.frameCount===undefined||v.frameCount===v.timestamps.length)&&v.timestamps.every((t,i)=>t>=v.start&&t<v.start+v.seconds&&(i===0||t>v.timestamps![i-1]))),'时间点数量须匹配frameCount、严格递增且位于请求范围内')},
 account_info:{scope:'private.read',write:false,args:z.object({}).strict()},
 feed:{scope:'private.read',write:false,args:z.object({kind:z.enum(['recommend','following']).default('following'),offset:uid.optional(),limit}).strict().refine(v=>v.kind==='following'||!v.offset,'推荐流不支持关注流游标')},
 library:{scope:'private.read',write:false,args:z.object({kind:z.enum(['history','watch_later','folders','favorites']),folderId:uid.optional(),page,limit,cursor:z.object({max:uid,viewAt:z.number().int().nonnegative(),business:z.enum(['archive','pgc','live','article-list','article']).default('archive')}).strict().optional()}).strict().refine(v=>v.kind==='history'||!v.cursor,'历史游标只能用于历史记录').refine(v=>v.kind!=='history'||v.page===1,'历史记录使用返回的cursor翻页，不使用page')},
 notifications:{scope:'private.read',write:false,args:z.object({kind:z.literal('unread').default('unread')}).strict()},
 notification_items:{scope:'notification.ack',write:true,args:z.object({kind:z.enum(['replies','mentions','likes']),cursor:z.object({id:uid,time:z.number().int().nonnegative()}).strict().optional(),limit}).strict()},
 dm_read:{scope:'dm.read',write:false,args:z.object({uid:uid.optional(),beforeSeq:uid.optional(),limit}).strict().refine(v=>!v.beforeSeq||!!v.uid,'消息游标必须指定真实对话UID')},
 like:{scope:'like.write',write:true,args:z.object({bvid:video,value:z.boolean()}).strict()},
 coin:{scope:'coin.write',write:true,args:z.object({bvid:video,count:z.number().int().min(1).max(2)}).strict()},
 video_state:{scope:'private.read',write:false,args:z.object({bvid:video}).strict()},
 folder_info:{scope:'private.read',write:false,args:z.object({folderId:uid}).strict()},
 watch_later:{scope:'watch_later.write',write:true,args:z.object({bvid:video,value:z.boolean()}).strict()},
 folder_create:{scope:'folder.manage',write:true,args:z.object({title:z.string().trim().min(1).max(40),intro:z.string().max(500).default(''),private:z.boolean().default(true)}).strict()},
 folder_edit:{scope:'folder.manage',write:true,args:z.object({folderId:uid,title:z.string().trim().min(1).max(40),intro:z.string().max(500),private:z.boolean()}).strict()},
 folder_delete:{scope:'folder.manage',write:true,args:z.object({folderId:uid}).strict()},
 favorite:{scope:'favorite.write',write:true,args:z.object({bvid:video,folderId:uid,value:z.boolean()}).strict()},
 comment:{scope:'comment.write',write:true,args:z.object({aid:uid,bvid:video.optional(),text,root:uid.optional(),parent:uid.optional()}).strict().refine(v=>!!v.root===!!v.parent,'root与parent必须同时提供')},
 delete_comment:{scope:'comment.write',write:true,args:z.object({aid:uid,bvid:video.optional(),rpid:uid}).strict()},
 follow:{scope:'follow.write',write:true,args:z.object({uid,value:z.boolean()}).strict()},
 dynamic_post:{scope:'dynamic.write',write:true,args:z.object({text}).strict()},
 dynamic_delete:{scope:'dynamic.write',write:true,args:z.object({dynamicId:uid}).strict()},
 dm_send:{scope:'dm.write',write:true,args:z.object({uid,text}).strict()},
 profile_signature:{scope:'profile.write',write:true,args:z.object({text:z.string().max(70)}).strict()},
} as const;
export type BiliOperation=keyof typeof biliOperations;
/** Local evidence needs no platform login. Network work never silently drops
 * an existing account to anonymous, even while credentials are being renewed. */
export function biliAccessAllowed(config:Partial<Pick<BilibiliConfig,'allowAnonymous'>>,name:string,account?:{state?:string;refresh_state?:string}|null){
 if(['task_result','analysis_history'].includes(name))return true;
 if(account)return account.state==='ready'&&(!account.refresh_state||['idle','confirm_pending'].includes(account.refresh_state));
 const op=biliOperations[name as BiliOperation];
 return config.allowAnonymous===true&&!!op&&!op.write&&['public.read','media.analyze'].includes(op.scope);
}
export function biliOperationAllowed(config:Pick<BilibiliConfig,'enabled'|'scopes'> & Partial<Pick<BilibiliConfig,'collectionsEnabled'>>,name:string){
 const op=biliOperations[name as BiliOperation];
 return !!op&&config.enabled&&config.scopes.includes(op.scope)&&(!['collections','collection_videos'].includes(name)||config.collectionsEnabled!==false);
}
export const BiliCallSchema=z.object({operation:z.enum(Object.keys(biliOperations) as [BiliOperation,...BiliOperation[]]),args:z.record(z.string(),z.unknown()),workId:z.string().min(1).max(200),stepId:z.string().min(1).max(160),authority:z.enum(['foreground','autonomous']),senderId:uid.optional(),continuation:z.object({actionId:z.string().uuid(),owner:z.string().uuid()}).strict().optional(),epoch:z.number().int().positive().optional(),origin:z.object({scope:z.string().regex(/^(private|group):[0-9]{1,20}$/),eventId:z.string().min(1).max(100),objective:z.string().max(1000)}).strict().optional(),deadlineAt:z.number().int().positive()}).strict();
export type BiliCall=z.infer<typeof BiliCallSchema>;
export type ToolResult={schemaVersion:'fishbot.tool-result.v1';actionId:string;execution:'completed'|'failed'|'canceled'|'pending';receipt:{status:'confirmed'|'rejected'|'partial'|'unknown'|'not_applicable';platformObjectId?:string;meaning?:string};data?:unknown;evidence?:unknown;error?:{code:string;retry:'never'|'safe_read'|'reconcile'|'after_auth';userMessage:string;source?:{endpoint:string;method:'GET'|'POST';httpStatus?:number;platformCode?:number}};usage:{modelRequests:number;inputBytes?:number;outputBytes?:number;costStatus:'unknown'|'actual';tokens?:unknown;providerUsage?:{prompt_tokens?:number;completion_tokens?:number;total_tokens?:number;[key:string]:unknown}|null};versions:{adapter:string}};
