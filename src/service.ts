import { randomUUID } from 'node:crypto';
import { AppError } from './errors.js';
import { boostSettings } from './weheat.js';
import type { WeheatGateway } from './weheat.js';
import type { StateStore } from './storage.js';
import type { Config } from './config.js';
import type { BoostInput, BoostResult, Device, DeviceSummary, Operation, Settings } from './schemas.js';

export class BoostService {
  private busy = false;
  // Serialize operation refreshes and writes so file snapshots cannot lose updates.
  private queue: Promise<void> = Promise.resolve();
  constructor(private readonly client: WeheatGateway, private readonly store: StateStore, private readonly config: Config) {}

  private serialized<T>(action: () => Promise<T>): Promise<T> {
    const result = this.queue.then(action);
    this.queue = result.then(() => {}, () => {});
    return result;
  }

  async devices(): Promise<DeviceSummary[]> {
    return (await this.client.installations()).map(item => ({
      installationId: item.id, heatPumpId: item.heatPump?.id ?? null,
      controlBoardId: item.heatPump?.controlBoardId ?? null,
      selected: item.id === this.config.installationId,
    }));
  }

  private async selected(): Promise<Device> {
    if (!this.config.installationId) throw new AppError(409, 'Select WEHEAT_INSTALLATION_ID in .env after running npm run devices.');
    const installation = (await this.client.installations()).find(item => item.id === this.config.installationId);
    const board = installation?.heatPump?.controlBoardId;
    if (!installation || !board) throw new AppError(409, 'Selected installation does not contain an accessible control board.');
    return { installationId: installation.id, controlBoardId: board };
  }

  async settings(): Promise<Settings> {
    return this.client.settings((await this.selected()).installationId);
  }

  operation(id: string): Promise<Operation> {
    return this.serialized(async () => {
      const operations = await this.store.readOperations();
      const operation = operations[id];
      if (!operation || !Object.hasOwn(operations, id)) throw new AppError(404, 'Operation not found.');
      if (operation.terminal || !operation.requestId) return operation;
      const result = await this.client.operation(operation.controlBoardId, operation.requestId);
      Object.assign(operation, result, { checkedAt: new Date().toISOString() });
      if (result.state === 'succeeded' || result.state === 'succeeded_with_mismatch') {
        try {
          const settings = await this.client.settings(operation.installationId);
          const value = settings.dhwBoostEndTime;
          operation.readback = {
            endTime: value ?? null,
            active: value === undefined ? null : Number(value) > Math.floor(Date.now() / 1000),
            matchesRequestedEndTime: value !== undefined && Number(value) === Number(operation.settings.DhwBoostEndTime),
            verified: value !== undefined,
            reason: value === undefined ? 'Boost end time not found in known readback shape.' : null,
          };
        } catch {
          operation.readback = { endTime: null, active: null, matchesRequestedEndTime: false, verified: false, reason: 'Settings readback failed.' };
        }
      }
      operations[id] = operation;
      await this.store.writeOperations(operations);
      return operation;
    });
  }

  async boost(input: BoostInput): Promise<BoostResult> {
    if (this.busy) throw new AppError(409, 'A boost submission is already in progress.');
    this.busy = true;
    try {
      return await this.serialized(async () => {
        const device = await this.selected();
        const settings = boostSettings(input);
        if (input.dryRun) return { dryRun: true, ...device, body: { settings, updateImmediately: false } };
        const operations = await this.store.readOperations();
        const pending = Object.values(operations).find(item => item.controlBoardId === device.controlBoardId && !item.terminal);
        if (pending) throw new AppError(409, `Previous operation ${pending.id} is unresolved. Check its status before submitting another write.`);
        const id = randomUUID();
        const operation: Operation = {
          id, ...device, settings, state: 'submitting', terminal: false,
          createdAt: new Date().toISOString(), checkedAt: null, upstreamStatus: null,
          requestId: null, note: null, readback: null,
        };
        operations[id] = operation;
        await this.store.writeOperations(operations);
        try {
          operation.requestId = await this.client.patch(device.controlBoardId, settings);
          operation.state = operation.requestId ? 'pending' : 'unknown';
          if (!operation.requestId) operation.note = 'Write returned without a recognized request ID. Do not retry; inspect upstream history and settings.';
        } catch {
          operation.state = 'unknown';
          operation.note = 'Submission outcome is uncertain. Do not retry; inspect upstream history and settings.';
        }
        await this.store.writeOperations(operations);
        return operation;
      });
    } finally { this.busy = false; }
  }
}
