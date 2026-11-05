import { fileURLToPath } from 'node:url';
import { z } from 'zod';

export const ROOT = fileURLToPath(new URL('../', import.meta.url));
export const STATE = fileURLToPath(new URL('../.state/', import.meta.url));
export interface Config {
  apiKey: string;
  port: number;
  installationId: string;
}

const environmentSchema = z.object({
  WEHEAT_LOCAL_API_KEY: z.string().min(32).refine(value => !value.startsWith('replace-')),
  PORT: z.coerce.number().int().min(1024).max(65535).default(8787),
  WEHEAT_INSTALLATION_ID: z.string().trim().default(''),
});

export function config(): Config {
  const result = environmentSchema.safeParse(process.env);
  if (!result.success) throw new Error('Invalid environment. Run npm run setup, then check .env (API key and port).');
  return {
    apiKey: result.data.WEHEAT_LOCAL_API_KEY, port: result.data.PORT,
    installationId: result.data.WEHEAT_INSTALLATION_ID,
  };
}
