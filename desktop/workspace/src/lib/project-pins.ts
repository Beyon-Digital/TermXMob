import {json,request} from './api';
export type ProjectPins={revision:number;projects:string[]};
export const loadProjectPins=()=>request<ProjectPins>('/api/workspace/project-pins');
export const changeProjectPin=(project_id:string,pinned:boolean,revision:number)=>request<ProjectPins>('/api/workspace/project-pins',json('PUT',{project_id,pinned,revision}));
export function orderProjectGroups<T>(groups:[string,T][],pins:string[]):[string,T][]{const selected=new Set(pins);return groups.map((row,index)=>({row,index})).sort((a,b)=>Number(selected.has(b.row[0]))-Number(selected.has(a.row[0]))||a.index-b.index).map(item=>item.row)}
