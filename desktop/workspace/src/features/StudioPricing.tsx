import {useEffect, useId, useState} from 'react';
import {ChevronDown, Coins, LoaderCircle} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {loadStudioPricing, pricingSourceUrl, type StudioPricingGuide} from '../lib/studio-pricing';
import './studio-pricing.css';

type Props = {projectId: string | null; ownerId?: string; modelId: string; adapter: 'openrouter' | 'kie'; visible: boolean; expanded?: boolean};
const readable = (value: string) => value.replaceAll('_', ' ');

export default function StudioPricing(props: Props) {
  const [locked, setLocked] = useState(false);
  const [foreground, setForeground] = useState(document.visibilityState !== 'hidden');
  useEffect(() => {
    const lock = () => setLocked(true);
    const unlock = () => setLocked(false);
    const visibility = () => setForeground(document.visibilityState !== 'hidden');
    window.addEventListener('termx-locked', lock);
    window.addEventListener('termx-session-locked', lock);
    window.addEventListener('termx-signed-out', lock);
    window.addEventListener('termx-session-unlocked', unlock);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener('termx-locked', lock);
      window.removeEventListener('termx-session-locked', lock);
      window.removeEventListener('termx-signed-out', lock);
      window.removeEventListener('termx-session-unlocked', unlock);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);
  if (!props.visible || locked || !foreground || !props.modelId) return null;
  return <PricingPanel key={JSON.stringify([props.ownerId || '', props.projectId, props.modelId, props.adapter])} {...props}/>;
}

function PricingPanel({projectId, modelId, adapter, expanded = false}: Props) {
  const [localOpen, setOpen] = useState(false);
  const open = expanded || localOpen;
  const [guide, setGuide] = useState<StudioPricingGuide | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const id = useId();
  useEffect(() => {
    if (!open || adapter !== 'openrouter') return;
    let current = true;
    const controller = new AbortController();
    setLoading(true); setFailed(false); setGuide(null);
    void loadStudioPricing(projectId, modelId, controller.signal).then(value => {
      if (!current || controller.signal.aborted) return;
      if (value.model !== modelId) { setFailed(true); return; }
      setGuide(value);
    }).catch(() => {
      if (current && !controller.signal.aborted) setFailed(true);
    }).finally(() => {
      if (current && !controller.signal.aborted) setLoading(false);
    });
    return () => { current = false; controller.abort(); };
  }, [open, modelId, projectId, adapter, attempt]);
  const source = guide ? pricingSourceUrl(guide.source_url, modelId) : null;
  return <Collapsible open={open} onOpenChange={setOpen} className="studio-pricing">
    {!expanded && <CollapsibleTrigger asChild><Button type="button" variant="ghost" className="pricing-trigger">
      <Coins size={14} aria-hidden="true"/>Published rates<ChevronDown size={14} aria-hidden="true" className={'pricing-chevron' + (open ? ' is-open' : '')}/>
    </Button></CollapsibleTrigger>}
    <CollapsibleContent><section className="pricing-panel" aria-labelledby={id}>
      <h3 id={id} className="pricing-heading">Published unit rates</h3>
      {loading && <p className="pricing-status" role="status"><LoaderCircle size={13} className="pricing-spinner" aria-hidden="true"/>Checking published rates…</p>}
      {adapter === 'kie' && <p className="pricing-status">Rates unavailable.</p>}
      {failed && <div className="pricing-status" role="status"><p>Rates could not be loaded.</p><Button type="button" size="sm" variant="ghost" onClick={() => setAttempt(value => value + 1)}>Retry rates</Button></div>}
      {guide && <>
        {guide.status === 'available' ? <ul className="pricing-rates" aria-label="Published rates by billable unit">{guide.rates.map((rate, index) => <li key={index}>
          <div><strong>{readable(rate.billable)}</strong>{rate.variant && <span className="pricing-variant">{readable(rate.variant)}</span>}<small>{rate.provider}</small></div>
          <span className="pricing-amount">USD {rate.cost_usd}<small>per {rate.unit}</small></span>
        </li>)}</ul> : <p className="pricing-status">Rates unavailable.</p>}
        <footer className="pricing-source"><time dateTime={guide.observed_at}>Checked {new Date(guide.observed_at).toLocaleString()}</time>{source && <a href={source} target="_blank" rel="noopener noreferrer">OpenRouter source</a>}</footer>
      </>}
    </section></CollapsibleContent>
  </Collapsible>;
}
