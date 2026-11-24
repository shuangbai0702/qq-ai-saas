export function httpError(message: string, statusCode = 400): Error & { statusCode: number } {
  return Object.assign(new Error(message), { statusCode });
}

export function requireAdmin(req: { user?: { is_admin?: boolean } }): void {
  if (!req.user?.is_admin) throw httpError('需要管理员权限', 403);
}

export function mockProtocolAllowed(): boolean {
  if (process.env.FISHBOT_ALLOW_MOCK_PROTOCOL === 'true') return true;
  return process.env.NODE_ENV !== 'production';
}
