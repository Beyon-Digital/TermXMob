import {useId} from 'react';
import * as Menu from '@radix-ui/react-dropdown-menu';
import {Check, ChevronDown} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import './report-dataset-picker.css';

export type ReportDatasetGroup = {label:string; options:{value:string; label:string}[]};

/** The same grouped choice in the report dialog and editor, using the app's Radix menu. */
export function ReportDatasetPicker({value, groups, disabled, onChange}:{value:string; groups:ReportDatasetGroup[]; disabled?:boolean; onChange:(value:string)=>void}){
 const id=useId(),available=groups.filter(group=>group.options.length),selected=available.flatMap(group=>group.options).find(option=>option.value===value),unavailable=disabled||!available.length;
 return <div className="bi-dataset-field"><label id={id+'-label'} htmlFor={id}>Data</label>
  <DropdownMenu><DropdownMenuTrigger asChild disabled={unavailable}><Button id={id} variant="secondary" className="bi-dataset-trigger" disabled={unavailable} aria-labelledby={id+'-label '+id+'-value'}>
   <span id={id+'-value'} title={selected?.label}>{selected?.label||(value?'Unavailable data':'Choose data')}</span><ChevronDown size={16} strokeWidth={1.5} aria-hidden="true"/>
  </Button></DropdownMenuTrigger><DropdownMenuContent className="bi-dataset-menu" align="start" aria-labelledby={id+'-label'}>
   <Menu.RadioGroup value={value} onValueChange={onChange}>{available.map((group,index)=><DropdownMenuGroup key={group.label} aria-labelledby={id+'-group-'+index}>
    {index>0&&<DropdownMenuSeparator/>}<DropdownMenuLabel id={id+'-group-'+index}>{group.label}</DropdownMenuLabel>
    {group.options.map(option=><Menu.RadioItem className="ui-menu-item ui-menu-check" key={option.value} value={option.value}>
     <span className="ui-menu-indicator"><Menu.ItemIndicator><Check size={16} strokeWidth={1.5} aria-hidden="true"/></Menu.ItemIndicator></span><span>{option.label}</span>
    </Menu.RadioItem>)}
   </DropdownMenuGroup>)}</Menu.RadioGroup>
  </DropdownMenuContent></DropdownMenu>
 </div>;
}
