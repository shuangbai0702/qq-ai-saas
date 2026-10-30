import type {BotConfig} from './index.js';
// zexuanw958-svg/dafeiyu-config-pack settings/pack.json,
// commit 8c01b0361b8a4baff5be3d2bc67ed55108953086. Platform-only additions
// retain bounded collection, scoped memory and explicit proactive recipients.
export const fishSocialPreset={
 historyTurns:30,proactive:true,
 behavior:{qqProfile:true,qzone:true,typingSimulation:true,typingSpeed:15,typingMaxDelay:3,typoEnabled:true,typoProbability:0.02,pokeReply:true,pokeProbability:0.25,memorySummary:true,summaryRounds:10,enabled:true,memory:true,memoryAutoCapture:true,styleLearning:true,socialAwareness:true,
  expressionSharing:'bot' as const,memeEnabled:true,memeProbability:0.35,
  stickerCollect:true,stickerAllGroups:true,memePacks:['blue-fat-fish'] as ('blue-fat-fish')[],
  followupSeconds:120,followupReplyProbability:0.8,randomReplyProbability:0.02,
  ambientContextMessages:50,splitMessages:true,burstDebounceMs:800,sendIntervalMs:800,
  quietStart:1,quietEnd:7},
};
export function applyFishSocialPreset(config:BotConfig):BotConfig{
 return {...config,historyTurns:fishSocialPreset.historyTurns,proactive:true,
  behavior:{...config.behavior,...fishSocialPreset.behavior,memePacks:[...fishSocialPreset.behavior.memePacks]} as NonNullable<BotConfig['behavior']>};
}
