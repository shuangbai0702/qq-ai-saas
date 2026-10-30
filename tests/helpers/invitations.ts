import type {Sql} from '../../packages/database/index.js';import {issueInvitation} from '../../packages/shared/invitations.js';
// Privileged fixture setup in an isolated database; the real register route
// still validates and atomically consumes a real invitation for every account.
export async function issueTestInvite(db:Sql,botLimit=1){return(await issueInvitation(db,null,{botLimit})).code}
