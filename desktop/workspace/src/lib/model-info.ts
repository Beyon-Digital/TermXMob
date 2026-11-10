import type {ModelInfo,ModelOverrides} from './host-types';

/** The parts of a provider the model metadata helpers read. */
export interface ModelSource {
  model?: string | null; models?: string[] | null;
  model_catalog?: Record<string, ModelInfo> | null; model_overrides?: ModelOverrides | null;
}
export type EffectiveInfo = Record<'context_window' | 'max_output' | 'price_in' | 'price_out', number | null>;

const count = new Intl.NumberFormat('en-US');
/** Missing values read "Unknown"; zero is a real value. */
export const formatTokens = (value?: number | null) => value == null ? 'Unknown' : `${count.format(value)} tokens`;
export const formatPrice = (value?: number | null) => value == null ? 'Unknown' : `$${value} per 1M tokens`;

/** Discovered values with the person's own overrides on top. */
export function modelInfo(source: ModelSource | undefined, model: string): EffectiveInfo {
  const found = source?.model_catalog?.[model], mine = source?.model_overrides?.[model];
  const pick = (field: keyof EffectiveInfo) => mine?.[field] ?? found?.[field] ?? null;
  return {context_window: pick('context_window'), max_output: pick('max_output'), price_in: pick('price_in'), price_out: pick('price_out')};
}

/** The comma or newline separated models selected on an account, without blanks or repeats. */
export function configuredModels(source?: Pick<ModelSource, 'model'>): string[] {
  return [...new Set((source?.model || '').split(/[\n,]/).map(model => model.trim()).filter(Boolean))];
}

/** Models a chat can use on an account: the checked ones, or every discovered one when none is singled out ("every model checked"). */
export function chatModels(source?: Pick<ModelSource, 'model' | 'models' | 'model_catalog'>): string[] {
  const chosen = configuredModels(source);
  return chosen.length ? chosen : [...new Set(source?.models?.length ? source.models : Object.keys(source?.model_catalog || {}))];
}

/** Models a route can use: what the host reports, else the selected models plus whatever discovery found. */
export function providerModels(source: ModelSource): string[] {
  if (source.models) return [...source.models];
  return [...new Set([...configuredModels(source), ...Object.keys(source.model_catalog || {})])];
}

/** A model id, with its context window after it when that is known. */
export function modelLabel(id: string, info: EffectiveInfo) {
  return info.context_window == null ? id : `${id} · ${formatTokens(info.context_window)}`;
}
