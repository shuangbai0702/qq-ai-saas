export type ExternalFailureSource = {
  endpoint: string;
  method: 'GET' | 'POST';
  httpStatus?: number;
  platformCode?: number;
};

export class MediaError extends Error {
  constructor(
    public code: string,
    message: string,
    public ambiguous = false,
    public source?: ExternalFailureSource,
    public usage?: Record<string, unknown>,
  ) {
    super(message);
  }
}
