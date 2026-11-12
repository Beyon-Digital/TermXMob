import {ApiError} from './api';

export type JsonValue = null | boolean | number | string | JsonValue[] | {[key: string]: JsonValue};
export type JsonSchema = Record<string, unknown>;
export interface WorkflowLimits {max_agents: number | null; concurrency: number | null; timeout_s: number | null}
export const UNLIMITED_WORKFLOW_LIMITS:WorkflowLimits = {max_agents:null,concurrency:null,timeout_s:null};
export interface WorkflowError {code: string; message: string; [key: string]: unknown}
export interface WorkflowSnapshot {
  name: string; description: string; language: 'javascript'; source: string;
  input_schema: JsonSchema; limits: WorkflowLimits;
}
export interface WorkflowDefinition extends WorkflowSnapshot {
  id: string; revision: number; project_id: string | null; archived: boolean;
  created_at: number; updated_at: number; allowed_actions?: string[];
}
export interface WorkflowCall {
  id: string; sequence: number; operation: 'agent' | 'phase' | 'log'; name: string; status: string;
  session_id: string | null; task_id: string | null; output: JsonValue; error: WorkflowError | null;
  started_at: number | null; finished_at: number | null;
}
export interface WorkflowRun {
  id: string; workflow_id: string | null; workflow_revision: number | null; name: string; status: string;
  args: Record<string, JsonValue>; project_id: string | null; parent_session_id: string | null;
  snapshot: WorkflowSnapshot; limits: WorkflowLimits; calls: WorkflowCall[]; call_count?:number;calls_truncated?:boolean;
  result: JsonValue; error: WorkflowError | null; allowed_actions: string[];
  created_at: number; updated_at: number; finished_at: number | null;
  events?: {sequence?: number; type: string; payload?: Record<string, unknown>}[];
}
export interface WorkflowCapabilities {
  runtime: boolean | {available?: boolean; reason?: string; [key: string]: unknown};
  limits: WorkflowLimits; helpers?: string[]; agent_only?: boolean; automatic_replay?: boolean;
  [key: string]: unknown;
}
export interface WorkflowValidation {
  valid: boolean | null; unsupported?: boolean; error?: WorkflowError | null;
  input_schema?: JsonSchema; limits?: WorkflowLimits;
}

export function workflowStatus(status = '') {
  const labels: Record<string, string> = {queued: 'Queued', running: 'Running', awaiting_approval: 'Needs approval', cancelling: 'Stopping', completed: 'Completed', failed: 'Failed', cancelled: 'Stopped', needs_attention: 'Needs attention'};
  return {
    label: labels[status] || (status ? status.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase()) : 'Unknown'),
    active: ['queued', 'running', 'awaiting_approval', 'cancelling'].includes(status),
    terminal: ['completed', 'failed', 'cancelled', 'needs_attention'].includes(status),
    tone: status === 'completed' ? 'completed' : status === 'failed' ? 'failed' : ['awaiting_approval', 'needs_attention'].includes(status) ? 'attention' : ['queued', 'running', 'cancelling'].includes(status) ? 'running' : '',
  };
}

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function errorMessage(value: unknown, depth = 0): string {
  if (depth > 8) return '';
  if (typeof value === 'string') {
    const message = value.trim();
    if (!message) return '';
    if (message.startsWith('{') || message.startsWith('[')) {try {return errorMessage(JSON.parse(message), depth + 1);} catch { /* A host's plain message can begin with punctuation. */ }}
    return message;
  }
  if (value instanceof Error) return errorMessage(value.message, depth + 1);
  if (object(value)) {
    for (const key of ['message', 'detail', 'error', 'reason']) {const nested = errorMessage(value[key], depth + 1); if (nested) return nested;}
  }
  return '';
}
export function workflowError(error: unknown) {return errorMessage(error) || 'The workflow request failed.';}
export function workflowAccessLost(error: unknown) {return error instanceof ApiError && [401, 403, 404, 423].includes(error.status);}
export function workflowRuntimeAvailable(capabilities?: WorkflowCapabilities | null) {
  return typeof capabilities?.runtime === 'boolean' ? capabilities.runtime : capabilities?.runtime?.available === true;
}
export function workflowRuntimeReason(capabilities?: WorkflowCapabilities | null) {
  return object(capabilities?.runtime) && typeof capabilities.runtime.reason === 'string' ? capabilities.runtime.reason : workflowRuntimeAvailable(capabilities) ? '' : 'JavaScript workflow runtime is unavailable.';
}

