import {randomInt} from 'node:crypto';
import {z} from 'zod';
const ProcessIdentity=z.object({pid:z.number().int().positive(),created:z.number().positive(),generation:z.number().int().nonnegative(),online:z.boolean(),counted:z.boolean()});
export const EgressSchema=z.object({route:z.enum(['direct','huawei']),switches:z.number().int().min(0).max(104),failures:z.number().int().nonnegative(),pending:z.boolean(),process:ProcessIdentity.optional()});
export type QQEgress=z.infer<typeof EgressSchema>;
export function proxyPort(env:NodeJS.ProcessEnv=process.env):number|undefined{
 const value=env.NAPCAT_EGRESS_PROXY_PORT;
 if(value===undefined||value==='')return undefined;
 if(!/^[1-9]\d{0,4}$/.test(value)||Number(value)>65535)throw new Error('NAPCAT_EGRESS_PROXY_PORT 必须为本机代理端口');
 return Number(value);
}
export function initialEgress(choose=()=>randomInt(2)):QQEgress{return{route:choose()===0?'direct':'huawei',switches:0,failures:0,pending:false}}
export function nextEgress(state:QQEgress):QQEgress{
 if(!state.pending)return state;
 if(state.switches===104)throw new Error('出口切换计数已达到可精确表示上限');
 return{route:state.route==='direct'?'huawei':'direct',switches:state.switches+1,failures:0,pending:false};
}
export function observeEgress(state:QQEgress,identity:{pid:number;created:number;generation:number},online:boolean,kicked:boolean):QQEgress{
 const active=state.process;
 if(!active||active.pid!==identity.pid||active.created!==identity.created||active.generation!==identity.generation)return state;
 if(online)return{...state,process:{...active,online:true,counted:false}};
 if(!kicked||!active.online||active.counted)return state;
 const failures=state.failures+1;
 return{...state,failures,pending:failures>=2**Math.floor(state.switches/2),process:{...active,counted:true}};
}
