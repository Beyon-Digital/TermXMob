import type {DataRecord, DataResult} from './data-studio';

export type BIKind = 'table'|'bar'|'line'|'area'|'scatter'|'pie'|'metric'|'pivot';
export type BIAggregate = 'none'|'sum'|'count'|'avg'|'min'|'max';
export type BIVisual = {kind:BIKind; x?:string; y?:string; aggregate?:BIAggregate};
export type BIFilter = {field:string; operator:string; value?:unknown};
export type BIReportDefinition = {name:string; query_id?:string; view_id?:string; visual:BIVisual; filters?:BIFilter[]; description?:string};
export type BIPublication = DataRecord & {version?:number; definition?:BIReportDefinition; published_at?:string};
export type BIReport = DataRecord & BIReportDefinition & {published?:boolean; published_version?:number|BIPublication; publication?:BIPublication; published_version_id?:string; published_revision?:number; version?:number; versions?:BIPublication[]; using_published_version?:boolean};
export type BIDashboard = DataRecord & {name:string; report_ids:string[]};
export type ChartPoint = {label:string; value:number; x:number; row:number};
export const BI_KINDS:{value:BIKind;label:string}[] = [{value:'table',label:'Table'},{value:'metric',label:'KPI'},{value:'bar',label:'Bar'},{value:'line',label:'Line'},{value:'area',label:'Area'},{value:'scatter',label:'Scatter'},{value:'pie',label:'Pie'},{value:'pivot',label:'Pivot'}];
export const BI_AGGREGATES:{value:BIAggregate;label:string}[] = [{value:'none',label:'None'},{value:'sum',label:'Sum'},{value:'count',label:'Count'},{value:'avg',label:'Average'},{value:'min',label:'Minimum'},{value:'max',label:'Maximum'}];
export const BI_FILTERS = [{value:'eq',label:'Equals'},{value:'ne',label:'Does not equal'},{value:'gt',label:'Greater than'},{value:'gte',label:'At least'},{value:'lt',label:'Less than'},{value:'lte',label:'At most'},{value:'contains',label:'Contains'},{value:'in',label:'Is any of'},{value:'not_in',label:'Is none of'},{value:'is_null',label:'Is empty'},{value:'not_null',label:'Is not empty'}];

/** Keep nulls, booleans and empty strings out of numeric plots. */
export function biNumber(value:unknown):number|null {
 if(typeof value!=='number'&&typeof value!=='string')return null;
 if(typeof value==='string'&&!value.trim())return null;
 const number=Number(value);return Number.isFinite(number)?number:null;
}
export function biCell(value:unknown):string {
 if(value===null||value===undefined)return 'NULL';
 if(typeof value==='object'){try{return JSON.stringify(value)}catch{return String(value)}}
 return String(value);
}
export function biPoints(result:DataResult,visual:BIVisual):{points:ChartPoint[];omitted:number;error?:string} {
 const yi=result.columns.findIndex(column=>column.name===visual.y),xi=result.columns.findIndex(column=>column.name===visual.x);
 if(yi<0)return {points:[],omitted:0,error:'Choose a value field.'};
 if(xi<0&&visual.kind!=='metric')return {points:[],omitted:0,error:'Choose a category field.'};
 const points:ChartPoint[]=[];let omitted=0;
 result.rows.forEach((row,index)=>{
  const value=biNumber(row[yi]),x=visual.kind==='scatter'?biNumber(row[xi]):index;
  if(value===null||x===null||(visual.kind==='pie'&&value<0)){omitted++;return}
  points.push({label:xi<0?result.columns[yi].name:biCell(row[xi]),value,x,row:index});
 });
 return {points,omitted};
}
export function biDomain(values:number[],includeZero=true):[number,number] {
 if(!values.length)return [0,1];
 let lo=Math.min(...values),hi=Math.max(...values);
 if(includeZero){lo=Math.min(0,lo);hi=Math.max(0,hi)}
 if(lo===hi){const offset=Math.abs(lo)*0.1||1;return [lo-offset,hi+offset]}
 return [lo,hi];
}
export function biScale(value:number,domain:[number,number],start:number,end:number){return start+(value-domain[0])/(domain[1]-domain[0])*(end-start)}
export function biFormat(value:number){return new Intl.NumberFormat(undefined,{maximumFractionDigits:4}).format(value)}
export function biPublished(report:BIReport){return !!(report.published||report.published_version||report.publication)}
export function biDefinition(report:BIReport):BIReportDefinition {return {name:report.name,...(report.query_id?{query_id:report.query_id}:{}),...(report.view_id?{view_id:report.view_id}:{}),visual:{...report.visual},filters:(report.filters||[]).map(filter=>({...filter})),description:report.description||''}}
export function biValidDefinition(value:unknown):value is BIReportDefinition {
 if(!value||typeof value!=='object')return false;
 const row=value as Partial<BIReportDefinition>;return typeof row.name==='string'&&(typeof row.query_id==='string'||typeof row.view_id==='string')&&!!row.visual&&BI_KINDS.some(kind=>kind.value===row.visual?.kind);
}
/** Export the rendered marks and labels, with resolved colors for a standalone SVG. */
export function biSvg(node:SVGSVGElement):string {
 const clone=node.cloneNode(true) as SVGSVGElement;
 clone.setAttribute('xmlns','http://www.w3.org/2000/svg');
 const originals=[node,...node.querySelectorAll('*')],copies=[clone,...clone.querySelectorAll('*')];
 originals.forEach((element,index)=>{
  const computed=getComputedStyle(element),copy=copies[index];
  ['fill','stroke','color','font-family','font-size','font-weight','opacity'].forEach(property=>{const value=computed.getPropertyValue(property);if(value)copy.setAttribute(property,value)});
 });
 return new XMLSerializer().serializeToString(clone);
}

export function biScopeInvalid(reason:unknown){const status=reason&&typeof reason==='object'&&'status' in reason?Number(reason.status):0;return [401,403,404,409,423].includes(status)}

/** Chart units follow CSS pixels so a narrow view never shrinks its 12px labels. */
export function biChartLayout(measuredWidth:number,values:number[]){
 const width=Math.max(240,Math.round(measuredWidth)),height=320,top=24,bottom=264,domain=biDomain(values);
 const format=(value:number)=>{const magnitude=Math.abs(value);return magnitude>=1e6||magnitude>0&&magnitude<0.001?new Intl.NumberFormat(undefined,{notation:magnitude>=1e6?'compact':'scientific',maximumFractionDigits:2}).format(value):biFormat(value)};
 const ticks=[0,0.5,1].map(fraction=>{const value=domain[0]+(domain[1]-domain[0])*fraction;return {value,label:format(value)}});
 const left=Math.max(48,Math.min(96,Math.max(...ticks.map(tick=>tick.label.length))*8+16)),right=width-24;
 const tickCount=Math.max(1,Math.min(6,Math.floor((right-left)/80))),labelLength=Math.max(4,Math.min(16,Math.floor((right-left)/tickCount/7)-1));
 return {width,height,left,right,top,bottom,domain,ticks,tickCount,labelLength};
}

export const biIsList=(operator:string)=>operator==='in'||operator==='not_in';
export function biListText(value:unknown){return Array.isArray(value)?value.map(item=>item===null?'':String(item)).join(', '):value===null||value===undefined?'':String(value)}
/** "a, b, 3" becomes ['a','b',3] when the field is numeric; blanks are dropped. */
export function biListValue(text:string,numeric:boolean){return text.split(',').map(part=>part.trim()).filter(Boolean).map(part=>numeric&&Number.isFinite(Number(part))?Number(part):part)}