export type WorkflowFieldType = 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array' | 'json';
export interface WorkflowSchemaField {
  key: string; label: string; description: string; type: WorkflowFieldType; required: boolean; nullable: boolean;
  schema: JsonSchema; enum?: JsonValue[]; default?: JsonValue;
  minimum?: number; maximum?: number; minLength?: number; maxLength?: number;
}
const types = new Set(['object', 'array', 'string', 'integer', 'number', 'boolean', 'null']);
const keywords = new Set(['type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const', 'minLength', 'maxLength', 'minItems', 'maxItems', 'minProperties', 'maxProperties', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'title', 'description', 'default']);
function jsonValue(value: unknown, depth = 0): value is JsonValue {
  if (depth > 32) return false;
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(item => jsonValue(item, depth + 1));
  return object(value) && Object.values(value).every(item => jsonValue(item, depth + 1));
}
function declaredTypes(schema: JsonSchema): string[] {
  const value = schema.type;
  return typeof value === 'string' ? [value] : Array.isArray(value) && value.every(type => typeof type === 'string') ? value : [];
}
/** Only ordinary object properties become controls. Open or unfamiliar roots retain a JSON editor. */
export function schemaFields(schema: JsonSchema): WorkflowSchemaField[] | null {
  if (!object(schema) || schema.type !== 'object' || Object.keys(schema).some(key => !keywords.has(key))) return null;
  if (!object(schema.properties)) return schema.additionalProperties === false ? [] : null;
  if (!Object.keys(schema.properties).length && schema.additionalProperties !== false) return null;
  const required = Array.isArray(schema.required) ? schema.required : [];
  const fields: WorkflowSchemaField[] = [];
  for (const [key, value] of Object.entries(schema.properties)) {
    if (!object(value)) return null;
    const kinds = declaredTypes(value), concrete = kinds.filter(kind => kind !== 'null');
    const type: WorkflowFieldType = concrete.length === 1 && types.has(concrete[0]) ? concrete[0] as WorkflowFieldType : 'json';
    const field: WorkflowSchemaField = {key, label: typeof value.title === 'string' ? value.title : key, description: typeof value.description === 'string' ? value.description : '', type, required: required.includes(key), nullable: kinds.includes('null'), schema: value};
    if (Array.isArray(value.enum) && value.enum.every(item => jsonValue(item))) field.enum = value.enum;
    if (Object.hasOwn(value, 'default') && jsonValue(value.default)) field.default = value.default;
    for (const bound of ['minimum', 'maximum', 'minLength', 'maxLength'] as const) if (typeof value[bound] === 'number') field[bound] = value[bound];
    fields.push(field);
  }
  return fields;
}
function encodeValue(field: WorkflowSchemaField, value: JsonValue) {
  return field.enum || value === null || field.type === 'object' || field.type === 'array' || field.type === 'json' ? JSON.stringify(value, null, 2) : String(value);
}
/** Encodes past arguments exactly as the input controls expect. Enum values always use JSON. */
export function workflowArgValues(schema: JsonSchema, args: Record<string, JsonValue>): Record<string, string> {
  const fields = schemaFields(schema);
  if (fields === null) return {$json: JSON.stringify(args, null, 2)};
  return Object.fromEntries(fields.map(field => [field.key, Object.hasOwn(args, field.key) ? encodeValue(field, args[field.key]) : '']));
}
export function initialWorkflowArgs(schema: JsonSchema): Record<string, string> {
  const fields = schemaFields(schema);
  const rootDefault = object(schema.default) && jsonValue(schema.default) ? schema.default as Record<string, JsonValue> : {};
  if (fields === null) return {$json: JSON.stringify(rootDefault, null, 2)};
  return Object.fromEntries(fields.map(field => [field.key, field.default !== undefined ? encodeValue(field, field.default) : Object.hasOwn(rootDefault, field.key) ? encodeValue(field, rootDefault[field.key]) : '']));
}
function equal(left: JsonValue, right: JsonValue): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((item, index) => equal(item, right[index]));
  if (object(left) && object(right)) return Object.keys(left).length === Object.keys(right).length && Object.keys(left).every(key => Object.hasOwn(right, key) && equal(left[key] as JsonValue, right[key] as JsonValue));
  return false;
}
function hasType(value: JsonValue, type: string) {
  if (type === 'null') return value === null;
  if (type === 'array') return Array.isArray(value);
  if (type === 'object') return object(value);
  if (type === 'integer') return typeof value === 'number' && Number.isInteger(value);
  return typeof value === type;
}
/** A small local validation pass; the host remains authoritative for schema and execution limits. */
function validateValue(value: JsonValue, schema: JsonSchema, label: string, depth = 0): void {
  if (depth > 32) throw new Error('Inputs exceed the supported nesting depth.');
  const unsupported = Object.keys(schema).find(key => !keywords.has(key));
  if (unsupported) throw new Error(`${label}: unsupported schema keyword ${unsupported}.`);
  const kinds = declaredTypes(schema);
  if (kinds.length && !kinds.some(type => hasType(value, type))) throw new Error(`${label} must be ${kinds.join(' or ')}.`);
  if (Array.isArray(schema.enum) && !schema.enum.some(choice => jsonValue(choice) && equal(value, choice))) throw new Error(`${label} must be one of the allowed values.`);
  if (Object.hasOwn(schema, 'const') && jsonValue(schema.const) && !equal(value, schema.const)) throw new Error(`${label} must match the required value.`);
  if (typeof value === 'number') {
    for (const [key, invalid, text] of [
      ['minimum', (bound: number) => value < bound, 'at least'], ['maximum', (bound: number) => value > bound, 'at most'],
      ['exclusiveMinimum', (bound: number) => value <= bound, 'greater than'], ['exclusiveMaximum', (bound: number) => value >= bound, 'less than'],
    ] as const) if (typeof schema[key] === 'number' && invalid(schema[key])) throw new Error(`${label} must be ${text} ${schema[key]}.`);
  } else if (typeof value === 'string') {
    const length = [...value].length;
    if (typeof schema.minLength === 'number' && length < schema.minLength) throw new Error(`${label} must contain at least ${schema.minLength} characters.`);
    if (typeof schema.maxLength === 'number' && length > schema.maxLength) throw new Error(`${label} must contain at most ${schema.maxLength} characters.`);
  } else if (Array.isArray(value)) {
    if (typeof schema.minItems === 'number' && value.length < schema.minItems) throw new Error(`${label} must contain at least ${schema.minItems} items.`);
    if (typeof schema.maxItems === 'number' && value.length > schema.maxItems) throw new Error(`${label} must contain at most ${schema.maxItems} items.`);
    if (object(schema.items)) value.forEach((item, index) => validateValue(item, schema.items as JsonSchema, `${label}[${index}]`, depth + 1));
  } else if (object(value)) {
    const entries = Object.entries(value);
    if (typeof schema.minProperties === 'number' && entries.length < schema.minProperties) throw new Error(`${label} must contain at least ${schema.minProperties} properties.`);
    if (typeof schema.maxProperties === 'number' && entries.length > schema.maxProperties) throw new Error(`${label} must contain at most ${schema.maxProperties} properties.`);
    if (Array.isArray(schema.required)) for (const key of schema.required) if (typeof key === 'string' && !Object.hasOwn(value, key)) throw new Error(`${label}: ${key} is required.`);
    const properties = object(schema.properties) ? schema.properties : {};
    for (const [key, item] of entries) {
      if (object(properties[key])) validateValue(item, properties[key], `${label}.${key}`, depth + 1);
      else if (schema.additionalProperties === false) throw new Error(`${label}.${key} is not an allowed input.`);
      else if (object(schema.additionalProperties)) validateValue(item, schema.additionalProperties, `${label}.${key}`, depth + 1);
    }
  }
}
function parseJson(raw: string, label: string): JsonValue {
  let value: unknown;
  try {value = JSON.parse(raw);} catch {throw new Error(`${label} must contain valid JSON.`);}
  if (!jsonValue(value)) throw new Error(`${label} must contain finite JSON values within the supported depth.`);
  return value;
}
export function parseWorkflowArgs(schema: JsonSchema, values: Record<string, string>): Record<string, JsonValue> {
  const fields = schemaFields(schema);
  let args: JsonValue;
  if (fields === null) args = parseJson(values.$json || '{}', 'Inputs');
  else args = Object.fromEntries(fields.flatMap(field => {
    const raw = values[field.key] ?? '';
    if (!raw.trim()) {if (field.required) throw new Error(`${field.label} is required.`); return [];}
    let value: JsonValue;
    if (field.enum || field.type === 'object' || field.type === 'array' || field.type === 'json' || field.nullable && raw.trim() === 'null') value = parseJson(raw, field.label);
    else if (field.type === 'boolean') {
      if (!['true', 'false'].includes(raw.trim())) throw new Error(`${field.label} must be true or false.`);
      value = raw.trim() === 'true';
    } else if (field.type === 'number' || field.type === 'integer') {
      if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(raw.trim())) throw new Error(`${field.label} must be a ${field.type === 'integer' ? 'whole number' : 'number'}.`);
      value = Number(raw);
      if (!Number.isFinite(value)) throw new Error(`${field.label} must be a finite number.`);
    } else value = raw;
    validateValue(value, field.schema, field.label);
    return [[field.key, value] as const];
  }));
  if (!object(args)) throw new Error('Inputs must be a JSON object.');
  if (new TextEncoder().encode(JSON.stringify(args)).length > 65_536) throw new Error('Inputs exceed 64 KiB.');
  validateValue(args, schema, 'Inputs');
  return args as Record<string, JsonValue>;
}

