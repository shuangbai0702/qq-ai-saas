import {behaviorDefaults} from './behavior-defaults.js';
import type {BotConfig} from './index.js';
// zexuanw958-svg/dafeiyu-config-pack settings/pack.json,
// commit 8c01b0361b8a4baff5be3d2bc67ed55108953086. Platform-only additions
// retain bounded collection, scoped memory and explicit proactive recipients.
export const fishSocialPreset={
 historyTurns:30,proactive:true,
 behavior:behaviorDefaults,
};
const presetFields=['groupParticipationEnabled', 'groupParticipationIntervalMinutes', 'groupParticipationStyle', 'groupDecisionTimeoutSeconds', 'ownerContactEnabled', 'ownerContactIntervalMinutes', 'ownerContactIdleMinutes', 'ownerContactDailyChecks', 'dailyPlanLimit', 'dailyProactiveLimit', 'qqProfile', 'qzone', 'autoSignature', 'typingSimulation', 'typingSpeed', 'typingMaxDelay', 'typoEnabled', 'typoProbability', 'pokeReply', 'pokeProbability', 'memorySummary', 'summaryRounds', 'enabled', 'memory', 'memoryAutoCapture', 'styleLearning', 'socialAwareness', 'expressionSharing', 'memeEnabled', 'stickerCollect', 'stickerAllGroups', 'memePacks', 'memeCooldownSeconds', 'followupSeconds', 'followupReplyProbability', 'randomReplyProbability', 'hourlyInterjectionLimit', 'naturalConversation', 'attentionEnabled', 'proactiveModelSchedule', 'autonomousGroupContact', 'selfDevelopment', 'memorySemantic', 'fatigueEnabled', 'fatigueWindowSeconds', 'fatigueMaxReplies', 'ambientContextMessages', 'splitMessages', 'burstDebounceMs', 'sendIntervalMs', 'quietStart', 'quietEnd'] as const;
export function applyFishSocialPreset(config:BotConfig):BotConfig{
 const overrides=Object.fromEntries(presetFields.map(key=>[key,behaviorDefaults[key]]));
 return {...config,historyTurns:fishSocialPreset.historyTurns,proactive:true,
  behavior:{...config.behavior,...overrides,
   dailyProactiveLimit:config.behavior?.dailyProactiveLimit??fishSocialPreset.behavior.dailyProactiveLimit,
   memePacks:[...fishSocialPreset.behavior.memePacks]} as NonNullable<BotConfig['behavior']>};
}
