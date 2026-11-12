/** Contract types for the visual query builder. Mirrors section 4.6 of the Data Studio foundation plan. */
export type ValueKind = 'text'|'integer'|'number'|'boolean'|'date'|'datetime'|'time'|'json'|'binary'|'uuid'|'array'|'other';
export type DateBucket = 'day'|'week'|'month'|'quarter'|'year';
export type Scalar = string|number|boolean;
export type ColumnRef = {table_alias:string; column:string};
export type TableRef = {table:string; alias:string};
export type JoinOn = {left:ColumnRef; right:ColumnRef};
export type Join = {kind:'inner'|'left'; table:string; alias:string; on:JoinOn[]};
export type SelectColumn = ColumnRef & {label?:string};
export type LeafOp = 'eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'between'|'in'|'not_in'|'contains'|'starts_with'|'is_null'|'not_null';
export type FilterLeaf = {field:ColumnRef; op:LeafOp; value?:Scalar; values?:Scalar[]};
export type FilterGroup = {op:'and'|'or'; items:FilterNode[]};
export type FilterSegment = {segment:string; negate?:boolean};
export type FilterNode = FilterLeaf|FilterGroup|FilterSegment;
export type GroupBy = ColumnRef & {bucket?:DateBucket; label?:string};
export type AggregateFn = 'count'|'count_distinct'|'sum'|'avg'|'min'|'max';
export type Aggregate = {fn:AggregateFn; table_alias?:string; column?:string; label?:string};
export type SortKey = (ColumnRef|{output:string}) & {direction:'asc'|'desc'};
export type PivotDim = {table_alias:string; column:string; bucket?:DateBucket; label?:string};
export type PivotValue = {fn:AggregateFn; table_alias?:string; column?:string; label?:string};
export type PivotSpec = {rows:PivotDim[]; columns:PivotDim|null; value:PivotValue; totals:boolean; sort:{by:'rows'|'total'; direction:'asc'|'desc'}};
export type QuerySpec = {version:1; source:string; from:TableRef; joins:Join[]; select:SelectColumn[]; filters:FilterGroup;
  group_by:GroupBy[]; aggregates:Aggregate[]; sort:SortKey[]; limit:number; offset:number; pivot?:PivotSpec};
export const isSegmentFilter = (node:FilterNode): node is FilterSegment => 'segment' in node;
export const isFilterLeaf = (node:FilterNode): node is FilterLeaf => 'field' in node;

export type OutputRole = 'column'|'group'|'aggregate'|'pivot_row'|'pivot_cell'|'pivot_total';
export type OutputColumn = {label:string; value_kind:ValueKind; role:OutputRole; alias?:string; table_alias?:string; column?:string; fn?:AggregateFn; bucket?:DateBucket; column_value?:Scalar|null; column_empty?:boolean};
export type SpecError = {code:string; path:string; message:string};
export type SpecWarning = {code:'fan_out'|'text_date'|'many_to_many'|'row_multiplication'|'segment_empty'; message:string; join_alias?:string};
export type JoinInfo = {alias:string; fan_out:'none'|'many'|'unknown'; message:string|null};
export type CompileResult =
  | {ok:true; dialect:string; sql:string; parameters:Record<string,Scalar>; columns:OutputColumn[]; limit:number; offset:number; fetch_limit:number; joins:JoinInfo[]; warnings:SpecWarning[]; schema_hash:string; pivot?:{probed:true; column_count:number; limit:number}}
  | {ok:false; errors:SpecError[]};
export type Relation = {child_table:string; child_columns:string[]; parent_table:string; parent_columns:string[]; source:'foreign_key'|'name_match'; confidence:'declared'|'likely'; cardinality:'many_to_one'; sentence:string};
export type RelationsResult = {connection_id:string; relations:Relation[]};
export type DialectCapabilities = { quote:string; param_style:'named_colon'; table_qualification:'none'|'schema'|'database';
  text_match:{modes:('contains'|'starts_with')[]; case:'insensitive'; case_folding:'ascii'|'unicode'; like_escape:string};
  text_equality:'exact'|'collation'; filter_ops:LeafOp[]; max_in_values:number; null_ordering:'last'; null_ordering_impl:'native'|'emulated';
  boolean:{literal_true:string; literal_false:string; bind:'integer'|'boolean'}; limit_syntax:'limit_offset'; aggregates:AggregateFn[];
  conditional_aggregation:'case'; date_bucket:{buckets:DateBucket[]; week_standard:'iso8601'; labels:Record<DateBucket,string>; kinds:ValueKind[]};
  join_kinds:('inner'|'left')[]; max_joins:number; max_filters:number; pivot:{max_columns:number; max_row_dims:number; column_kinds:ValueKind[]}|false; version_floor:string };
export type CapabilitiesResult = {connection_id:string; engine:string; label:string; supported:boolean; reason:string|null; qualified:boolean;
  requires_source_test:boolean; source_tested:boolean; dialect:DialectCapabilities|null};
export type SchemaColumn = {name:string; type?:string; nullable?:boolean; primary_key?:boolean; value_kind?:ValueKind};
export type SchemaForeignKey = {columns:string[]; referred_table:string; referred_columns:string[]};
export type SchemaTable = {name:string; schema?:string; kind?:string; row_estimate?:number|null; columns:SchemaColumn[]; foreign_keys?:SchemaForeignKey[]; readable?:boolean; reason?:string};
// Results reuse DataJob / DataResult from lib/data-studio.ts unchanged.