export interface WorkflowTemplate {id: string; name: string; description: string; source: string; input_schema: JsonSchema}
export const workflowTemplates: WorkflowTemplate[] = [
  {id: 'review', name: 'Review and refine', description: 'Review a request, then ask another agent to refine the findings.', input_schema: {type: 'object', properties: {request: {type: 'string', minLength: 1}}, required: ['request'], additionalProperties: false}, source: `const review = await phase("Review", () => agent({
  name: "Reviewer",
  prompt: "Review this request and identify risks and concrete next steps:\\n" + args.request
}));
return await phase("Refine", () => agent({
  name: "Editor",
  prompt: "Refine these findings into a concise, actionable response:\\n" + review.output
}));`},
  {id: 'parallel-audit', name: 'Parallel audit', description: 'Inspect a request from several angles and combine the actual findings.', input_schema: {type: 'object', properties: {request: {type: 'string', minLength: 1}, perspectives: {type: 'array', items: {type: 'string', minLength: 1}, minItems: 1, maxItems: 8}}, required: ['request', 'perspectives'], additionalProperties: false}, source: `const findings = await phase("Independent reviews", () =>
  parallel(args.perspectives, async (perspective) => agent({
    name: perspective,
    prompt: "Review this request from the perspective of " + perspective + ":\\n" + args.request
  }))
);
return await phase("Combine findings", () => agent({
  name: "Coordinator",
  prompt: "Combine these independent findings, resolve conflicts, and state remaining uncertainties:\\n" +
    findings.map((finding, index) => args.perspectives[index] + ":\\n" + finding.output).join("\\n\\n")
}));`},
  {id: 'transform', name: 'Transform data', description: 'Filter, transform and summarize input values using JavaScript.', input_schema: {type: 'object', properties: {values: {type: 'array', items: {type: 'number'}, maxItems: 1000}, minimum: {type: 'number'}}, required: ['values', 'minimum'], additionalProperties: false}, source: `return await phase("Summarize values", async () => {
  const selected = args.values.filter(value => value >= args.minimum);
  log("Selected values", {count: selected.length});
  return {
    count: selected.length,
    sum: selected.reduce((total, value) => total + value, 0),
    values: selected
  };
});`},
];
