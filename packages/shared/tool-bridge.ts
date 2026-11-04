import {createHmac,timingSafeEqual} from 'node:crypto';
export function bridgeToken(key:Buffer,bot:string,generation:number,revision:number){return createHmac('sha256',key).update(`fishbot.internal-tools.v1:${bot}:${generation}:${revision}`).digest('hex')}
export function verifyBridge(key:Buffer,bot:any,token:string){const expected=bridgeToken(key,bot.id,bot.generation,bot.revision);return /^[a-f0-9]{64}$/.test(token)&&timingSafeEqual(Buffer.from(expected),Buffer.from(token))}
