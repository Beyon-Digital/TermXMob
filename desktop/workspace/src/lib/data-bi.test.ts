import {describe,expect,it} from 'vitest';
import {biChartLayout,biDomain,biNumber,biPoints,biValidDefinition} from './data-bi';
const result={columns:[{name:'category'},{name:'amount'}],rows:[['a',12],['b',null],['c',''],['d',Infinity],['e',-4],['f','3.5']],row_count:6,truncated:false};
describe('Data BI chart values',()=>{
 it('keeps zero and finite numbers while excluding missing or nonnumeric values',()=>{expect([0,'2',null,'',false,Infinity,'NaN'].map(biNumber)).toEqual([0,2,null,null,null,null,null])});
 it('retains negative values for ordinary charts and counts omitted rows',()=>{const chart=biPoints(result,{kind:'bar',x:'category',y:'amount'});expect(chart.points.map(row=>row.value)).toEqual([12,-4,3.5]);expect(chart.omitted).toBe(3)});
 it('excludes negative values from a pie without replacing them',()=>{const chart=biPoints(result,{kind:'pie',x:'category',y:'amount'});expect(chart.points.map(row=>row.value)).toEqual([12,3.5]);expect(chart.omitted).toBe(4)});
 it('treats field names as exact data keys',()=>{expect(biPoints(result,{kind:'bar',x:'category',y:'SUM(amount)'}).error).toBe('Choose a value field.')});
 it('requires a finite numeric x value for scatter',()=>{expect(biPoints(result,{kind:'scatter',x:'category',y:'amount'}).points).toHaveLength(0)});
 it('uses a nonzero domain for constant values',()=>{expect(biDomain([0,0])).toEqual([-1,1]);expect(biDomain([-4,12])).toEqual([-4,12])});
 it('only accepts supported typed report proposals',()=>{expect(biValidDefinition({name:'Report',query_id:'q',visual:{kind:'bar'}})).toBe(true);expect(biValidDefinition({name:'Report',query_id:'q',visual:{kind:'raw_html'}})).toBe(false)});
});

describe('Responsive BI chart geometry',()=>{
 it('keeps CSS-pixel coordinates and fewer labels on a narrow plot',()=>{
  const mobile=biChartLayout(280,[-12000,24000]),desktop=biChartLayout(800,[-12000,24000]);
  expect(mobile.width).toBe(280);expect(mobile.right).toBe(256);expect(mobile.left).toBeGreaterThanOrEqual(64);expect(mobile.right-mobile.left).toBeGreaterThan(120);
  expect(mobile.tickCount).toBeLessThan(desktop.tickCount);expect(mobile.ticks).toHaveLength(3);expect(mobile.labelLength).toBeLessThanOrEqual(16);
 });
 it('reserves numeric tick space and bounds large or tiny axis labels',()=>{
  for(const values of [[-1e12,2e12],[1e-8,2e-8],[-42,101]]){const chart=biChartLayout(240,values);expect(chart.left).toBeGreaterThanOrEqual(Math.min(96,Math.max(...chart.ticks.map(tick=>tick.label.length))*8+16));expect(chart.ticks.every(tick=>tick.label.length<=10)).toBe(true);expect(chart.left).toBeLessThan(chart.right)}
  expect(biChartLayout(100,[1,2]).width).toBe(240);
 });
});
