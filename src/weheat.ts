import { randomBytes, createHash } from 'node:crypto';
import ky, { HTTPError } from 'ky';
import type { KyInstance } from 'ky';
import { z } from 'zod';
import { AppError } from './errors.js';
import { discoverySchema, installationsSchema, settingsSchema, submissionSchema,
  tokenResponseSchema, upstreamStatusSchema } from './schemas.js';
import type { BoostInput, BoostSettings, Installation, OperationState, Settings, TokenResponse, Tokens } from './schemas.js';
import type { StateStore } from './storage.js';

// Live discovery now advertises the canonical Keycloak URL without /auth.
// The older /auth discovery route still responds, but declares this issuer.
export const ISSUER = 'https://auth.weheat.nl/realms/Weheat';
export const API = 'https://api.weheat.nl/api/v1';
export const CLIENT_ID = 'wh-app-consumer';
export const REDIRECT_URI = 'weheat://auth';
// The app explicitly excludes these discovery routes from its version check.
const VERSION_EXCLUDED_PATHS = new Set(['/users/me', '/installations/fully-expanded']);

// ky retries are globally disabled: timed-out mutations must never be repeated.
export const upstreamHttp = ky.create({ retry: 0, timeout: 15_000, redirect: 'error' });
export type HttpClient = Pick<KyInstance, 'get' | 'post' | 'patch'>;

function endpoint(value: string): string {
  const url = new URL(value);
  const issuer = new URL(ISSUER);
  if (url.origin !== issuer.origin || !url.pathname.startsWith(`${issuer.pathname}/protocol/openid-connect/`) || url.username || url.password || url.search || url.hash) {
    throw new AppError(502, 'Unexpected OIDC endpoint; refusing to send credentials.');
  }
  return url.href;
}

export async function discovery(http: HttpClient = upstreamHttp): Promise<{ authorization: string; token: string }> {
  const result = discoverySchema.parse(await http.get(`${ISSUER}/.well-known/openid-configuration`).json());
  if (result.issuer !== ISSUER) throw new AppError(502, 'Unexpected OIDC issuer.');
  return { authorization: endpoint(result.authorization_endpoint), token: endpoint(result.token_endpoint) };
}

export function loginRequest(authorizationEndpoint: string): { url: string; verifier: string; state: string } {
  const verifier = randomBytes(32).toString('base64url');
  const state = randomBytes(32).toString('base64url');
  const url = new URL(endpoint(authorizationEndpoint));
  url.search = new URLSearchParams({
    client_id: CLIENT_ID, redirect_uri: REDIRECT_URI, response_type: 'code', scope: 'openid profile',
    state, code_challenge_method: 'S256', code_challenge: createHash('sha256').update(verifier).digest('base64url'),
  }).toString();
  return { url: url.href, verifier, state };
}

export function callbackCode(input: string, state: string): string {
  const result = z.url().safeParse(input.trim());
  if (!result.success) throw new AppError(400, 'Expected the complete weheat://auth callback URL.');
  const url = new URL(result.data);
  if (`${url.protocol}//${url.host}${url.pathname}` !== REDIRECT_URI || url.hash || url.username || url.password || url.searchParams.getAll('state').length !== 1 || url.searchParams.get('state') !== state) {
    throw new AppError(400, 'Callback URI or OAuth state did not match.');
  }
  const code = url.searchParams.get('code');
  if (url.searchParams.has('error') || !code || url.searchParams.getAll('code').length !== 1) throw new AppError(400, 'Login did not return one authorization code.');
  return code;
}

export async function exchangeCode(tokenEndpoint: string, code: string, verifier: string, http: HttpClient = upstreamHttp): Promise<TokenResponse> {
  return tokenResponseSchema.parse(await http.post(endpoint(tokenEndpoint), {
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI, code, code_verifier: verifier }),
  }).json());
}

export function tokenRecord(tokens: TokenResponse, previous: Tokens | null = null): Tokens {
  return {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token ?? previous?.refresh_token ?? null,
    expires_at: Date.now() + (tokens.expires_in ?? 60) * 1000,
  };
}

export interface WeheatGateway {
  installations(): Promise<Installation[]>;
  settings(id: string): Promise<Settings>;
  patch(board: string, settings: BoostSettings): Promise<string | null>;
  operation(board: string, id: string): Promise<StatusResult>;
}

export class WeheatClient implements WeheatGateway {
  private refreshing: Promise<Tokens> | null = null;
  constructor(private readonly store: StateStore, private readonly http: HttpClient = upstreamHttp) {}

