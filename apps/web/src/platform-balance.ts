import {useLiveResource} from './hooks';
export type Balance={enabled:boolean;available:number;granted:number;spent:number;reserved:number;expired?:number;campaign?:{id:string|null;claimedAt:string|null;expiresAt:string|null;granted:number;spent:number;reserved:number;available:number}};
export function usePlatformBalance(){return useLiveResource<Balance>('/platform-model/balance',15000)}
export const tokenCount=(n:number)=>n.toLocaleString('zh-CN');
