import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {AutonomyRepository} from '../../../packages/database/autonomous.js';
import {AutonomousGoalAudienceSchema,AutonomousGoalUpdateSchema} from '../../../packages/contracts/autonomous.js';
export function registerAutonomousGoals(app:FastifyInstance,db:Sql,runtime:(req:any)=>Promise<any>){
 const repo=new AutonomyRepository(db);
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/autonomous-goals/read',async req=>{
  const bot=await runtime(req),v=z.object({trusted:AutonomousGoalAudienceSchema}).strict().parse(req.body);
  return repo.readGoals(bot,v.trusted);
 });
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/autonomous-goals/update',async req=>{
  const bot=await runtime(req),v=z.object({trusted:AutonomousGoalAudienceSchema,update:AutonomousGoalUpdateSchema}).strict().parse(req.body);
  return repo.updateGoal(bot,v.trusted,v.update);
 });
}
