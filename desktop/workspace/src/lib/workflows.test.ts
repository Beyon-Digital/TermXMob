import {describe, expect, it} from 'vitest';
import {ApiError} from './api';
import {initialWorkflowArgs, parseWorkflowArgs, schemaFields, workflowAccessLost, workflowArgValues, workflowError, workflowRuntimeAvailable, workflowRuntimeReason, workflowStatus, workflowTemplates, type JsonSchema, type WorkflowCapabilities} from './workflows';

const schema: JsonSchema = {
  type: 'object', additionalProperties: false, required: ['request', 'count', 'approved', 'choice'],
  properties: {
    request: {type: 'string', title: 'Request', minLength: 2, maxLength: 12},
    count: {type: 'integer', minimum: 1, maximum: 8, default: 2},
    approved: {type: 'boolean', default: false},
    choice: {type: 'string', enum: ['quick', 'careful'], default: 'careful'},
    note: {type: ['string', 'null']},
    config: {type: 'object', properties: {enabled: {type: 'boolean'}}, additionalProperties: false},
    items: {type: 'array', items: {type: 'number'}, minItems: 1, maxItems: 2},
  },
};
const values = () => ({...initialWorkflowArgs(schema), request: 'Check this'});

describe('workflow input controls', () => {
  it('describes named primitive and structured fields and carries safe defaults', () => {
    const fields = schemaFields(schema)!;
    expect(fields.map(field => [field.key, field.type, field.required])).toEqual([
      ['request', 'string', true], ['count', 'integer', true], ['approved', 'boolean', true], ['choice', 'string', true],
      ['note', 'string', false], ['config', 'object', false], ['items', 'array', false],
    ]);
    expect(fields[0].label).toBe('Request');
    expect(fields[4].nullable).toBe(true);
    expect(initialWorkflowArgs(schema)).toEqual({request: '', count: '2', approved: 'false', choice: '"careful"', note: '', config: '', items: ''});
    expect(parseWorkflowArgs(schema, values())).toEqual({request: 'Check this', count: 2, approved: false, choice: 'careful'});
  });

  it('round trips past primitive, nullable and structured inputs without changing values', () => {
    const args = {request: 'Inspect', count: 3, approved: true, choice: 'quick', note: null, config: {enabled: false}, items: [1.5, 2]};
    const encoded = workflowArgValues(schema, args);
    expect(encoded.choice).toBe('"quick"');
    expect(encoded.note).toBe('null');
    expect(encoded.config).toContain('"enabled": false');
    expect(parseWorkflowArgs(schema, encoded)).toEqual(args);
  });

  it('retains defaults of zero, false, empty strings and explicit null', () => {
    const defaults = {type: 'object', additionalProperties: false, properties: {zero: {type: 'number', default: 0}, off: {type: 'boolean', default: false}, empty: {type: 'string', enum: ['', 'yes'], default: ''}, nullable: {type: ['number', 'null'], default: null}}};
    expect(initialWorkflowArgs(defaults)).toEqual({zero: '0', off: 'false', empty: '""', nullable: 'null'});
    expect(parseWorkflowArgs(defaults, initialWorkflowArgs(defaults))).toEqual({zero: 0, off: false, empty: '', nullable: null});
  });

  it.each([
    ['request', '', 'Request is required'], ['request', 'x', 'at least 2'], ['request', 'A request that is too long', 'at most 12'],
    ['count', '2.5', 'integer'], ['count', '0', 'at least 1'], ['count', '9', 'at most 8'], ['count', 'Infinity', 'whole number'],
    ['count', '1e999', 'finite'], ['count', '0x10', 'whole number'], ['approved', 'yes', 'true or false'], ['choice', '"invented"', 'allowed values'],
    ['config', '{broken', 'valid JSON'], ['config', '{"enabled":"false"}', 'boolean'], ['config', '{"extra":true}', 'not an allowed input'],
    ['items', '[]', 'at least 1 items'], ['items', '[1,2,3]', 'at most 2 items'], ['items', '["one"]', 'number'],
  ])('rejects invalid %s input %s before starting a workflow', (key, value, message) => {
    expect(() => parseWorkflowArgs(schema, {...values(), [key]: value})).toThrow(message);
  });

  it('uses a JSON object editor for open or unfamiliar roots and enforces recognized bounds', () => {
    const open = {type: 'object', minProperties: 1, additionalProperties: {type: 'number', exclusiveMinimum: 0}};
    expect(schemaFields(open)).toBeNull();
    expect(initialWorkflowArgs(open)).toEqual({$json: '{}'});
    expect(parseWorkflowArgs(open, {$json: '{"value":2}'})).toEqual({value: 2});
    expect(() => parseWorkflowArgs(open, {$json: '[]'})).toThrow('JSON object');
    expect(() => parseWorkflowArgs(open, {$json: '{}'})).toThrow('at least 1 properties');
    expect(() => parseWorkflowArgs(open, {$json: '{"value":0}'})).toThrow('greater than 0');
    expect(() => parseWorkflowArgs({type: 'object', $ref: 'https://example.com/schema.json'}, {$json: '{}'})).toThrow('unsupported schema keyword $ref');
  });

  it('distinguishes an input-free closed object from an open root', () => {
    expect(schemaFields({type: 'object', additionalProperties: false})).toEqual([]);
    expect(initialWorkflowArgs({type: 'object', additionalProperties: false})).toEqual({});
    expect(parseWorkflowArgs({type: 'object', additionalProperties: false}, {})).toEqual({});
    expect(schemaFields({type: 'array'})).toBeNull();
  });

  it('compares enum objects structurally and preserves JSON scalar types', () => {
    const mixed = {type: 'object', properties: {choice: {enum: [1, '1', {a: 1, b: 2}]}, exact: {type: 'number', const: 2}}, required: ['choice'], additionalProperties: false};
    expect(parseWorkflowArgs(mixed, {choice: '1'})).toEqual({choice: 1});
    expect(parseWorkflowArgs(mixed, {choice: '"1"'})).toEqual({choice: '1'});
    expect(parseWorkflowArgs(mixed, {choice: '{"b":2,"a":1}', exact: '2'})).toEqual({choice: {b: 2, a: 1}, exact: 2});
    expect(() => parseWorkflowArgs(mixed, {choice: '1', exact: '3'})).toThrow('required value');
  });

  it('supports root defaults and nullable JSON union fields', () => {
    const defaults = {type: 'object', default: {count: 4}, properties: {count: {type: 'integer'}, value: {type: ['string', 'number', 'null']}}, additionalProperties: false};
    expect(initialWorkflowArgs(defaults)).toEqual({count: '4', value: ''});
    expect(parseWorkflowArgs(defaults, {count: '4', value: '"hello"'})).toEqual({count: 4, value: 'hello'});
    expect(parseWorkflowArgs(defaults, {value: 'null'})).toEqual({value: null});
  });

  it('preserves a literal prototype-like field as JSON data', () => {
    const prototype = JSON.parse('{"type":"object","properties":{"__proto__":{"type":"string"}},"required":["__proto__"],"additionalProperties":false}');
    const args = parseWorkflowArgs(prototype, JSON.parse('{"__proto__":"safe"}'));
    expect(Object.hasOwn(args, '__proto__')).toBe(true);
    expect(JSON.stringify(args)).toBe('{"__proto__":"safe"}');
    expect(Object.getPrototypeOf(args)).toBe(Object.prototype);
  });

  it('counts Unicode code points and rejects oversized or deeply nested JSON inputs', () => {
    expect(parseWorkflowArgs({type: 'object', properties: {text: {type: 'string', maxLength: 1}}, additionalProperties: false}, {text: '😀'})).toEqual({text: '😀'});
    expect(() => parseWorkflowArgs({type: 'object'}, {$json: JSON.stringify({text: 'x'.repeat(65536)})})).toThrow('64 KiB');
    const nested = '{"value":' + '['.repeat(34) + '0' + ']'.repeat(34) + '}';
    expect(() => parseWorkflowArgs({type: 'object'}, {$json: nested})).toThrow('supported depth');
    expect(() => parseWorkflowArgs({type: 'object'}, {$json: '{"value":1e999}'})).toThrow('finite JSON');
  });
});

