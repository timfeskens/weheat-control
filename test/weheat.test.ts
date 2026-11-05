import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, stat, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { request } from 'node:http';
import ky, { HTTPError } from 'ky';
import ts from 'typescript';
import { z } from 'zod';
import { glob } from 'node:fs/promises';
import { Store } from '../src/storage.js';
import type { StateStore } from '../src/storage.js';
import { BoostService } from '../src/service.js';
import { createApi } from '../src/http.js';
import { acquireLock } from '../src/lock.js';
import { AppError } from '../src/errors.js';
import { boostInputSchema, installationsSchema, settingsSchema, submissionSchema, tokenResponseSchema } from '../src/schemas.js';
import type { BoostSettings, Operations, Tokens, Installation, Settings } from '../src/schemas.js';
import type { Config } from '../src/config.js';
import { API, ISSUER, REDIRECT_URI, boostSettings, callbackCode, discovery, loginRequest,
  operationStatus, tokenRecord, WeheatClient } from '../src/weheat.js';
import type { StatusResult, WeheatGateway } from '../src/weheat.js';

const json = <T>(body: T, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const endpoints = { issuer: ISSUER, authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`, token_endpoint: `${ISSUER}/protocol/openid-connect/token` };
const configuration: Config = { apiKey: 'a'.repeat(64), port: 8787, installationId: 'installation-1' };
const on = (): z.infer<typeof boostInputSchema> => boostInputSchema.parse({ enabled: true, durationMinutes: 30 });
const off = (): z.infer<typeof boostInputSchema> => boostInputSchema.parse({ enabled: false });

class MemoryStore implements StateStore {
  tokens: Tokens | null = null;
  operations: Operations = {};
  async readTokens(): Promise<Tokens | null> { return structuredClone(this.tokens); }
  async writeTokens(tokens: Tokens): Promise<void> { this.tokens = structuredClone(tokens); }
  async readOperations(): Promise<Operations> { return structuredClone(this.operations); }
  async writeOperations(operations: Operations): Promise<void> { this.operations = structuredClone(operations); }
}

class MockGateway implements WeheatGateway {
  calls: { board: string; settings: BoostSettings }[] = [];
  submission: 'success' | 'missing-id' | 'timeout' = 'success';
  readback = true;
  status: StatusResult = operationStatus(2);
  constructor(private readonly store: MemoryStore) {}
  async installations(): Promise<Installation[]> {
    return [{ id: 'installation-1', heatPump: { id: 'pump-1', controlBoardId: 'board-1' } }];
  }
  async patch(board: string, settings: BoostSettings): Promise<string | null> {
    this.calls.push({ board, settings });
    if (this.submission === 'timeout') throw new Error('timeout');
    return this.submission === 'missing-id' ? null : 'request-1';
  }
  async operation(board: string, id: string): Promise<StatusResult> {
    assert.equal(board, 'board-1'); assert.equal(id, 'request-1');
    return this.status;
  }
  async settings(): Promise<Settings> {
    if (!this.readback) return {};
    const first = Object.values(this.store.operations)[0];
    return first ? { dhwBoostEndTime: first.settings.DhwBoostEndTime } : {};
  }
}

function fixture(overrides: Partial<Config> = {}): { store: MemoryStore; client: MockGateway; service: BoostService } {
  const store = new MemoryStore();
  const client = new MockGateway(store);
  return { store, client, service: new BoostService(client, store, { ...configuration, ...overrides }) };
}

const mockHttp = (handler: (request: Request) => Promise<Response>) => ky.create({
  retry: 0, timeout: 1000, redirect: 'error', fetch: async input => handler(new Request(input)),
});

test('boost on/off uses string Unix seconds and finite duration', () => {
  assert.deepEqual(boostSettings(on(), 1_700_000_000_999), { DhwBoostEndTime: '1700001800' });
  assert.deepEqual(boostSettings(off(), 1_700_000_000_999), { DhwBoostEndTime: '1700000000' });
});

test('Zod rejects invalid input instead of asserting a JSON response type', () => {
  for (const enabled of ['true', 1, null, undefined]) assert.equal(boostInputSchema.safeParse({ enabled, durationMinutes: 30 }).success, false);
  for (const duration of [0, -1, 121, 1.5, '30', null, undefined, Infinity]) assert.equal(boostInputSchema.safeParse({ enabled: true, durationMinutes: duration }).success, false);
  for (const body of [null, [], { enabled: false, arbitrarySetting: 1 }, { enabled: false, dryRun: 'false' }, { enabled: false, durationMinutes: 30 }]) {
    assert.equal(boostInputSchema.safeParse(body).success, false);
  }
});

test('PKCE S256, scopes, fixed callback, and state verification', () => {
  const value = loginRequest(endpoints.authorization_endpoint);
  const url = new URL(value.url);
  assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
  assert.equal(url.searchParams.get('code_challenge')?.length, 43);
  assert.equal(url.searchParams.get('redirect_uri'), REDIRECT_URI);
  assert.equal(url.searchParams.get('scope'), 'openid profile');
  assert.ok(value.state.length >= 40); assert.ok(value.verifier.length >= 43);
  assert.equal(callbackCode(`weheat://auth?code=example&state=${value.state}`, value.state), 'example');
});

