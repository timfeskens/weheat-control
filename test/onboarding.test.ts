import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseEnv } from 'node:util';
import ky from 'ky';
import { ensureEnvironment, saveSelectedEnvironment, selectedEnvironment } from '../src/environment.js';
import { chooseInstallation, finishOnboarding } from '../src/onboarding.js';
import type { OnboardingInteraction } from '../src/onboarding.js';
import type { Installation } from '../src/schemas.js';
import { Store } from '../src/storage.js';
import { API, WeheatClient } from '../src/weheat.js';

const eligible: Installation = { id: 'installation-1', heatPump: { id: 'pump-1', controlBoardId: 'board-1' } };
const other: Installation = { id: 'installation-2', heatPump: { controlBoardId: 'board-2' } };
const environment = '# Private configuration\nWEHEAT_LOCAL_API_KEY=unchanged-key\nWEHEAT_INSTALLATION_ID=old # selection\nWEHEAT_ALLOW_WRITES=true # safety\nPORT=9000\nEXTRA="keep # this"\n';

function interaction(answers: string[] = []): OnboardingInteraction & { messages: string[]; prompts: string[] } {
  const messages: string[] = [];
  const prompts: string[] = [];
  return {
    messages, prompts,
    message(text) { messages.push(text); },
    async question(prompt) {
      prompts.push(prompt);
      const answer = answers.shift();
      assert.notEqual(answer, undefined, 'Unexpected interactive question');
      return answer ?? '';
    },
  };
}

async function temporaryEnvironment(action: (path: string, directory: string) => Promise<void>): Promise<void> {
  const directory = await mkdtemp(join(tmpdir(), 'weheat-onboarding-'));
  try { await action(join(directory, '.env'), directory); }
  finally { await rm(directory, { recursive: true }); }
}

test('automatically select the sole installation with a control board without prompting', async () => {
  const ui = interaction();
  assert.deepEqual(await chooseInstallation([
    { id: 'no-pump' }, { id: 'null-pump', heatPump: null },
    { id: 'no-board', heatPump: {} }, eligible,
  ], ui), eligible);
  assert.equal(ui.prompts.length, 0);
  assert.ok(ui.messages.some(text => text.includes('Found 1 installation')));
});

test('multiple installations require a valid numbered choice and retry invalid answers', async () => {
  const ui = interaction(['0', '3', '1.5', '1e0', '', ' 2 ']);
  assert.deepEqual(await chooseInstallation([eligible, other], ui), other);
  assert.equal(ui.prompts.length, 6);
  assert.equal(ui.messages.filter(text => text.includes('Enter one')).length, 5);
  assert.ok(ui.messages.some(text => text.includes('board-2')));
});

test('selection can be cancelled and no eligible installation fails clearly', async () => {
  await assert.rejects(chooseInstallation([eligible, other], interaction([' Q '])), /cancelled/);
  await assert.rejects(chooseInstallation([{ id: 'no-board' }], interaction()), /No accessible/);
  await assert.rejects(chooseInstallation([], interaction()), /No accessible/);
});

test('environment update preserves unrelated values, comments, and exported settings', () => {
  const original = environment.replace('WEHEAT_INSTALLATION_ID=old', '  export WEHEAT_INSTALLATION_ID = "old"');
  const updated = selectedEnvironment(original, eligible.id);
  assert.equal(updated, original
    .replace('"old" # selection', 'installation-1 # selection'));
  assert.deepEqual(parseEnv(updated), {
    WEHEAT_LOCAL_API_KEY: 'unchanged-key', WEHEAT_INSTALLATION_ID: eligible.id,
    WEHEAT_ALLOW_WRITES: 'true', PORT: '9000', EXTRA: 'keep # this',
  });
});

test('environment update retains CRLF and appends missing settings safely', () => {
  const original = '# comment\r\nWEHEAT_LOCAL_API_KEY=preserved';
  assert.equal(selectedEnvironment(original, eligible.id), `${original}\r\nWEHEAT_INSTALLATION_ID=installation-1\r\n`);
  assert.equal(selectedEnvironment('', eligible.id), 'WEHEAT_INSTALLATION_ID=installation-1\n');
  const crlf = environment.replaceAll('\n', '\r\n');
  assert.equal(selectedEnvironment(crlf, eligible.id), selectedEnvironment(environment, eligible.id).replaceAll('\n', '\r\n'));
});

test('environment update rejects duplicate keys, multiline control values, and unsafe IDs', () => {
  for (const text of [
    `${environment}WEHEAT_INSTALLATION_ID=duplicate\n`,
    'WEHEAT_INSTALLATION_ID="first\nsecond"\n',
    'UNRELATED="first\nWEHEAT_INSTALLATION_ID=old\nlast"\n',
  ]) assert.throws(() => selectedEnvironment(text, eligible.id));
  for (const id of ['', 'id\nINJECT=true', 'id #comment', 'id"', 'id=oops']) {
    assert.throws(() => selectedEnvironment(environment, id));
  }
  const multiline = 'UNRELATED="first\nsecond"\n';
  assert.equal(parseEnv(selectedEnvironment(multiline, eligible.id)).UNRELATED, 'first\nsecond');
});