  private refresh(): Promise<Tokens> {
    if (!this.refreshing) {
      this.refreshing = (async () => {
        const previous = await this.store.readTokens();
        if (!previous?.refresh_token) throw new AppError(401, 'Stop the service and run npm run login.');
        const endpoints = await discovery(this.http);
        const tokens = tokenResponseSchema.parse(await this.http.post(endpoints.token, {
          body: new URLSearchParams({ grant_type: 'refresh_token', client_id: CLIENT_ID, refresh_token: previous.refresh_token }),
        }).json());
        const record = tokenRecord(tokens, previous);
        await this.store.writeTokens(record);
        return record;
      })().finally(() => { this.refreshing = null; });
    }
    return this.refreshing;
  }

  private async request(method: 'GET' | 'PATCH', path: string, body?: BoostSettings): Promise<Response> {
    try {
      let tokens = await this.store.readTokens();
      if (!tokens) throw new AppError(401, 'Not logged in. Stop the service and run npm run login.');
      if (tokens.expires_at < Date.now() + 30_000) tokens = await this.refresh();
      const send = (record: Tokens): Promise<Response> => {
        const headers = {
          Authorization: `Bearer ${record.access_token}`,
          ...(VERSION_EXCLUDED_PATHS.has(path.split('?')[0] ?? path)
            || (method === 'GET' && /^\/installations\/[^/]+\/settings$/.test(path)) ? {} : { 'x-version': '4.5.14' }),
        };
        return method === 'GET' ? this.http.get(`${API}${path}`, { headers })
          : this.http.patch(`${API}${path}`, { headers, json: { settings: body, updateImmediately: false } });
      };
      try { return await send(tokens); }
      catch (error) {
        // Only reads are retried once after explicit 401, never settings writes.
        if (!(error instanceof HTTPError) || error.response.status !== 401 || method !== 'GET') throw error;
        return await send(await this.refresh());
      }
    } catch (error) {
      if (error instanceof AppError) throw error;
      if (error instanceof HTTPError) {
        if (error.response.status === 505) {
          throw new AppError(502, 'Weheat rejected the app/API version (HTTP 505). The bundled 4.5.14 version may no longer be supported for this route. No automatic retry was performed.');
        }
        throw new AppError(error.response.status === 401 ? 401 : 502, `Weheat returned HTTP ${error.response.status}.`);
      }
      throw new AppError(502, 'Upstream request failed. A submitted write may have been accepted; do not blindly retry.');
    }
  }

  async installations(): Promise<Installation[]> {
    return installationsSchema.parse(await (await this.request('GET', '/installations/fully-expanded')).json());
  }

  async settings(id: string): Promise<Settings> {
    return settingsSchema.parse(await (await this.request('GET', `/installations/${encodeURIComponent(id)}/settings`)).json());
  }

  async patch(board: string, settings: BoostSettings): Promise<string | null> {
    const response = submissionSchema.parse(await (await this.request('PATCH', `/control-boards/${encodeURIComponent(board)}/settings`, settings)).json());
    return response.settingsRequestId ?? null;
  }

  async operation(board: string, id: string): Promise<StatusResult> {
    const result = upstreamStatusSchema.parse(await (await this.request('GET', `/control-boards/${encodeURIComponent(board)}/settings/${encodeURIComponent(id)}`)).json());
    return operationStatus(result.status);
  }
}

export function boostSettings(input: BoostInput, now = Date.now()): BoostSettings {
  return { DhwBoostEndTime: String(Math.floor(now / 1000) + (input.enabled ? input.durationMinutes * 60 : 0)) };
}

export interface StatusResult { state: OperationState; terminal: boolean; upstreamStatus: number | null }
const statuses: OperationState[] = [
  'pending', 'executing', 'succeeded', 'cancelled', 'failed', 'succeeded_with_mismatch',
  'failed_device_wrong_state', 'failed_wrong_execution_order', 'failed_device_disconnected',
  'failed_settings_request_mismatch', 'failed_settings_key_not_found', 'failed_another_request_executing',
];
export function operationStatus(status: number | undefined): StatusResult {
  const state = status === undefined ? undefined : statuses[status];
  if (status === undefined || !Number.isInteger(status) || !state) return { state: 'unknown', terminal: false, upstreamStatus: null };
  return { state, terminal: status >= 2, upstreamStatus: status };
}
