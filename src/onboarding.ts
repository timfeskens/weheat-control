import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { z } from 'zod';
import { AppError } from './errors.js';
import { saveSelectedEnvironment } from './environment.js';
import type { WeheatGateway } from './weheat.js';
import type { Installation } from './schemas.js';

export interface OnboardingInteraction {
  question(prompt: string): Promise<string>;
  message(text: string): void;
}

export async function openLoginBrowser(url: string): Promise<boolean> {
  const command = process.platform === 'darwin' ? 'open' : process.platform === 'linux' ? 'xdg-open' : null;
  if (!command) return false;
  try {
    // No shell interpolation or protocol handler installation.
    await promisify(execFile)(command, [url], { timeout: 5000 });
    return true;
  } catch { return false; }
}

export async function chooseInstallation(installations: Installation[], interaction: OnboardingInteraction): Promise<Installation> {
  const available = installations.filter(item => item.heatPump?.controlBoardId);
  interaction.message(`✓ Found ${available.length} installation${available.length === 1 ? '' : 's'} with a control board`);
  const single = available[0];
  if (!single) throw new AppError(409, 'No accessible heat-pump control board found. No installation was selected.');
  if (available.length === 1) return single;
  interaction.message('Select the installation to control:');
  for (const [index, item] of available.entries()) {
    interaction.message(`  ${index + 1}. Installation ${item.id} — heat pump ${item.heatPump?.id ?? 'not supplied'} — board ${item.heatPump?.controlBoardId}`);
  }
  while (true) {
    const answer = (await interaction.question(`Choose 1–${available.length} (or q to cancel): `)).trim();
    if (answer.toLowerCase() === 'q') throw new AppError(409, 'Selection cancelled. No installation was saved.');
    const result = z.string().regex(/^[1-9]\d*$/).transform(Number).pipe(z.number().int().min(1).max(available.length)).safeParse(answer);
    const selected = result.success ? available[result.data - 1] : undefined;
    if (selected) return selected;
    interaction.message('Enter one of the listed numbers, or q to cancel.');
  }
}

export async function finishOnboarding(client: Pick<WeheatGateway, 'installations'>, path: string, interaction: OnboardingInteraction): Promise<void> {
  try {
    const selected = await chooseInstallation(await client.installations(), interaction);
    await saveSelectedEnvironment(path, selected.id);
    interaction.message(`✓ Selected installation ${selected.id} and saved .env`);
    interaction.message('✓ Setup complete — controls are ready. No device settings changed during setup.');
    interaction.message('\nStart the API:\n  npm start\n\nPreview a 30-minute boost in another terminal:\n  npm run boost -- preview 30');
  } catch (error) {
    interaction.message('Saved login tokens have been retained. Retry device setup without signing in:\n  npm run login -- --resume');
    if (error instanceof AppError) throw error;
    throw new AppError(502, 'Device discovery or configuration save failed. No settings commands were sent.');
  }
}
