import {Input} from '../components/ui/input';
import {formatPrice,formatTokens} from '../lib/model-info';
import type {ModelInfo, ModelOverride, ModelOverrides} from '../lib/host-types';

type OverrideField = keyof ModelOverride;

/** Discovered metadata with the person's own values on top; missing values read "Unknown", never zero. Same rule as `modelInfo`. */
function Meta({info, override}: {info?: ModelInfo; override?: ModelOverride}) {
  const pick = (field: OverrideField) => override?.[field] ?? info?.[field];
  const mark = (field: OverrideField) => override?.[field] != null ? ' (your value)' : '';
  return <p className="model-row-meta">
    Context window {formatTokens(pick('context_window'))}{mark('context_window')} · Max output {formatTokens(pick('max_output'))}{mark('max_output')} · Input {formatPrice(pick('price_in'))}{mark('price_in')} · Output {formatPrice(pick('price_out'))}{mark('price_out')}
  </p>;
}

const fields: [OverrideField, string, string][] = [
  ['context_window', 'Context window (tokens)', '1'], ['max_output', 'Max output (tokens)', '1'],
  ['price_in', 'Input price (USD per 1M tokens)', 'any'], ['price_out', 'Output price (USD per 1M tokens)', 'any'],
];

/**
 * One row per model the host has discovered, plus any selected model discovery no longer lists.
 * An empty `included` list means every discovered model is available.
 */
export default function ProviderModelCatalog({catalog, included, overrides, disabled, onInclude, onOverride}: {
  catalog: Record<string, ModelInfo>; included: string[]; overrides: ModelOverrides; disabled: boolean;
  onInclude: (id: string, checked: boolean) => void; onOverride: (id: string, field: OverrideField, value: number | undefined) => void;
}) {
  const ids = [...new Set([...Object.keys(catalog), ...included])];
  if (!ids.length) return <p className="manager-help">No models yet. Save the account, then choose Refresh models to load what the provider offers.</p>;
  const all = included.length === 0;
  return <ul className="model-rows" aria-label="Discovered models">{ids.map(id => {
    const checked = all || included.includes(id), last = checked && !all && included.length === 1;
    return <li className="model-row" key={id}>
      <label className="model-row-include"><input type="checkbox" name="models" value={id} checked={checked} disabled={disabled || last} onChange={event => onInclude(id, event.target.checked)}/><span className="model-row-id">{id}</span>{!(id in catalog) && <em> · no longer listed by the provider</em>}</label>
      <Meta info={catalog[id]} override={overrides[id]}/>
      <details className="model-row-override"><summary>Override values for {id}</summary>
        <fieldset disabled={disabled}><legend className="visually-hidden">Overrides for {id}</legend>
          {fields.map(([field, label, step]) => <label key={field}>{label}<Input type="number" name={`${field}:${id}`} min={0} step={step} placeholder={catalog[id]?.[field] != null ? String(catalog[id]?.[field]) : 'Unknown'} value={overrides[id]?.[field] ?? ''} onChange={event => onOverride(id, field, event.target.value === '' ? undefined : Number(event.target.value))}/></label>)}
        </fieldset>
      </details>
    </li>;
  })}</ul>;
}
