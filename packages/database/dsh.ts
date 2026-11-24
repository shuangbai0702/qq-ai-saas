import type { Sql } from './index.js';

export async function listActiveBotIdentitiesForDsh(db: Sql): Promise<Array<{ id: string; tenant_id: string }>> {
  return db.query('SELECT id,tenant_id FROM bots WHERE deleted_at IS NULL');
}

export async function listRunningBotsForDsh(db: Sql, botId?: string): Promise<any[]> {
  return db.query(
    "SELECT * FROM bots WHERE deleted_at IS NULL AND desired_state='running'" + (botId ? ' AND id=$1' : ''),
    botId ? [botId] : [],
  );
}

export async function findActiveBotForDsh(db: Sql, botId: string): Promise<any | undefined> {
  const [bot] = await db.query('SELECT * FROM bots WHERE id=$1 AND deleted_at IS NULL', [botId]);
  return bot;
}

export async function findActingAutonomyCycleDecision(
  db: Sql,
  cycleId: string,
  botId: string,
  tenantId: string,
  epoch: number,
): Promise<any | undefined> {
  const [cycle] = await db.query(
    "SELECT decision FROM autonomy_cycles WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND epoch=$4 AND finished_at IS NULL AND state='acting'",
    [cycleId, botId, tenantId, epoch],
  );
  return cycle?.decision;
}
