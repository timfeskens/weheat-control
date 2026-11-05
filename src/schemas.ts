import { z } from 'zod';

export const identifierSchema = z.union([z.string().min(1), z.number().int().nonnegative()]).transform(String);
export const tokenResponseSchema = z.object({
  access_token: z.string().min(1),
  refresh_token: z.string().min(1).optional(),
  expires_in: z.number().positive().optional(),
});
export const tokensSchema = z.object({
  access_token: z.string().min(1),
  refresh_token: z.string().min(1).nullable(),
  expires_at: z.number().finite(),
});
export type Tokens = z.infer<typeof tokensSchema>;
export type TokenResponse = z.infer<typeof tokenResponseSchema>;

export const discoverySchema = z.object({
  issuer: z.url(), authorization_endpoint: z.url(), token_endpoint: z.url(),
});
export const installationSchema = z.object({
  id: identifierSchema,
  heatPump: z.object({ id: identifierSchema.optional(), controlBoardId: identifierSchema.optional() }).nullable().optional(),
});
// Candidate response envelopes; still require read-only live verification.
export const installationsSchema = z.union([
  z.array(installationSchema),
  z.object({ data: z.array(installationSchema) }).transform(value => value.data),
]);
export type Installation = z.infer<typeof installationSchema>;

// Settings are intentionally projected to supported fields, not exposed as an
// unrestricted upstream passthrough. Epoch writes in the app use strings.
const epochSchema = z.union([
  z.string().regex(/^\d+$/), z.number().int().nonnegative(),
  z.iso.datetime({ offset: true }).transform(value => Math.floor(Date.parse(value) / 1000)),
]).transform(String);
const settingsBodySchema = z.object({ dhwBoostEndTime: epochSchema.optional() });
export const settingsSchema = z.union([
  z.object({ data: settingsBodySchema }).transform(value => value.data),
  settingsBodySchema,
]);
export type Settings = z.infer<typeof settingsSchema>;

const submissionBodySchema = z.object({ settingsRequestId: identifierSchema.optional() });
export const submissionSchema = z.union([
  z.object({ data: submissionBodySchema }).transform(value => value.data),
  submissionBodySchema,
]);
const statusBodySchema = z.object({ status: z.number().int().optional() });
export const upstreamStatusSchema = z.union([
  z.object({ data: statusBodySchema }).transform(value => value.data), statusBodySchema,
]);

export const boostInputSchema = z.discriminatedUnion('enabled', [
  z.strictObject({ enabled: z.literal(true), durationMinutes: z.number().int().min(1).max(120), dryRun: z.boolean().default(false) }),
  z.strictObject({ enabled: z.literal(false), dryRun: z.boolean().default(false) }),
]);
export type BoostInput = z.infer<typeof boostInputSchema>;
export const boostSettingsSchema = z.object({ DhwBoostEndTime: z.string().regex(/^\d+$/) });
export type BoostSettings = z.infer<typeof boostSettingsSchema>;

export const stateSchema = z.enum([
  'submitting', 'unknown', 'pending', 'executing', 'succeeded', 'cancelled', 'failed',
  'succeeded_with_mismatch', 'failed_device_wrong_state', 'failed_wrong_execution_order',
  'failed_device_disconnected', 'failed_settings_request_mismatch',
  'failed_settings_key_not_found', 'failed_another_request_executing',
]);
export type OperationState = z.infer<typeof stateSchema>;
export const operationSchema = z.object({
  id: z.uuid(), installationId: z.string().min(1), controlBoardId: z.string().min(1),
  settings: boostSettingsSchema, state: stateSchema, terminal: z.boolean(),
  createdAt: z.iso.datetime(), requestId: z.string().min(1).nullable(),
  checkedAt: z.iso.datetime().nullable(), upstreamStatus: z.number().int().nullable(),
  note: z.string().nullable(),
  readback: z.object({
    endTime: z.string().nullable(), active: z.boolean().nullable(),
    matchesRequestedEndTime: z.boolean(), verified: z.boolean(), reason: z.string().nullable(),
  }).nullable(),
});
export type Operation = z.infer<typeof operationSchema>;
export const operationsSchema = z.record(z.string(), operationSchema);
export type Operations = z.infer<typeof operationsSchema>;
export interface Device { installationId: string; controlBoardId: string }
export interface DeviceSummary {
  installationId: string;
  heatPumpId: string | null;
  controlBoardId: string | null;
  selected: boolean;
}
export const previewSchema = z.object({
  dryRun: z.literal(true), installationId: z.string(), controlBoardId: z.string(),
  body: z.object({ settings: boostSettingsSchema, updateImmediately: z.literal(false) }),
});
export type Preview = z.infer<typeof previewSchema>;
export const boostResultSchema = z.union([previewSchema, operationSchema]);
export type BoostResult = z.infer<typeof boostResultSchema>;
