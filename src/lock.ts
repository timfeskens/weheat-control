import { mkdir, open, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { hasErrorCode } from './errors.js';

export async function acquireLock(directory: string): Promise<() => Promise<void>> {
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const path = join(directory, 'process.lock');
  const file = await open(path, 'wx', 0o600).catch(error => {
    if (error instanceof Error && hasErrorCode(error, 'EEXIST')) {
      throw new Error('Another command owns .state/process.lock. Stop the server before login/devices. After a crash, verify the recorded PID before removing that lock.');
    }
    throw new Error('Could not acquire the private state lock.');
  });
  try { await file.writeFile(String(process.pid)); }
  finally { await file.close(); }
  return () => unlink(path);
}
