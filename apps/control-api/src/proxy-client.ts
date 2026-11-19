import {createHmac,timingSafeEqual} from 'node:crypto';
import {isIP} from 'node:net';

// The public Tunnel must overwrite Host with this marker. The API listener is
// loopback-only; local runtime calls retain their ordinary localhost Host.
export const edgeIngressHost='fishbot-edge-ingress.invalid';

// Forwarded headers alone are untrusted: the public tunnel can also be reached
// directly. Only the edge's short-lived signature supplies a client identity.
export function proxyClientIp(headers:Record<string,string|string[]|undefined>,secret:string|undefined,now=Date.now()):string|undefined{
 if(!secret)return;
 const ip=headers['x-fishbot-client-ip'],at=headers['x-fishbot-client-at'],signature=headers['x-fishbot-client-signature'];
 if(typeof ip!=='string'||!isIP(ip)||typeof at!=='string'||!/^\d{13}$/.test(at)||Math.abs(now-Number(at))>90000||typeof signature!=='string'||! /^[a-f0-9]{64}$/.test(signature))return;
 const expected=createHmac('sha256',secret).update(at+'\n'+ip).digest();
 if(timingSafeEqual(expected,Buffer.from(signature,'hex')))return ip;
}
