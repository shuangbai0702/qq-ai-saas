import {createHmac, timingSafeEqual} from 'node:crypto';
import type {BotConfig} from '../contracts/index.js';

export const platformModelId = 'platform';
export const platformModelPath = '/v1/platform-model';
export const platformUpstream = {baseUrl:'https://nai.lunarbyte.pw/v1', model:'gpt-5.6-luna'};

type ModelBot = {id:string; tenant_id:string; generation:number; revision:number};
function signature(key:Buffer, bot:ModelBot){
 return createHmac('sha256',key).update(JSON.stringify(['platform-model-v1',bot.id,bot.tenant_id,bot.generation,bot.revision])).digest('base64url');
}

export function platformModelCredential(key:Buffer, bot:ModelBot){
 return `fish-platform.${bot.id}.${signature(key,bot)}`;
}

export function verifyPlatformModelCredential(key:Buffer, bot:ModelBot, supplied:string){
 const expected=Buffer.from(platformModelCredential(key,bot));
 const actual=Buffer.from(supplied);
 return actual.length===expected.length&&timingSafeEqual(actual,expected);
}

/** Keep the user's saved custom connection in the config revision. Resolve only for execution. */
export function resolvePlatformModel(config:BotConfig, key:Buffer, bot:ModelBot){
 const origin=new URL(process.env.FISHBOT_PLATFORM_MODEL_RUNTIME_ORIGIN||process.env.APP_ORIGIN||'http://127.0.0.1:8787');
 if(origin.protocol!=='https:'&&!(origin.protocol==='http:'&&origin.hostname==='127.0.0.1'))throw new Error('平台模型运行地址无效');
 const baseUrl=origin.origin+platformModelPath;
 return {config:{...config,baseUrl,model:platformModelId,auxiliaryModel:undefined,delegationModel:undefined,summaryModel:undefined,favoriteModel:undefined,reasoningEffort:undefined,behavior:{...config.behavior,embeddingBaseUrl:'',embeddingModel:''}},apiKey:platformModelCredential(key,bot)};
}
