import {useId, useRef} from 'react';
import {Plus, TriangleAlert, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Select} from '../../components/ui/select';
import {addFilterNode, describeFilter, isFilterGroup, refKey, removeFilterNode, replaceFilterNode, setGroupMode, type FilterContext, type GroupPath} from '../../lib/query-builder';
import {isFilterLeaf, isSegmentFilter, type FilterGroup, type FilterLeaf, type LeafOp} from '../../lib/query-spec';
import {describeSegmentNode} from '../../lib/segments';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';
import {FilterPopover, type ColumnOption} from './ColumnFilter';
import SegmentFilterMenu, {MembershipChoice, type SegmentContext} from './SegmentFilterMenu';

type Props = {
  filters: FilterGroup; context: FilterContext; options: ColumnOption[]; allowedOps?: readonly LeafOp[]; onChange: (filters: FilterGroup) => void;
  /** Disables adding. Existing chips can still be removed. */ readOnly?: boolean;
  /** Lets people filter by membership in a saved segment, and names the segments in chips. */ segments?: SegmentContext;
};

/** Plain-language filter chips with "Match all of these" and "Match any of these" groups. One level of nesting. */
export default function FilterChips({filters, context, options, allowedOps, onChange, readOnly = false, segments}: Props) {
  const focusAdd = useRef<HTMLButtonElement>(null), choiceName = useId();
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
            : isSegmentFilter(item) ? (() => {
              const sentence = describeSegmentNode(item, segments?.names ?? (() => undefined)), missing = !segments?.names(item.segment);
              const remove = () => {onChange(removeFilterNode(filters, path, index)); focusAdd.current?.focus();};
              return <li key={index} className="data-chip" data-missing={missing || undefined}>
                {missing && <TriangleAlert size={16} strokeWidth={1.5} className="data-chip-warning" aria-hidden="true"/>}
                {missing ? <span className="data-chip-text">{sentence}</span> : <Popover>
                  <PopoverTrigger asChild><button type="button" className="data-chip-text" disabled={readOnly} onKeyDown={event => {if (event.key === 'Delete' || event.key === 'Backspace') {event.preventDefault(); remove();}}}>{sentence}</button></PopoverTrigger>
                  <PopoverContent className="data-filter-popover" aria-label={'Edit filter: ' + sentence}><div className="data-filter-form">
                    <MembershipChoice value={!!item.negate} name={choiceName + index} onChange={negate => onChange(replaceFilterNode(filters, path, index, {segment: item.segment, ...(negate ? {negate: true} : {})}))}/>
                  </div></PopoverContent>
                </Popover>}
                <button type="button" className="data-chip-remove" aria-label={'Remove filter: ' + sentence} onClick={remove}><X size={16} strokeWidth={1.5}/></button>
              </li>;
            })()
            : (() => {
              if (!isFilterLeaf(item)) return null;
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
        {!nested && !readOnly && segments && <SegmentFilterMenu context={segments}/>}
        {!nested && !readOnly && filters.items.length > 0 && <Button variant="ghost" size="sm" onClick={() => onChange(addFilterNode(filters, [], {op: 'or', items: []}))}><Plus size={16} strokeWidth={1.5}/>Add group</Button>}
      </div>
    </div>;
  }
  return <div className="data-filters" aria-label="Filters" role="group">{renderGroup(filters, [])}</div>;
}
