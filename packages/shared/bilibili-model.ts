/** Configuration evidence only; actual image support requires a provider receipt. */
export function bilibiliModelConfigured(settings:any,botModel?:any,hasBotKey=false):boolean {
 const model=settings?.config?.model;
 if(model?.provider==='openai')return !!(settings.model_secret&&model.baseUrl?.trim()&&model.model?.trim());
 return model?.provider==='bot'&&!!(hasBotKey&&botModel?.baseUrl?.trim()&&botModel?.model?.trim());
}
export const bilibiliModelRequired='没有可用的图片模型配置或Key；请配置B站图片模型';