test('environment creation is private, idempotent, and does not replace the API key', async () => {
  await temporaryEnvironment(async path => {
    assert.equal(await ensureEnvironment(path), true);
    const created = await readFile(path, 'utf8');
    assert.match(parseEnv(created).WEHEAT_LOCAL_API_KEY ?? '', /^[a-f0-9]{64}$/);
    assert.equal(parseEnv(created).WEHEAT_ALLOW_WRITES, undefined);
    assert.equal((await stat(path)).mode & 0o777, 0o600);
    assert.equal(await ensureEnvironment(path), false);
    assert.equal(await readFile(path, 'utf8'), created);
  });
});

test('selection is saved privately with no temporary files and is idempotent', async () => {
  await temporaryEnvironment(async (path, directory) => {
    await writeFile(path, environment, { mode: 0o600 });
    await saveSelectedEnvironment(path, eligible.id);
    assert.equal(await readFile(path, 'utf8'), selectedEnvironment(environment, eligible.id));
    assert.equal((await stat(path)).mode & 0o777, 0o600);
    assert.deepEqual(await readdir(directory), ['.env']);
    await saveSelectedEnvironment(path, eligible.id);
    assert.equal(await readFile(path, 'utf8'), selectedEnvironment(environment, eligible.id));
  });
});

test('unsafe environment files are not overwritten and symlinks are refused', async () => {
  await temporaryEnvironment(async (path, directory) => {
    const invalid = `${environment}WEHEAT_INSTALLATION_ID=duplicate\n`;
    await writeFile(path, invalid);
    await assert.rejects(saveSelectedEnvironment(path, eligible.id));
    assert.equal(await readFile(path, 'utf8'), invalid);
    assert.deepEqual(await readdir(directory), ['.env']);
    const link = join(directory, 'link');
    await symlink(path, link);
    await assert.rejects(saveSelectedEnvironment(link, eligible.id), /symbolic link/);
    assert.equal(await readFile(path, 'utf8'), invalid);
  });
});

test('onboarding discovers devices using only GET, saves selection, and prints next commands', async () => {
  await temporaryEnvironment(async (path, directory) => {
    await writeFile(path, environment, { mode: 0o600 });
    const store = new Store(directory);
    const tokens = { access_token: 'fake-access', refresh_token: 'fake-refresh', expires_at: Date.now() + 300_000 };
    await store.writeTokens(tokens);
    let calls = 0;
    const client = new WeheatClient(store, ky.create({
      retry: 0, fetch: async input => {
        const request = new Request(input);
        calls++;
        assert.equal(request.method, 'GET');
        assert.equal(request.url, `${API}/installations/fully-expanded`);
        assert.equal(request.headers.has('x-version'), false);
        return Response.json({ data: [eligible] });
      },
    }));
    const ui = interaction();
    await finishOnboarding(client, path, ui);
    assert.equal(calls, 1);
    assert.equal(ui.prompts.length, 0);
    assert.deepEqual(await store.readTokens(), tokens);
    assert.equal(parseEnv(await readFile(path, 'utf8')).WEHEAT_INSTALLATION_ID, eligible.id);
    assert.equal(parseEnv(await readFile(path, 'utf8')).WEHEAT_ALLOW_WRITES, 'true');
    assert.ok(ui.messages.some(text => text.includes('npm start') && text.includes('npm run boost -- preview 30')));
    assert.ok(ui.messages.some(text => text.includes('controls are ready')));
  });
});

test('failed discovery retains saved tokens and configuration, then resumes successfully', async () => {
  await temporaryEnvironment(async (path, directory) => {
    await writeFile(path, environment, { mode: 0o600 });
    const store = new Store(directory);
    const tokens = { access_token: 'fake-access', refresh_token: 'fake-refresh', expires_at: Date.now() + 300_000 };
    await store.writeTokens(tokens);
    let failing = true;
    const client = new WeheatClient(store, ky.create({
      retry: 0, fetch: async input => {
        assert.equal(new Request(input).method, 'GET');
        return failing ? Response.json({}, { status: 503 }) : Response.json({ data: [eligible] });
      },
    }));
    const ui = interaction();
    await assert.rejects(finishOnboarding(client, path, ui));
    assert.deepEqual(await store.readTokens(), tokens);
    assert.equal(await readFile(path, 'utf8'), environment);
    assert.ok(ui.messages.some(text => text.includes('npm run login -- --resume')));
    failing = false;
    await finishOnboarding(client, path, ui);
    assert.equal(parseEnv(await readFile(path, 'utf8')).WEHEAT_INSTALLATION_ID, eligible.id);
    assert.deepEqual(await store.readTokens(), tokens);
  });
});

test('cancelled selection and configuration-save failures retain the prior configuration', async () => {
  await temporaryEnvironment(async path => {
    await writeFile(path, environment, { mode: 0o600 });
    const ui = interaction(['q']);
    await assert.rejects(finishOnboarding({ installations: async () => [eligible, other] }, path, ui), /cancelled/);
    assert.equal(await readFile(path, 'utf8'), environment);
    assert.ok(ui.messages.some(text => text.includes('--resume')));
    const invalid = `${environment}WEHEAT_INSTALLATION_ID=duplicate\n`;
    await writeFile(path, invalid);
    await assert.rejects(finishOnboarding({ installations: async () => [eligible] }, path, interaction()), /Duplicate/);
    assert.equal(await readFile(path, 'utf8'), invalid);
  });
});
