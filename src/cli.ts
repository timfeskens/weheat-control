import { join } from 'node:path';
import { createInterface } from 'node:readline/promises';
import ky, { HTTPError } from 'ky';
import { z } from 'zod';
import { Store } from './storage.js';
import { config, ROOT, STATE } from './config.js';
import { acquireLock } from './lock.js';
import { AppError, hasErrorCode } from './errors.js';
import { boostInputSchema, boostResultSchema, operationSchema } from './schemas.js';
import { WeheatClient, callbackCode, discovery, exchangeCode, loginRequest, tokenRecord } from './weheat.js';
import { ensureEnvironment } from './environment.js';
import { finishOnboarding, openLoginBrowser } from './onboarding.js';

async function question(prompt: string): Promise<string> {
  if (!process.stdin.isTTY) throw new AppError(400, 'This step requires an interactive terminal; never paste the callback into chat.');
  const readline = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  try { return await readline.question(prompt); }
  finally { readline.close(); }
}

async function main(): Promise<void> {
  const [command, action, duration] = process.argv.slice(2);
  const environmentPath = join(ROOT, '.env');
  if (command === 'setup') {
    console.log(await ensureEnvironment(environmentPath)
      ? 'Created private .env. No credentials requested and no upstream requests made.'
      : '.env already exists; it was not overwritten.');
    return;
  }
  const store = new Store(STATE);
  if (command === 'login' || command === 'devices') {
    const release = await acquireLock(STATE);
    try {
      if (command === 'login') {
        if (action !== undefined && action !== '--resume') throw new AppError(400, 'Usage: npm run login [-- --resume]');
        if (await ensureEnvironment(environmentPath)) console.log('✓ Created private .env with a random local API key');
        if (action === '--resume') {
          if (!await store.readTokens()) throw new AppError(401, 'No saved login. Run npm run login without --resume first.');
          console.log('Using saved login to finish device setup.');
        } else {
          if (!process.stdin.isTTY) throw new AppError(400, 'Login requires an interactive terminal.');
          const endpoints = await discovery();
          const request = loginRequest(endpoints.authorization);
          console.log(`Weheat login URL (fallback if the browser does not open):\n\n${request.url}\n\nAfter login, do NOT launch the mobile app. Copy the weheat://auth link from the protocol-handler prompt. If your browser does not expose it, stop here; do not install a callback handler or weaken OAuth checks.\n`);
          console.log(await openLoginBrowser(request.url) ? 'Opened your default browser.' : 'Could not open the browser automatically. Open the URL above manually.');
          console.log('Callback input is visible and may remain in terminal scrollback. Do not share it.');
          const callback = await question('Paste the complete weheat://auth callback URL: ');
          const tokens = await exchangeCode(endpoints.token, callbackCode(callback, request.state), request.verifier);
          await store.writeTokens(tokenRecord(tokens));
          console.log('✓ Login successful — tokens saved with owner-only permissions');
        }
        await finishOnboarding(new WeheatClient(store), environmentPath, { question, message: text => console.log(text) });
      } else {
        const devices = await new WeheatClient(store).installations();
        console.log(JSON.stringify(devices.map(item => ({ installationId: item.id, heatPumpId: item.heatPump?.id ?? null, controlBoardId: item.heatPump?.controlBoardId ?? null })), null, 2));
      }
    } finally { await release(); }
    return;
  }
  if (command !== 'boost') throw new AppError(400, 'Use setup, login, devices, or boost.');
  const configuration = config();
  if (!action || !['on', 'off', 'preview'].includes(action)) throw new AppError(400, 'Usage: npm run boost -- on 30 | off | preview 30');
  if (action === 'off' && duration !== undefined) throw new AppError(400, 'Off does not accept a duration.');
  const input = boostInputSchema.safeParse(action === 'off'
    ? { enabled: false }
    : { enabled: true, durationMinutes: Number(duration), dryRun: action === 'preview' });
  if (!input.success) throw new AppError(400, 'Specify a duration from 1 to 120 minutes. Example: npm run boost -- on 30');
  const local = ky.create({ prefix: `http://127.0.0.1:${configuration.port}`,
    headers: { Authorization: `Bearer ${configuration.apiKey}` }, retry: 0, timeout: 45_000, redirect: 'error' });
  const operation = boostResultSchema.parse(await local.post('boost', { json: input.data }).json());
  console.log(JSON.stringify(operation, null, 2));
  if ('dryRun' in operation) return;
  if (operation.state === 'unknown') { process.exitCode = 1; return; }
  console.log('Checking every 2 seconds for up to 2 minutes. Timeout does not mean failure.');
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise<void>(resolve => setTimeout(resolve, 2000));
    const status = operationSchema.parse(await local.get(`operations/${operation.id}`).json());
    if (status.terminal || status.state === 'unknown') {
      console.log(JSON.stringify(status, null, 2));
      if (status.state !== 'succeeded' || !status.readback?.matchesRequestedEndTime) process.exitCode = 1;
      return;
    }
  }
  console.log(`Unresolved. GET /operations/${operation.id} to check again. Do not repeat the write.`);
  process.exitCode = 1;
}

main().catch(async error => {
  if (error instanceof AppError) console.error(error.message);
  else if (error instanceof Error && hasErrorCode(error, 'EEXIST')) console.error('.env already exists; it was not overwritten.');
  else if (error instanceof HTTPError) {
    // Only parse safe local API error fields; never print upstream bodies or ky's
    // error object, which can include Authorization headers and token requests.
    if (error.request.url.startsWith('http://127.0.0.1:')) {
      const result = await error.response.json().then(value => z.object({ error: z.string() }).safeParse(value)).catch(() => null);
      console.error(result?.success ? result.data.error : 'Local API request failed. Do not blindly retry a write.');
    } else console.error(`Weheat returned HTTP ${error.response.status}; no automatic retry was performed.`);
  } else console.error('Command failed. Check configuration, state lock, login handoff, or connection. No automatic retry was performed.');
  process.exitCode = 1;
});
