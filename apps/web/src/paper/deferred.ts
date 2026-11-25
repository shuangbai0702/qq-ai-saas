import {useEffect} from 'react';
let started=false;

// Optional painting starts after a real screen mounts, never during auth or route download.
export function usePaperDecoration(){
 useEffect(()=>{
  if(started)return;
  let active=true;
  const timer=setTimeout(()=>{void import('./start').then(({startPaper})=>{
   if(active&&!started){startPaper();started=true}
  }).catch(()=>{/* Business controls remain usable if decoration is unavailable. */})},2000);
  return()=>{active=false;clearTimeout(timer)};
 },[]);
}
