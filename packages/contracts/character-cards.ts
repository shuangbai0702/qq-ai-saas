import {z} from 'zod';
export const CharacterCardSchema=z.object({
 id:z.string().regex(/^[A-Za-z0-9_-]{1,80}$/),
 name:z.string().refine(v=>v.length<=80,'名称最多80字符').refine(v=>!!v.trim(),'名称不能为空白').refine(v=>!/[\u0000\uD800-\uDFFF]/u.test(v),'名称含无法保存的字符'),
 kind:z.enum(['meme','speech','world','custom']),
 content:z.string().refine(v=>v.length<=4000,'正文最多4000字符').refine(v=>!!v.trim(),'正文不能为空白').refine(v=>!/[\u0000\uD800-\uDFFF]/u.test(v),'正文含无法保存的字符'),
 enabled:z.boolean(),
}).strict();
export const CharacterCardsSchema=z.array(CharacterCardSchema).max(5).refine(cards=>new Set(cards.map(c=>c.id)).size===cards.length,'角色卡 ID 不能重复');
export type CharacterCard=z.infer<typeof CharacterCardSchema>;
export const libraryCardKinds={speech:'表达',meme:'知识',world:'背景'} as const;
export const LibraryCardKindSchema=z.enum(['speech','meme','world']);
export const characterCardCover={sourceMaxBytes:8*1024*1024,maxEdge:1024,maxBytes:128*1024,mime:'image/webp',fallbackMime:'image/jpeg',dataPrefix:'data:image/webp;base64,'} as const;
export const characterCardCoverDataMaxLength=characterCardCover.dataPrefix.length+4*Math.ceil(characterCardCover.maxBytes/3);
export const LibraryCardInputSchema=CharacterCardSchema.pick({name:true,content:true}).extend({
 kind:LibraryCardKindSchema,
 description:z.string().refine(v=>v.length<=200,'描述最多200字符').refine(v=>!/[\u0000\uD800-\uDFFF]/u.test(v),'描述含无法保存的字符').default(''),
 cover:z.string().max(characterCardCoverDataMaxLength,'封面处理失败，请重新选择图片').nullable().optional(),
}).strict();
export type LibraryCardInput=z.infer<typeof LibraryCardInputSchema>;
export interface LibraryCard {id:string;name:string;kind:keyof typeof libraryCardKinds;description:string;revision:number;authorName:string;editable:boolean;coverUrl:string|null;content?:string}
// Fixed tuple encoding is shared with the Python owner. Lengths use UTF-16,
// matching existing JS schemas and HTML maxlength, without altering content.
export const cardMessages=(cards:CharacterCard[])=>cards.filter(c=>c.enabled).map(c=>({role:'system',content:`[FishBot 角色卡] ${c.name}\n${c.content}`}));
export const cardsIdentity=(cards:CharacterCard[])=>JSON.stringify(cards.map(c=>[c.id,c.name,c.kind,c.content,c.enabled]));