test('reject incorrect callback, state, duplicates, missing code, fragments and errors', () => {
  for (const callback of [
    'weheat://auth?code=a&state=wrong', 'https://example.com?code=a&state=right',
    'weheat://auth?state=right', 'weheat://auth?code=a&state=right&error=denied',
    'weheat://auth?code=a&state=right#fragment', 'weheat://auth?code=a&code=b&state=right',
    'weheat://auth?code=a&state=right&state=right',
  ]) assert.throws(() => callbackCode(callback, 'right'));
});

test('discovery validates issuer and does not allow credential forwarding to another host', async () => {
  assert.deepEqual(await discovery(mockHttp(async () => json(endpoints))), {
    authorization: endpoints.authorization_endpoint, token: endpoints.token_endpoint,
  });
  await assert.rejects(discovery(mockHttp(async () => json({ ...endpoints, issuer: 'https://other.test' }))));
  await assert.rejects(discovery(mockHttp(async () => json({ ...endpoints, token_endpoint: 'https://other.test/token' }))));
});

test('discovery uses the live canonical issuer without the legacy /auth prefix', async () => {
  assert.equal(ISSUER, 'https://auth.weheat.nl/realms/Weheat');
  const result = await discovery(mockHttp(async req => {
    assert.equal(req.url, 'https://auth.weheat.nl/realms/Weheat/.well-known/openid-configuration');
    return json({
      issuer: 'https://auth.weheat.nl/realms/Weheat',
      authorization_endpoint: 'https://auth.weheat.nl/realms/Weheat/protocol/openid-connect/auth',
      token_endpoint: 'https://auth.weheat.nl/realms/Weheat/protocol/openid-connect/token',
    });
  }));
  assert.equal(new URL(loginRequest(result.authorization).url).pathname, '/realms/Weheat/protocol/openid-connect/auth');
});

test('discovery still rejects legacy, foreign-realm, insecure, and injected endpoints', async () => {
  await assert.rejects(discovery(mockHttp(async () => json({ ...endpoints, issuer: 'https://auth.weheat.nl/auth/realms/Weheat' }))));
  for (const token_endpoint of [
    'https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/token',
    'https://auth.weheat.nl/realms/Other/protocol/openid-connect/token',
    'http://auth.weheat.nl/realms/Weheat/protocol/openid-connect/token',
    `${endpoints.token_endpoint}?redirect=other`, `${endpoints.token_endpoint}#fragment`,
    'https://user:password@auth.weheat.nl/realms/Weheat/protocol/openid-connect/token',
  ]) await assert.rejects(discovery(mockHttp(async () => json({ ...endpoints, token_endpoint }))));
});