describe('workflow state and failures', () => {
  it('keeps approvals live, stopping distinct from stopped, and unknown states unproven', () => {
    expect(workflowStatus('awaiting_approval')).toMatchObject({label: 'Needs approval', active: true, terminal: false, tone: 'attention'});
    expect(workflowStatus('cancelling')).toMatchObject({label: 'Stopping', active: true, terminal: false});
    expect(workflowStatus('cancelled')).toMatchObject({label: 'Stopped', active: false, terminal: true});
    expect(workflowStatus('needs_attention')).toMatchObject({active: false, terminal: true, tone: 'attention'});
    expect(workflowStatus('completed')).toMatchObject({terminal: true, tone: 'completed'});
    expect(workflowStatus('new_host_state')).toMatchObject({label: 'New host state', active: false, terminal: false, tone: ''});
  });

  it('unwraps structured host errors without showing object dumps', () => {
    expect(workflowError({code: 'invalid_input', message: 'Choose a request.'})).toBe('Choose a request.');
    expect(workflowError(new ApiError(400, '{"detail":{"error":{"code":"changed","message":"Workflow changed; reload it."}}}'))).toBe('Workflow changed; reload it.');
    expect(workflowError(new Error('Host unavailable'))).toBe('Host unavailable');
    expect(workflowError({code: 'unknown'})).toBe('The workflow request failed.');
    const cycle: Record<string, unknown> = {}; cycle.error = cycle;
    expect(workflowError(cycle)).toBe('The workflow request failed.');
    for (const status of [401, 403, 404, 423]) expect(workflowAccessLost(new ApiError(status, 'Unavailable'))).toBe(true);
    for (const status of [400, 409, 500, 503]) expect(workflowAccessLost(new ApiError(status, 'Unavailable'))).toBe(false);
    expect(workflowAccessLost({status: 403})).toBe(false);
  });

  it('requires an affirmative runtime capability and preserves its actual reason', () => {
    const base = {limits: {max_agents: 16, concurrency: 4, timeout_s: 900}};
    const unavailable: WorkflowCapabilities = {...base, runtime: {available: false, reason: 'Install the workflow runtime.'}};
    expect(workflowRuntimeAvailable(unavailable)).toBe(false);
    expect(workflowRuntimeReason(unavailable)).toBe('Install the workflow runtime.');
    expect(workflowRuntimeAvailable({...base, runtime: true})).toBe(true);
    expect(workflowRuntimeAvailable({...base, runtime: {available: true}})).toBe(true);
    expect(workflowRuntimeAvailable({...base, runtime: {}})).toBe(false);
    expect(workflowRuntimeAvailable(null)).toBe(false);
    expect(workflowRuntimeReason({...base, runtime: true})).toBe('');
  });

  it('ships distinct agent orchestration and pure data templates with supported object schemas', () => {
    expect(new Set(workflowTemplates.map(template => template.id)).size).toBe(3);
    for (const template of workflowTemplates) {
      expect(template.input_schema.type).toBe('object');
      expect(schemaFields(template.input_schema)).not.toBeNull();
      expect(template.source).not.toMatch(/run_shell|fetch\(|require\(|import\s|process\./);
    }
    expect(workflowTemplates.find(template => template.id === 'transform')!.source).not.toContain('agent(');
    expect(workflowTemplates.find(template => template.id === 'parallel-audit')!.source).toContain('parallel(');
  });
});
