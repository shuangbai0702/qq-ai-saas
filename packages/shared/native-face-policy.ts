import {nativeFacePolicy,casualChatPolicy} from '../contracts/fish-persona.js';
const oldInstruction='可偶尔用一个已支持标记：[QQ_FACE:14]微笑、[QQ_FACE:4]得意、[QQ_FACE:5]流泪、[QQ_FACE:32]疑问；平台会转换为QQ表情，不能捏造其他控制标签。';
export function disableNativeQQFaces(config:Record<string,any>){
 const next=structuredClone(config);next.behavior={...next.behavior,qqFaces:false};
 let prompt=typeof next.systemPrompt==='string'?next.systemPrompt:'';
 prompt=prompt.replace(oldInstruction,'');
 if(!prompt.includes(nativeFacePolicy))prompt+='\n'+nativeFacePolicy;
 // Replace only this rollout's tagged single-line rule when its wording changes.
 if(prompt.includes('[闲聊表达]'))prompt=prompt.replace(/\[闲聊表达\][^\r\n]*/g,casualChatPolicy);
 else prompt+='\n'+casualChatPolicy;
 next.systemPrompt=prompt;return next;
}
