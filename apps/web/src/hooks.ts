import {useCallback,useEffect,useRef,useState} from 'react';
import {api} from './api';

// A request belongs to this mounted page. A new refresh invalidates the old reply.
export function useLiveResource<T=any>(url:string,interval=5000){
 const [result,setResult]=useState<{url:string;data:T}>(),[failure,setFailure]=useState<{url:string;message:string}>();
 const activeUrl=useRef(url);activeUrl.current=url;
 const owner=useRef<AbortController|null>(null),sequence=useRef(0),request=useRef<AbortController|null>(null);
 const refresh=useCallback(async()=>{
  const page=owner.current;if(!page||page.signal.aborted||activeUrl.current!==url)return;
  request.current?.abort();const controller=new AbortController();request.current=controller;const order=++sequence.current;
  const current=()=>activeUrl.current===url&&!page.signal.aborted&&!controller.signal.aborted&&order===sequence.current;
  try{const value=await api(url,undefined,undefined,controller.signal);if(current()){setResult({url,data:value});setFailure(undefined);return value as T}}
  catch(e){if(current())setFailure({url,message:(e as Error).message})}
 },[url]);
 useEffect(()=>{const page=new AbortController();owner.current=page;setResult(undefined);setFailure(undefined);let timer:ReturnType<typeof setTimeout>;const tick=async()=>{await refresh();if(!page.signal.aborted&&interval>0)timer=setTimeout(tick,interval)};void tick();return()=>{page.abort();request.current?.abort();sequence.current++;clearTimeout(timer)}},[refresh,interval]);
 return {data:result?.url===url?result.data:undefined,error:failure?.url===url?failure.message:'',refresh};
}

export function useLifetime(){const ref=useRef<AbortController|null>(null);useEffect(()=>{const controller=new AbortController();ref.current=controller;return()=>controller.abort()},[]);return ref}
