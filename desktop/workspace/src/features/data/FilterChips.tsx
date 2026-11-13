import {useRef} from 'react';
import {Plus, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Select} from '../../components/ui/select';
import {addFilterNode, describeFilter, isFilterGroup, refKey, removeFilterNode, replaceFilterNode, setGroupMode, type FilterContext, type GroupPath} from '../../lib/query-builder';
import type {FilterGroup, FilterLeaf, LeafOp} from '../../lib/query-spec';
import {FilterPopover, type ColumnOption} from './ColumnFilter';

type Props = {
  filters: FilterGroup; context: FilterContext; options: ColumnOption[]; allowedOps?: readonly LeafOp[]; onChange: (filters: FilterGroup) => void;
  /** Disables adding. Existing chips can still be removed. */ readOnly?: boolean;
};

/** Plain-language filter chips with "Match all of these" and "Match any of these" groups. One level of nesting. */
export default function FilterChips({filters, context, options, allowedOps, onChange, readOnly = false}: Props) {
  const focusAdd = useRef<HTMLButtonElement>(null);
  const optionFor = (leaf: FilterLeaf) => options.find(row => refKey(row.ref) === refKey(leaf.field));
  function renderGroup(group: FilterGroup, path: GroupPath) {
    const nested = path.length > 0;
    const addButton = <Button variant="ghost" size="sm" ref={path.length ? undefined : focusAdd} disabled={readOnly}><Plus size={16} strokeWidth={1.5}/>Add filter</Button>;
    return <div className={nested ? 'data-filter-group data-filter-nested' : 'data-filter-group'} role={nested ? 'group' : undefined} aria-label={nested ? 'Filter group ' + (path[0] + 1) : undefined} key={path.join('.')}>
      <div className="data-filter-row">
        {(group.items.length > 1 || nested) && <Select className="data-compact data-match-select" size={1} aria-label={nested ? 'Match mode for group ' + (path[0] + 1) : 'Match mode'} value={group.op} onChange={event => onChange(setGroupMode(filters, path, event.target.value as 'and' | 'or'))}>
          <option value="and">Match all of these</option><option value="or">Match any of these</option></Select>}
        <ul className="data-chip-list">
          {group.items.map((item, index) => isFilterGroup(item)
            ? <li key={index} className="data-chip-group">{renderGroup(item, [...path, index])}<Button variant="ghost" size="sm" aria-label={'Remove group ' + (index + 1)} onClick={() => onChange(removeFilterNode(filters, path, index))}><X size={16} strokeWidth={1.5}/>Remove group</Button></li>
            : (() => {
              const sentence = describeFilter(item, context), option = optionFor(item);
              const remove = () => {onChange(removeFilterNode(filters, path, index)); focusAdd.current?.focus();};
              return <li key={index} className="data-chip">
                <FilterPopover title={'Edit filter: ' + sentence} column={option} columns={options} leaf={item} allowedOps={allowedOps} onApply={leaf => onChange(replaceFilterNode(filters, path, index, leaf))}>
                  <button type="button" className="data-chip-text" disabled={readOnly || !option} onKeyDown={event => {if (event.key === 'Delete' || event.key === 'Backspace') {event.preventDefault(); remove();}}}>{sentence}</button>
                </FilterPopover>
                <button type="button" className="data-chip-remove" aria-label={'Remove filter: ' + sentence} onClick={remove}><X size={16} strokeWidth={1.5}/></button>
              </li>;
            })())}
        </ul>
        {readOnly ? addButton : <FilterPopover title="Add filter" columns={options} allowedOps={allowedOps} onApply={leaf => onChange(addFilterNode(filters, path, leaf))}>{addButton}</FilterPopover>}
        {!nested && !readOnly && filters.items.length > 0 && <Button variant="ghost" size="sm" onClick={() => onChange(addFilterNode(filters, [], {op: 'or', items: []}))}><Plus size={16} strokeWidth={1.5}/>Add group</Button>}
      </div>
    </div>;
  }
  return <div className="data-filters" aria-label="Filters" role="group">{renderGroup(filters, [])}</div>;
}
