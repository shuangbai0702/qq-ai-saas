import {BiliError} from '../bilibili/web-adapter.js';

/** ASR timestamps are clip-relative estimates, never speaker/sample alignment. */
export function asrSegments(value:unknown,duration:number):{start:number;end:number;text:string}[]{
 const rows=(value as any)?.segments;
 if(!Array.isArray(rows)||rows.length>1000)throw new BiliError('asr_invalid','ASR未返回可核实的片段相对时间轴');
 let lastEnd=0;
 for(const row of rows){
  if(!row||typeof row.text!=='string'||!row.text.trim()||row.text.length>2000||!Number.isFinite(row.start)||!Number.isFinite(row.end)||row.start<lastEnd||row.end<=row.start||row.end>duration+0.1)throw new BiliError('asr_invalid','ASR未返回可核实的有序片段相对时间轴');
  lastEnd=row.end;
 }
 return rows.map(row=>({start:row.start,end:Math.min(row.end,duration),text:row.text}));
}
