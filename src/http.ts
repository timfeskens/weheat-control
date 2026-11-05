import { createServer } from 'node:http';
import type { IncomingMessage, Server } from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';
import { z } from 'zod';
import { AppError } from './errors.js';
import { boostInputSchema } from './schemas.js';
import type { BoostInput } from './schemas.js';
import type { BoostService } from './service.js';
import type { Config } from './config.js';

const digest = (value: string): Buffer => createHash('sha256').update(value).digest();

async function readBoost(request: IncomingMessage): Promise<BoostInput> {
  if (request.headers['content-type']?.split(';')[0] !== 'application/json') throw new AppError(415, 'Use Content-Type: application/json.');
  request.setEncoding('utf8');
  let body = '';
  for await (const chunk of request) {
    body += z.string().parse(chunk);
    if (Buffer.byteLength(body) > 4096) throw new AppError(413, 'Request body too large.');
  }
  try {
    const result = boostInputSchema.safeParse(JSON.parse(body));
    if (!result.success) throw new AppError(400, 'Use enabled:boolean, durationMinutes:1–120 for on only, and optional dryRun:boolean. No other fields are accepted.');
    return result.data;
  } catch (error) {
    if (error instanceof AppError) throw error;
    throw new AppError(400, 'Invalid JSON.');
  }
}

export function createApi(service: BoostService, config: Config): Server {
  const server = createServer(async (request, response) => {
    const reply = <T>(status: number, body: T): void => {
      response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'", Connection: 'close' });
      response.end(JSON.stringify(body));
    };
    try {
      const address = server.address();
      const port = address !== null && typeof address !== 'string' ? address.port : config.port;
      if (request.headers.origin || ![`127.0.0.1:${port}`, `localhost:${port}`].includes(request.headers.host ?? '')) {
        throw new AppError(403, 'Only local non-browser requests are allowed.');
      }
      const path = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
      if (request.method === 'GET' && path === '/health') return reply(200, { status: 'ok', liveDeviceVerified: false });
      if (!timingSafeEqual(digest(request.headers.authorization ?? ''), digest(`Bearer ${config.apiKey}`))) throw new AppError(401, 'A local API bearer key is required.');
      if (request.method === 'GET' && path === '/devices') return reply(200, await service.devices());
      if (request.method === 'GET' && path === '/settings') return reply(200, await service.settings());
      if (request.method === 'POST' && path === '/boost') {
        const result = await service.boost(await readBoost(request));
        return reply('dryRun' in result ? 200 : 202, result);
      }
      const operation = /^\/operations\/([a-f0-9-]{36})$/.exec(path)?.[1];
      if (request.method === 'GET' && operation) return reply(200, await service.operation(operation));
      throw new AppError(404, 'Route not found.');
    } catch (error) {
      reply(error instanceof AppError ? error.status : 500, {
        error: error instanceof AppError ? error.message : 'Internal error; no automatic retry was performed.',
      });
    }
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 5000;
  return server;
}
