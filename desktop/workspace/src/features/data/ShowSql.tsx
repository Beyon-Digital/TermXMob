import {useRef, useState} from 'react';
import {Copy} from 'lucide-react';
import {Button} from '../../components/ui/button';
import type {CompiledOk} from './use-spec-run';
import type {SpecError} from '../../lib/query-spec';

/** The generated statement, read-only, with the values that are passed alongside it. */
export default function ShowSql({compile, errors}: {compile?: CompiledOk; errors: SpecError[]}) {
  const [copied, setCopied] = useState(''), timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  async function copy() {
    if (!compile) return;
    try {await navigator.clipboard.writeText(compile.sql); setCopied('Copied.');} catch {setCopied('Copy is not available here. Select the text instead.');}
    clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(''), 2500);
  }
  if (errors.length) return <section className="data-show-sql" aria-label="Generated SQL">
    <p className="data-muted">This view cannot be written yet:</p>
    <ul>{errors.map((error, index) => <li key={index}>{error.message}</li>)}</ul>
  </section>;
  if (!compile) return <section className="data-show-sql" aria-label="Generated SQL"><p className="data-muted">The SQL appears once the view is ready.</p></section>;
  const values = Object.entries(compile.parameters);
  return <section className="data-show-sql" aria-label="Generated SQL">
    <div className="data-section-heading"><h2 className="data-popover-subtitle">Generated SQL (read-only)</h2><Button variant="ghost" size="sm" onClick={() => void copy()}><Copy size={16} strokeWidth={1.5}/>Copy</Button></div>
    <pre className="data-sql-preview" tabIndex={0}>{compile.sql}</pre>
    {copied && <p className="data-muted" role="status">{copied}</p>}
    {values.length > 0 && <dl className="data-sql-values" aria-label="Values">{values.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl>}
  </section>;
}
