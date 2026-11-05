import { lstat, open, readFile, rename, unlink } from 'node:fs/promises';
import { randomBytes, randomUUID } from 'node:crypto';
import { parseEnv } from 'node:util';
import { AppError, hasErrorCode } from './errors.js';

export async function ensureEnvironment(path: string): Promise<boolean> {
  const file = await open(path, 'wx', 0o600).catch(error => {
    if (error instanceof Error && hasErrorCode(error, 'EEXIST')) return null;
    throw new AppError(500, 'Could not create the private .env file.');
  });
  if (!file) return false;
  try {
    await file.writeFile(`WEHEAT_LOCAL_API_KEY=${randomBytes(32).toString('hex')}\nWEHEAT_INSTALLATION_ID=\nPORT=8787\n`);
    await file.sync();
  } finally { await file.close(); }
  return true;
}

/** Preserve all unrelated lines, comments, line endings, and effective values. */
export function selectedEnvironment(text: string, installationId: string): string {
  if (!/^[a-zA-Z0-9_-]+$/.test(installationId)) {
    throw new AppError(409, 'Installation ID cannot be safely written to .env. Configuration was not changed.');
  }
  const values = new Map([
    ['WEHEAT_INSTALLATION_ID', installationId],
  ]);
  const before = parseEnv(text);
  const newline = text.includes('\r\n') ? '\r\n' : '\n';
  const lines = text.split(/\r?\n/);
  const seen = new Set<string>();
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line === undefined) continue;
    const match = /^([ \t]*(?:export[ \t]+)?)(WEHEAT_INSTALLATION_ID)([ \t]*=[ \t]*)(.*)$/.exec(line);
    if (!match) continue;
    const [, prefix, key, separator, rest] = match;
    if (!key || prefix === undefined || separator === undefined || rest === undefined) continue;
    if (seen.has(key)) throw new AppError(409, 'Duplicate onboarding settings in .env. Remove duplicate installation settings before retrying.');
    // Only single-line values are safe to replace. Multiline or unusual syntax
    // fails closed instead of accidentally rewriting unrelated configuration.
    const value = /^(?:"[^"\r\n]*"|'[^'\r\n]*'|[^'"#\r\n]*?)([ \t]*(?:#.*)?)$/.exec(rest);
    const replacement = values.get(key);
    if (!value || replacement === undefined) throw new AppError(409, 'Onboarding settings must use simple single-line .env values. Configuration was not changed.');
    lines[index] = `${prefix}${key}${separator}${replacement}${value[1] ?? ''}`;
    seen.add(key);
  }
  let result = lines.join(newline);
  for (const [key, value] of values) {
    if (seen.has(key)) continue;
    if (result && !result.endsWith('\n')) result += newline;
    result += `${key}=${value}${newline}`;
  }
  const after = parseEnv(result);
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (values.has(key) ? after[key] !== values.get(key) : before[key] !== after[key]) {
      throw new AppError(409, 'Could not preserve .env configuration safely; no changes were saved.');
    }
  }
  return result;
}

export async function saveSelectedEnvironment(path: string, installationId: string): Promise<void> {
  if (!(await lstat(path)).isFile()) throw new AppError(409, '.env must be a regular file, not a symbolic link.');
  const original = await readFile(path, 'utf8');
  const updated = selectedEnvironment(original, installationId);
  const temporary = `${path}.${randomUUID()}.tmp`;
  const file = await open(temporary, 'wx', 0o600);
  try {
    try { await file.writeFile(updated); await file.sync(); }
    finally { await file.close(); }
    if (await readFile(path, 'utf8') !== original) throw new AppError(409, '.env changed during onboarding; retry to avoid overwriting edits.');
    await rename(temporary, path);
  } finally { await unlink(temporary).catch(() => {}); }
}
