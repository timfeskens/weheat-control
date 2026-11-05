export class AppError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export function hasErrorCode(error: Error, code: string): boolean {
  return 'code' in error && error.code === code;
}
