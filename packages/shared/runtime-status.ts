// Health reports expire; a saved online flag is not proof of a live QQ.
export function runtimeStatus(bot:any,now=Date.now()){
 const at=Date.parse(bot?.health?.sampledAt||'');
 const fresh=Number.isFinite(at)&&now-at>=-5000&&now-at<=90000;
 const available=!['offline','deleting','stopping'].includes(bot?.desired_state);
 return{fresh,online:fresh&&available&&bot?.health?.protocol?.online===true,aiReady:fresh&&available&&bot?.health?.ai?.ready===true,aiAlive:fresh&&available&&bot?.health?.ai?.alive===true};
}
