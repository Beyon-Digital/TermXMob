export interface ReviewDiffLine{kind:'context'|'addition'|'deletion'|'hunk'|'meta';text:string;oldLine:number|null;newLine:number|null}
export interface SplitDiffRow{kind:'line'|'hunk'|'meta';before:ReviewDiffLine|null;after:ReviewDiffLine|null;text?:string}
export function parseReviewDiff(patch:string):ReviewDiffLine[]{
 const rows:ReviewDiffLine[]=[];let before=0,after=0,inHunk=false;
 for(const raw of patch.replace(/\r\n/g,'\n').split('\n')){
  const hunk=/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
  if(hunk){before=Number(hunk[1]);after=Number(hunk[2]);inHunk=true;rows.push({kind:'hunk',text:raw,oldLine:null,newLine:null});continue}
  if(inHunk&&raw.startsWith('+'))rows.push({kind:'addition',text:raw.slice(1),oldLine:null,newLine:after++});
  else if(inHunk&&raw.startsWith('-'))rows.push({kind:'deletion',text:raw.slice(1),oldLine:before++,newLine:null});
  else if(inHunk&&raw.startsWith(' '))rows.push({kind:'context',text:raw.slice(1),oldLine:before++,newLine:after++});
  else if(raw&&(!/^(diff --git |index |--- |\+\+\+ )/.test(raw))){rows.push({kind:'meta',text:raw,oldLine:null,newLine:null});if(!raw.startsWith('\\'))inHunk=false}
  else if(raw.startsWith('diff --git '))inHunk=false;
 }
 return rows;
}
export function splitReviewDiff(lines:ReviewDiffLine[]):SplitDiffRow[]{
 const rows:SplitDiffRow[]=[];let removed:ReviewDiffLine[]=[],added:ReviewDiffLine[]=[];
 const flush=()=>{for(let index=0;index<Math.max(removed.length,added.length);index++)rows.push({kind:'line',before:removed[index]||null,after:added[index]||null});removed=[];added=[]};
 for(const line of lines){if(line.kind==='deletion'){removed.push(line);continue}if(line.kind==='addition'){added.push(line);continue}flush();if(line.kind==='context')rows.push({kind:'line',before:line,after:line});else rows.push({kind:line.kind,before:null,after:null,text:line.text})}flush();return rows;
}
