// Health reports expire; a saved online flag is not proof of a live QQ.
export const HEALTH_MAX_AGE_MS=90000,HEALTH_FUTURE_SKEW_MS=5000;
const HEALTH_TIMESTAMP_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?([Zz]|[+-][0-9]{2}:[0-9]{2})$';
function sampledTime(value:unknown){
 if(typeof value!=='string'||!new RegExp(HEALTH_TIMESTAMP_PATTERN).test(value))return NaN;
 const year=Number(value.slice(0,4)),month=Number(value.slice(5,7)),day=Number(value.slice(8,10));
 const days=[31,year%4===0&&(year%100!==0||year%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
 if(month<1||month>12||day<1||day>days[month-1])return NaN;
 return Date.parse(value);
}
export function runtimeOnlineSql(alias='b'){
 if(!/^[a-z][a-z0-9_]*$/i.test(alias))throw new Error('Invalid SQL alias');
 return `${alias}.deleted_at IS NULL AND ${alias}.desired_state='running' AND ${alias}.health->'protocol'->'online'='true'::jsonb AND CASE WHEN ${alias}.health->>'sampledAt' ~ '${HEALTH_TIMESTAMP_PATTERN}' AND pg_input_is_valid(${alias}.health->>'sampledAt','timestamp with time zone') THEN (${alias}.health->>'sampledAt')::timestamptz END BETWEEN now()-interval '${HEALTH_MAX_AGE_MS} milliseconds' AND now()+interval '${HEALTH_FUTURE_SKEW_MS} milliseconds'`;
}
export function runtimeStatus(bot:any,now=Date.now()){
 const at=sampledTime(bot?.health?.sampledAt);
 const fresh=Number.isFinite(at)&&now-at>=-HEALTH_FUTURE_SKEW_MS&&now-at<=HEALTH_MAX_AGE_MS;
 const available=!bot?.deleted_at&&!['stopped','offline','deleting','stopping'].includes(bot?.desired_state);
 return{fresh,online:fresh&&available&&bot?.health?.protocol?.online===true,aiReady:fresh&&available&&bot?.health?.ai?.ready===true,aiAlive:fresh&&available&&bot?.health?.ai?.alive===true};
}
