export interface ProcessRecord {
  pid: number;
  kind: string;
  created?: number;
  startedAt?: string;
}

export interface ProcessInspection {
  pid: number;
  created: number;
  rssBytes: number;
  valid?: boolean;
  inspectionError?: boolean;
  memoryPolicy?: unknown;
  loopActivity?: { active?: boolean; at?: number };
}

/** Minimal runtime-process interface shared by protocol & AI adapters. */
export interface ProcessHostLike {
  readonly root: string;
  inspect(id: string, kind: string): Promise<ProcessInspection | null>;
  record(id: string, kind: string): Promise<ProcessRecord | null>;
  start(
    id: string,
    kind: string,
    executable: string,
    args: string[],
    cwd: string,
    env: NodeJS.ProcessEnv,
    keys?: string[],
  ): Promise<void>;
  stop(
    id: string,
    kind: string,
    intent?: string,
    expected?: { pid: number; created?: number },
  ): Promise<void>;
  endInspection(): void;
}
