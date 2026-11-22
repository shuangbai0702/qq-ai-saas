import {z} from 'zod';
export const CharacterCardSchema=z.object({
 id:z.string().regex(/^[A-Za-z0-9_-]{1,80}$/),
 name:z.string().refine(v=>v.length<=80,'名称最多80字符').refine(v=>!!v.trim(),'名称不能为空白'),
 kind:z.enum(['meme','speech','world','custom']),
 content:z.string().refine(v=>v.length<=4000,'正文最多4000字符').refine(v=>!!v.trim(),'正文不能为空白'),
 enabled:z.boolean(),
}).strict();
export const CharacterCardsSchema=z.array(CharacterCardSchema).max(5).refine(cards=>new Set(cards.map(c=>c.id)).size===cards.length,'角色卡 ID 不能重复');
export type CharacterCard=z.infer<typeof CharacterCardSchema>;
// Fixed tuple encoding is shared with the Python owner. Lengths use UTF-16,
// matching existing JS schemas and HTML maxlength, without altering content.
export const cardMessages=(cards:CharacterCard[])=>cards.filter(c=>c.enabled).map(c=>({role:'system',content:`[FishBot 角色卡] ${c.name}\n${c.content}`}));
export const cardsIdentity=(cards:CharacterCard[])=>JSON.stringify(cards.map(c=>[c.id,c.name,c.kind,c.content,c.enabled]));
