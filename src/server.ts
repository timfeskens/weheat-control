import { Store } from './storage.js';
import { WeheatClient } from './weheat.js';
import { BoostService } from './service.js';
import { config, STATE } from './config.js';
import { createApi } from './http.js';
import { acquireLock } from './lock.js';

const configuration = config();
const release = await acquireLock(STATE);
const store = new Store(STATE);
const service = new BoostService(new WeheatClient(store), store, configuration);
const server = createApi(service, configuration);
let stopping = false;
function shutdown(): void {
  if (stopping) return;
  stopping = true;
  server.close(() => { release().catch(() => { process.exitCode = 1; }); });
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
server.on('error', () => {
  console.error('Could not start local API. Check whether PORT is in use.');
  release().catch(() => {}).finally(() => { process.exitCode = 1; });
});
server.listen(configuration.port, '127.0.0.1', () => {
  console.log(`Weheat API: http://127.0.0.1:${configuration.port}`);
});
