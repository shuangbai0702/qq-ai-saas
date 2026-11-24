/** LLBot JS heap is separately recoverable; QQNT memory is never treated as JS. */
export function resourceDecision(state,sample){
 const now=sample.at;
 if(state.pid!==sample.pid){for(const key of Object.keys(state))delete state[key];state.pid=sample.pid;state.started=now}
 const high=sample.rssBytes>768*1048576||sample.heapUsed>384*1048576;
 if(!high)delete state.highSince;else state.highSince??=now;
 const critical=sample.heapUsed>512*1048576||sample.rssBytes>1536*1048576;
 const idle=!sample.active&&now-sample.lastActivity>=120000;
 if(!high)return{decision:'healthy'};
 if(!idle)return{decision:'busy',pressureSince:state.highSince};
 if(!state.reclaimedAt||now-state.reclaimedAt>=300000){state.reclaimedAt=now;return{decision:'reclaim-js',pressureSince:state.highSince}}
 if(now-state.highSince>=300000&&(critical||sample.available<sample.total*.25))return{decision:'restart-llbot',pressureSince:state.highSince};
 return{decision:'observing',pressureSince:state.highSince};
}
