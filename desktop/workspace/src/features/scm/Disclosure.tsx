import {ChevronRight} from 'lucide-react';
import {useState, type ReactNode} from 'react';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../../components/ui/collapsible';

type DisclosureProps = {title: string; children: ReactNode; defaultOpen?: boolean};

/** A collapsed section of the view: a one-line trigger over a form. */
export function Disclosure({title, children, defaultOpen = false}: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="scm-disclosure">
      <CollapsibleTrigger className="scm-disclosure-trigger">
        <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" className="disclosure-chevron" />
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent data-rail="false" className="scm-disclosure-body">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

/** A label above its control. The label is text, never the placeholder. */
export function Field({label, children}: {label: string; children: ReactNode}) {
  return (
    <label className="scm-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
