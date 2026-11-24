import path from 'node:path';
import type { BotConfig, ProtocolKind } from '../../../packages/contracts/index.js';
import { BotConfigSchema } from '../../../packages/contracts/index.js';
import type { Sql } from '../../../packages/database/index.js';
import { decrypt } from '../../../packages/shared/security.js';
import { resolvePlatformModel } from '../../../packages/shared/platform-model.js';
import { runtimeIpc } from '../../../packages/shared/runtime-ipc.js';
import type { NodeAgent } from '../../node-agent/src/agent.js';
import type { MockProtocolAdapter } from '../../../adapters/mock/index.js';

const PROTOCOL_CAPABILITIES: Record<ProtocolKind, string[]> = {
  mock: ['mock', 'qr', 'cancel', 'text', 'onebot-reverse-ws'],
  napcat: ['qr', 'text', 'image', 'record', 'onebot-reverse-ws', 'private-management-api'],
  euler: ['qr', 'cancel', 'requires-uin', 'requires-signer', 'onebot-reverse-ws', 'text', 'image', 'record'],
};

export interface AiIpcTarget {
  port: number;
  route: string;
  token: string;
  groupGeneration: string;
}

/** Minimal Control API -> Runtime interface; never leaks NodeAgent internals. */
export interface RuntimeControlPort {
  readonly pythonExecutable: string;
  protocolCapabilities(protocol: ProtocolKind): string[];
  personalModelConnection(bot: { id: string; tenant_id: string }): Promise<{ baseUrl: string; apiKey: string }>;
  modelProbeContext(bot: any): Promise<{ config: BotConfig; apiKey: string }>;
  logSecrets(bot: any): Promise<{ apiKey: string; token: string }>;
  isAiRunning(bot: any): Promise<boolean>;
  aiIpcTarget(bot: any): Promise<AiIpcTarget>;
  applyUserCards(botId: string, tenantId: string, expectedRevision: number): Promise<boolean>;
  applyUserPersona(botId: string, tenantId: string): Promise<boolean>;
  applyUserTools(botId: string, tenantId: string): Promise<boolean>;
  mockTransition(bot: any, body: { state: string; uin?: string }): Promise<unknown>;
}

export function resolvePythonExecutable(): string {
  return path.resolve(
    process.env.ASTRBOT_PYTHON ||
      `references/astrbot/.venv/${process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'}`,
  );
}

