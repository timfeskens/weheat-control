import { mkdir, readFile, open, rename, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { tokensSchema, operationsSchema } from './schemas.js';
import type { Tokens, Operations } from './schemas.js';
import { hasErrorCode } from './errors.js';

export interface StateStore {
  readTokens(): Promise<Tokens | null>;
  writeTokens(tokens: Tokens): Promise<void>;
  readOperations(): Promise<Operations>;
  writeOperations(operations: Operations): Promise<void>;
}

export class Store implements StateStore {
  constructor(private readonly directory: string) {}

  private async read(name: string): Promise<string | null> {
    try { return await readFile(join(this.directory, name), 'utf8'); }
    catch (error) {
      if (error instanceof Error && hasErrorCode(error, 'ENOENT')) return null;
      throw new Error(`Cannot read ${name}; refusing to replace existing state.`);
    }
  }

  private async write(name: string, value: Tokens | Operations): Promise<void> {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const temporary = join(this.directory, `${name}.${randomUUID()}.tmp`);
    const file = await open(temporary, 'wx', 0o600);
    try { await file.writeFile(JSON.stringify(value, null, 2)); await file.sync(); }
    finally { await file.close(); }
    try { await rename(temporary, join(this.directory, name)); }
    finally { await unlink(temporary).catch(() => {}); }
  }

  async readTokens(): Promise<Tokens | null> {
    const text = await this.read('tokens.json');
    if (text === null) return null;
    return tokensSchema.parse(JSON.parse(text));
  }

  writeTokens(tokens: Tokens): Promise<void> { return this.write('tokens.json', tokensSchema.parse(tokens)); }

  async readOperations(): Promise<Operations> {
    const text = await this.read('operations.json');
    return text === null ? {} : operationsSchema.parse(JSON.parse(text));
  }

  writeOperations(operations: Operations): Promise<void> {
    return this.write('operations.json', operationsSchema.parse(operations));
  }
}
