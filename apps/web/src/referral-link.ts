export function referralFromLocation(){
 const [route,query]=location.hash.split('?',2);
 const referralCode=route.match(/^#\/invite\/([A-Za-z0-9_-]{24})$/)?.[1];
 if(!referralCode)return;
 const inviteCode=new URLSearchParams(query).get('activation')?.trim();
 return {referralCode,inviteCode:inviteCode&&inviteCode.length<=100?inviteCode:undefined};
}

