/** Reproducible patch for ONE reviewed distribution hash, never a permissive runtime regex patch. */
import {createHash} from 'node:crypto';
export const LLBOT_SOURCE_SHA='f330a511b2c37933c7e284de6b70b2775ac72265a5ae60b6d0086fc3f26bcb1c';
function once(source,old,value){if(source.split(old).length!==2)throw Error('LLBot 8.3.2 patch anchor mismatch');return source.replace(old,value)}
export function patchRuntime(bytes){
 if(createHash('sha256').update(bytes).digest('hex')!==LLBOT_SOURCE_SHA)throw Error('LLBot distribution hash does not match reviewed 8.3.2 test baseline');
 let source=bytes.toString('utf8');
 source='import {installActions} from "./fishbot-actions.mjs";\nimport {hardenPmhq,hardenReverse} from "./fishbot-lifecycle.mjs";\nimport {readFileSync as fishRead} from "node:fs";\nconst fishManifest=JSON.parse(fishRead(process.env.FISHBOT_LLBOT_MANIFEST,"utf8"));\n'+source;
 source=once(source,'\treturn actionMap;','\tinstallActions(actionMap, adapter.ctx, selfInfo, fishManifest);\n\treturn actionMap;');
 // Remove an unrelated location clear from nickname/signature-only writes.
 const location=/\n\s*bytesProperties\.push\(\{\s*key: 20032,\s*value: Buffer\.alloc\(12\)\s*\}\);/g;
 if([...source.matchAll(location)].length!==1)throw Error('LLBot profile patch anchor mismatch');source=source.replace(location,'');
 // Respect the port returned by the QQ upload service.
 source=once(source,'server.push(`${uint32ToIPV4Addr(ip)}:15000`);','server.push(`${uint32ToIPV4Addr(ip)}:${ports[index]}`);');
 source=once(source,'onLoad().catch((e) => getLogger("main").error(e));',`hardenPmhq(PmhqQQProtocol, globalThis.WebSocket, selfInfo, {decode:deepConvertMap,encode:deepStringifyMap});
hardenReverse(OB11WebSocketReverse, WebSocket$1, {
 headers: config => ({'X-Self-ID':selfInfo.uin, Authorization:'Bearer '+config.token, 'x-client-role':'Universal'}),
 event: () => new OB11LifeCycleEvent(LifeCycleSubType.CONNECT),
 heartbeat: config => new OB11HeartbeatEvent(selfInfo.online,true,config.heartInterval)
});
onLoad().catch(() => { console.error('LLBot startup failed'); process.exit(1); });`);
 source=once(source,'\t\t\tloadPluginAfterLogin();','\t\t\tif (fishManifest.expectedUin && selfInfo.uin !== fishManifest.expectedUin) process.exit(78);\n\t\t\tloadPluginAfterLogin();');
 // Internal login probes also have a finite deadline.
 source=once(source,'fetch(`${this.httpUrl}health`);','fetch(`${this.httpUrl}health`, {signal:AbortSignal.timeout(4000)});');
 return source.replace('//# sourceMappingURL=llbot.js.map','// Patched distribution: original source map intentionally not advertised.');
}