test('schema-validated tokens support refresh token rotation', () => {
  const previous = tokenRecord(tokenResponseSchema.parse({ access_token: 'old', refresh_token: 'refresh' }));
  assert.equal(tokenRecord(tokenResponseSchema.parse({ access_token: 'new' }), previous).refresh_token, 'refresh');
  assert.equal(tokenRecord(tokenResponseSchema.parse({ access_token: 'new', refresh_token: 'rotated' }), previous).refresh_token, 'rotated');
  assert.equal(tokenResponseSchema.safeParse({}).success, false);
});

test('boundary schemas normalize supported envelopes, strip unsupported settings, fail closed on malformed data', () => {
  assert.equal(installationsSchema.parse({ data: [{ id: 1, heatPump: { controlBoardId: 2 } }] })[0]?.heatPump?.controlBoardId, '2');
  assert.equal(installationsSchema.safeParse({ data: [{ heatPump: {} }] }).success, false);
  assert.deepEqual(settingsSchema.parse({ data: { dhwBoostEndTime: 123, privateField: 'not-exposed' } }), { dhwBoostEndTime: '123' });
  assert.deepEqual(settingsSchema.parse({ dhwBoostEndTime: '2026-07-26T12:28:27Z' }), {
    dhwBoostEndTime: String(Date.parse('2026-07-26T12:28:27Z') / 1000),
  });
  assert.equal(settingsSchema.safeParse({ dhwBoostEndTime: 'not-a-number' }).success, false);
  assert.deepEqual(submissionSchema.parse({ data: { settingsRequestId: 0 } }), { settingsRequestId: '0' });
  assert.deepEqual(submissionSchema.parse({ unrelated: true }), {});
});

test('status preserves mismatch, failures and unrecognized states', () => {
  assert.equal(operationStatus(0).terminal, false);
  assert.equal(operationStatus(1).state, 'executing');
  assert.equal(operationStatus(2).state, 'succeeded');
  assert.equal(operationStatus(5).state, 'succeeded_with_mismatch');
  assert.equal(operationStatus(11).state, 'failed_another_request_executing');
  assert.equal(operationStatus(99).state, 'unknown');
  assert.equal(operationStatus(undefined).terminal, false);
});

test('ky sends the recovered PATCH request shape and encodes the board ID', async () => {
  const store = new MemoryStore();
  store.tokens = tokenRecord(tokenResponseSchema.parse({ access_token: 'secret', expires_in: 300 }));
  const client = new WeheatClient(store, mockHttp(async req => {
    assert.equal(req.url, `${API}/control-boards/board%2F1/settings`);
    assert.equal(req.method, 'PATCH');
    assert.equal(req.headers.get('Authorization'), 'Bearer secret');
    assert.equal(req.headers.get('x-version'), '4.5.14');
    assert.equal(req.redirect, 'error');
    assert.deepEqual(JSON.parse(await req.text()), { settings: { DhwBoostEndTime: '123' }, updateImmediately: false });
    return json({ settingsRequestId: 'request-1' });
  }));
  assert.equal(await client.patch('board/1', { DhwBoostEndTime: '123' }), 'request-1');
});

test('device discovery and live-verified installation settings reads omit x-version', async () => {
  const store = new MemoryStore();
  store.tokens = tokenRecord(tokenResponseSchema.parse({ access_token: 'secret', expires_in: 300 }));
  const client = new WeheatClient(store, mockHttp(async req => {
    assert.equal(req.headers.get('Authorization'), 'Bearer secret');
    if (req.url === `${API}/installations/fully-expanded`) {
      assert.equal(req.headers.has('x-version'), false);
      return json({ data: [{ id: 'installation-1', heatPump: { controlBoardId: 'board-1' } }] });
    }
    assert.equal(req.url, `${API}/installations/installation-1/settings`);
    assert.equal(req.headers.has('x-version'), false);
    return json({ dhwBoostEndTime: '0' });
  }));
  assert.equal((await client.installations())[0]?.id, 'installation-1');
  assert.deepEqual(await client.settings('installation-1'), { dhwBoostEndTime: '0' });
});

