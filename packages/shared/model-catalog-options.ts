/** Browser-safe values; provider support must still come from metadata. */
export const reasoningEfforts=['none','minimal','low','medium','high','xhigh','max'] as const;

/** Apply a default only when API metadata confirms support; preserve explicit choices. */
export function defaultReasoningEffort(model?:{reasoningSupported?:boolean;reasoningEfforts?:readonly string[]}){
 if(model?.reasoningSupported===false)return undefined;
 return model?.reasoningEfforts?.includes('medium')||model?.reasoningSupported===true&&!model.reasoningEfforts?'medium' as const:undefined;
}
