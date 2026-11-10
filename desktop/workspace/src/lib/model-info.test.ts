import {describe,expect,it} from 'vitest';
import {configuredModels,formatPrice,formatTokens,modelInfo,modelLabel,providerModels} from './model-info';

const provider={model:'a\nb',model_catalog:{a:{context_window:8192,price_in:0.15,price_out:0.6},c:{}},model_overrides:{a:{context_window:4096}}};

describe('model metadata',()=>{
 it('lets a hand-entered value win over a discovered one',()=>{
  expect(modelInfo(provider,'a')).toMatchObject({context_window:4096,price_in:0.15,price_out:0.6});
 });
 it('reports a model discovery knows nothing about as unknown, never zero',()=>{
  expect(modelInfo(provider,'b')).toEqual({context_window:null,max_output:null,price_in:null,price_out:null});
  expect(modelInfo(undefined,'x').context_window).toBeNull();
  expect(formatTokens(null)).toBe('Unknown');expect(formatTokens(0)).toBe('0 tokens');
  expect(formatPrice(undefined)).toBe('Unknown');expect(formatPrice(0)).toBe('$0 per 1M tokens');
 });
 it('lists the host-reported models, or the selection plus the catalog when the host did not send them',()=>{
  expect(providerModels({...provider,models:['z']})).toEqual(['z']);
  expect(providerModels(provider)).toEqual(['a','b','c']);
  expect(providerModels({})).toEqual([]);
 });
 it('splits the selected models on commas and newlines without blanks or repeats',()=>{
  expect(configuredModels({model:' a, b\n\nb ,'})).toEqual(['a','b']);
  expect(configuredModels({model:null})).toEqual([]);
  expect(configuredModels()).toEqual([]);
 });
 it('labels a model with its context window only when that is known',()=>{
  expect(modelLabel('a',modelInfo(provider,'a'))).toBe('a · 4,096 tokens');
  expect(modelLabel('b',modelInfo(provider,'b'))).toBe('b');
 });
});