test('version rejection explains the problem without retrying or echoing upstream bodies', async () => {
  const store = new MemoryStore();
  store.tokens = tokenRecord(tokenResponseSchema.parse({ access_token: 'secret', expires_in: 300 }));
  let calls = 0;
  const client = new WeheatClient(store, mockHttp(async () => {
    calls++;
    return json({ sensitive: 'must-not-echo' }, 505);
  }));
  await assert.rejects(client.settings('installation-1'), error => error instanceof AppError && error.message.includes('version (HTTP 505)') && !error.message.includes('must-not-echo'));
  assert.equal(calls, 1);
});

test('ky never retries writes, including HTTP 401/503 and network failure', async () => {
  for (const outcome of [401, 503, 0]) {
    const store = new MemoryStore();
    store.tokens = tokenRecord(tokenResponseSchema.parse({ access_token: 'secret', expires_in: 300 }));
    let calls = 0;
    const client = new WeheatClient(store, mockHttp(async () => {
      calls++;
      if (outcome === 0) throw new Error('timeout');
      return json({ secret: 'must-not-echo' }, outcome);
    }));
    await assert.rejects(client.patch('board-1', { DhwBoostEndTime: '123' }), error => error instanceof AppError && !error.message.includes('must-not-echo'));
    assert.equal(calls, 1);
  }
});

test('concurrent requests share exactly one proactive refresh', async () => {
  const store = new MemoryStore();
  store.tokens = { access_token: 'old', refresh_token: 'old-refresh', expires_at: 0 };
  let refreshCalls = 0;
  const client = new WeheatClient(store, mockHttp(async req => {
    if (req.url.endsWith('openid-configuration')) return json(endpoints);
    if (req.url === endpoints.token_endpoint) {
      refreshCalls++;
      await new Promise<void>(resolve => setImmediate(resolve));
      return json({ access_token: 'new', refresh_token: 'rotated', expires_in: 300 });
    }
    assert.equal(req.headers.get('Authorization'), 'Bearer new');
    return json({ data: [] });
  }));
  await Promise.all([client.installations(), client.installations()]);
  assert.equal(refreshCalls, 1); assert.equal(store.tokens?.refresh_token, 'rotated');
});

test('preview never mutates and controls submit without opt-in', async () => {
  const { service, client } = fixture();
  const preview = await service.boost(boostInputSchema.parse({ enabled: true, durationMinutes: 30, dryRun: true }));
  assert.ok('dryRun' in preview);
  assert.equal(preview.controlBoardId, 'board-1');
  assert.equal(client.calls.length, 0);
  await service.boost(on());
  assert.equal(client.calls.length, 1);
});

test('device must be explicitly selected and accessible', async () => {
  for (const installationId of ['', 'other']) {
    const { service, client } = fixture({ installationId });
    await assert.rejects(service.boost(off()), { status: 409 });
    assert.equal(client.calls.length, 0);
  }
});

test('track exact request ID, read back actual state, then allow next command', async () => {
  const { service, client } = fixture();
  const first = await service.boost(on());
  assert.ok(!('dryRun' in first));
  assert.equal(first.requestId, 'request-1');
  await assert.rejects(service.boost(off()), { status: 409 });
  const completed = await service.operation(first.id);
  assert.equal(completed.state, 'succeeded');
  assert.equal(completed.readback?.matchesRequestedEndTime, true);
  await service.boost(off()); assert.equal(client.calls.length, 2);
});

test('mismatch status and missing readback are never clean verified success', async () => {
  const { service, client } = fixture();
  client.status = operationStatus(5); client.readback = false;
  const initial = await service.boost(off());
  assert.ok(!('dryRun' in initial));
  const result = await service.operation(initial.id);
  assert.equal(result.state, 'succeeded_with_mismatch');
  assert.equal(result.readback?.verified, false);
});

test('ambiguous submission blocks writes even across service restarts', async () => {
  for (const submission of ['timeout', 'missing-id'] satisfies MockGateway['submission'][]) {
    const { service, store, client } = fixture(); client.submission = submission;
    const result = await service.boost(off());
    assert.ok(!('dryRun' in result)); assert.equal(result.state, 'unknown');
    const restarted = new BoostService(client, store, configuration);
    await assert.rejects(restarted.boost(off()), { status: 409 });
  }
});

