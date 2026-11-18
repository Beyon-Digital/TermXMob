import {ChevronRight} from 'lucide-react';

/** The path of the open file, one segment per folder, under the editor tab strip. Segments are text; navigation is the explorer's job. */
export default function Breadcrumbs({path, readOnly = false}: {path: string; readOnly?: boolean}) {
  const segments = path.split('/').filter(Boolean);
  return (
    <nav className="editor-breadcrumbs" aria-label="File path">
      <ol>
        {segments.map((segment, index) => (
          <li key={index} aria-current={index === segments.length - 1 ? 'page' : undefined}>
            {index > 0 && <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" />}
            <span>{segment}</span>
          </li>
        ))}
      </ol>
      {readOnly && <small>Read only</small>}
    </nav>
  );
}