/** Stateless DB-backed control port used when EXTERNAL_AGENT=true (no NodeAgent in API process). */
export function createExternalRuntimeControl(db: Sql, _root: string, key: Buffer): RuntimeControlPort {
  const pythonExecutable = resolvePythonExecutable();

  async function personalModelConnection(bot: { id: string; tenant_id: string }) {
    const [row] = await db.query(
      `SELECT r.config->>'baseUrl' AS base_url,s.ciphertext FROM bots b
       JOIN bot_config_revisions r ON r.bot_id=b.id AND r.revision=b.revision
       LEFT JOIN secrets s ON s.bot_id=b.id AND s.tenant_id=b.tenant_id
       WHERE b.id=$1 AND b.tenant_id=$2 AND b.deleted_at IS NULL`,
      [bot.id, bot.tenant_id],
    );
    return {
      baseUrl: row?.base_url ?? '',
      apiKey: row?.ciphertext ? decrypt(key, bot.tenant_id + ':' + bot.id, row.ciphertext) : '',
    };
  }

  async function modelProbeContext(bot: any) {
    const [revision] = await db.query(
      `SELECT wanted.config,actual.config AS current_config,s.ciphertext FROM bots b
       JOIN bot_config_revisions wanted ON wanted.bot_id=b.id AND wanted.revision=$2
       JOIN bot_config_revisions actual ON actual.bot_id=b.id AND actual.revision=b.revision
       LEFT JOIN secrets s ON s.bot_id=b.id AND s.tenant_id=b.tenant_id
       WHERE b.id=$1 AND b.tenant_id=$3`,
      [bot.id, bot.revision, bot.tenant_id],
    );
    const config = BotConfigSchema.parse(revision.config);
    const model = config.platformModel
      ? resolvePlatformModel(config, key, bot)
      : {
          config,
          apiKey:
            revision.ciphertext &&
            revision.current_config.baseUrl === revision.config.baseUrl &&
            (revision.current_config.behavior?.embeddingBaseUrl || '') === (revision.config.behavior?.embeddingBaseUrl || '')
              ? decrypt(key, bot.tenant_id + ':' + bot.id, revision.ciphertext)
              : '',
        };
    return { config: model.config, apiKey: model.config.model.trim() ? model.apiKey : '' };
  }

  async function logSecrets(bot: any) {
    const [instance] = await db.query('SELECT token FROM instances WHERE bot_id=$1', [bot.id]);
    const conn = await personalModelConnection(bot);
    return { apiKey: conn.apiKey, token: instance?.token || '' };
  }

  async function aiIpcTarget(bot: any): Promise<AiIpcTarget> {
    if (bot.runtime_mode === 'shared') {
      const [group] = await db.query(
        'SELECT g.health_port,g.token,g.generation FROM group_members m JOIN runtime_groups g ON g.id=m.group_id WHERE m.bot_id=$1',
        [bot.id],
      );
      if (!group) throw Object.assign(new Error('运行组尚未就绪'), { statusCode: 409 });
      return {
        port: Number(group.health_port),
        route: `/members/${bot.id}/behavior-data`,
        token: group.token,
        groupGeneration: String(group.generation),
      };
    }
    const [instance] = await db.query('SELECT ai_port,token FROM instances WHERE bot_id=$1', [bot.id]);
    if (!instance) throw Object.assign(new Error('运行实例尚未就绪，请稍后重试'), { statusCode: 409 });
    return {
      port: Number(instance.ai_port) + 1,
      route: '/behavior-data',
      token: instance.token,
      groupGeneration: '',
    };
  }

  async function isAiRunning(bot: any): Promise<boolean> {
    if (bot.desired_state === 'stopped' && bot.observed_state === 'stopped') return false;
    try {
      const target = await aiIpcTarget(bot);
      const res = await runtimeIpc(target.port, '/health', {
        method: 'GET',
        headers: { Authorization: `Bearer ${target.token}` },
        timeoutMs: 1500,
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  return {
    pythonExecutable,
    protocolCapabilities(protocol) {
      return [...(PROTOCOL_CAPABILITIES[protocol] || [])];
    },
    personalModelConnection,
    modelProbeContext,
    logSecrets,
    isAiRunning,
    aiIpcTarget,
    // External NodeAgent owns live configuration apply via the durable commands table.
    async applyUserCards() {
      return false;
    },
    async applyUserPersona() {
      return false;
    },
    async applyUserTools() {
      return false;
    },
    async mockTransition(bot, body) {
      const [instance] = await db.query('SELECT protocol_port,token FROM instances WHERE bot_id=$1', [bot.id]);
      if (!instance) throw Object.assign(new Error('模拟协议尚未就绪'), { statusCode: 409 });
      const res = await fetch(`http://127.0.0.1:${instance.protocol_port}/transition`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${instance.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(2000),
      });
      if (!res.ok) throw Object.assign(new Error('模拟协议尚未就绪'), { statusCode: 409 });
      return res.json();
    },
  };
}

/** Embedded single-process control port backed by an in-process NodeAgent. */
export function createEmbeddedRuntimeControl(agent: NodeAgent): RuntimeControlPort {
  return {
    pythonExecutable: agent.host.python,
    protocolCapabilities(protocol) {
      return agent.protocols[protocol].getCapabilities();
    },
    personalModelConnection(bot) {
      return agent.personalModelConnection(bot);
    },
    async modelProbeContext(bot) {
      const ctx = await agent.context(bot);
      return { config: ctx.config, apiKey: ctx.apiKey };
    },
    async logSecrets(bot) {
      const ctx = await agent.context(bot);
      return { apiKey: ctx.apiKey, token: ctx.token };
    },
    async isAiRunning(bot) {
      const processId = bot.runtime_mode === 'shared' ? bot.group_id : bot.id;
      if (!processId) return false;
      return Boolean(await agent.host.inspect(processId, 'ai'));
    },
    async aiIpcTarget(bot) {
      const ctx = await agent.context(bot);
      if (bot.runtime_mode === 'shared') {
        const group = await agent.shared.group(bot.id);
        if (!group) throw Object.assign(new Error('运行组尚未就绪'), { statusCode: 409 });
        return {
          port: group.health_port,
          route: `/members/${bot.id}/behavior-data`,
          token: group.token,
          groupGeneration: String(group.generation),
        };
      }
      return {
        port: ctx.aiPort + 1,
        route: '/behavior-data',
        token: ctx.token,
        groupGeneration: '',
      };
    },
    applyUserCards(botId, tenantId, expectedRevision) {
      return agent.applyUserCards(botId, tenantId, expectedRevision);
    },
    applyUserPersona(botId, tenantId) {
      return agent.applyUserPersona(botId, tenantId);
    },
    applyUserTools(botId, tenantId) {
      return agent.applyUserTools(botId, tenantId);
    },
    async mockTransition(bot, body) {
      const adapter = agent.protocols.mock as MockProtocolAdapter;
      return adapter.call(await agent.context(bot), '/transition', body);
    },
  };
}