test('simultaneous submissions cannot race', async () => {
  const { service, client } = fixture();
  const results = await Promise.allSettled([service.boost(on()), service.boost(off())]);
  assert.equal(results.filter(item => item.status === 'fulfilled').length, 1);
  assert.equal(client.calls.length, 1);
});

test('state uses owner-only files, validates disk JSON, and locks token ownership', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'weheat-test-'));
  try {
    const store = new Store(directory);
    await store.writeTokens(tokenRecord(tokenResponseSchema.parse({ access_token: 'not-real' })));
    assert.equal((await stat(join(directory, 'tokens.json'))).mode & 0o777, 0o600);
    assert.equal((await store.readTokens())?.access_token, 'not-real');
    const release = await acquireLock(directory);
    assert.equal(await readFile(join(directory, 'process.lock'), 'utf8'), String(process.pid));
    await assert.rejects(acquireLock(directory)); await release();
  } finally { await rm(directory, { recursive: true }); }
});

test('HTTP API requires auth, validates JSON, and blocks origins and foreign Host headers', async () => {
  const { service } = fixture();
  const server = createApi(service, configuration);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address !== null && typeof address !== 'string');
  const url = `http://127.0.0.1:${address.port}`;
  const local = ky.create({ prefix: url, retry: 0, throwHttpErrors: false });
  const auth = { Authorization: `Bearer ${configuration.apiKey}` };
  try {
    assert.equal((await local.get('health')).status, 200);
    assert.equal((await local.get('devices')).status, 401);
    assert.equal((await local.get('devices', { headers: auth })).status, 200);
    assert.equal((await local.get('devices', { headers: { ...auth, Origin: 'https://malicious.test' } })).status, 403);
    // Fetch ignores a caller-supplied Host on this Node version. Send a real raw
    // HTTP request to prove the server, not the fetch implementation, rejects it.
    const foreignHostStatus = await new Promise<number>(resolve => {
      const req = request(`${url}/devices`, { headers: { ...auth, Host: 'malicious.test' } }, response => {
        response.resume(); resolve(response.statusCode ?? 0);
      });
      req.end();
    });
    assert.equal(foreignHostStatus, 403);
    for (const body of ['{bad', '{"enabled":"false"}', '{"enabled":false,"extra":1}']) {
      assert.equal((await local.post('boost', { headers: { ...auth, 'Content-Type': 'application/json' }, body })).status, 400);
    }
    assert.equal((await local.post('boost', { headers: { ...auth, 'Content-Type': 'application/json' }, body: ' '.repeat(5000) })).status, 413);
    assert.equal((await local.post('boost', { headers: auth, json: { enabled: false } })).status, 202);
    const preview = await local.post('boost', { headers: auth, json: { enabled: true, durationMinutes: 30, dryRun: true } });
    assert.equal(preview.status, 200);
    // Also exercise the throwing ky path used by the CLI.
    await assert.rejects(ky.get(`${url}/devices`, { retry: 0 }), error => error instanceof HTTPError && error.response.status === 401);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});

test('application and tests contain no explicit any/unknown, type assertions, or non-null assertions', async () => {
  for await (const path of glob(['src/**/*.ts', 'test/**/*.ts'])) {
    const source = ts.createSourceFile(path, await readFile(path, 'utf8'), ts.ScriptTarget.Latest, true);
    const visit = (node: ts.Node): void => {
      assert.notEqual(node.kind, ts.SyntaxKind.AnyKeyword, `${path}: forbidden broad type`);
      assert.notEqual(node.kind, ts.SyntaxKind.UnknownKeyword, `${path}: forbidden broad type`);
      assert.equal(ts.isAsExpression(node) || ts.isTypeAssertionExpression(node) || ts.isNonNullExpression(node), false, `${path}: unchecked assertion`);
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
});
