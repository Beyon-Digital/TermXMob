import {createContext,useContext,isValidElement,type ComponentProps,type ReactNode} from 'react';
import {defaultComponents,defaultRemarkPlugins,type Components,type ExtraProps} from 'streamdown';
import {MessageResponse} from '../components/ai-elements/message';

export type ConversationFileReference={path:string;line?:number};
const namedFiles=new Set(['Dockerfile','Containerfile','Makefile','Justfile','Procfile','.gitignore','.gitattributes','.env','.env.local','.npmrc','.prettierrc']);
const fileExtension=/\.(?:[cm]?[jt]sx?|pyi?|rs|go|java|kt|swift|c|cc|cpp|h|hpp|cs|rb|php|ex|exs|html?|css|scss|sass|less|vue|svelte|jsonc?|ya?ml|toml|mdx?|txt|sh|bash|zsh|fish|sql|graphql|gql|xml|lock|ini|conf|config|csv|svg)$/i;
/** Only exact file-looking references become editor actions, never prose or URLs. */
export function conversationFileReference(value:string):ConversationFileReference|null{
 let path=value.trim();
 if(!path||/[\n\r\x00-\x1f<>]/.test(path))return null;
 const location=path.match(/(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)$/);
 const line=location?Number(location[1]||location[2]):undefined;
 if(location)path=path.slice(0,location.index);
 if(line!==undefined&&(!Number.isSafeInteger(line)||line<1))return null;
 if(!path||/^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i.test(path)||path.includes('?')||path.includes('#')||path.includes('\\')||/(?:^|\/)\.\.(?:\/|$)/.test(path))return null;
 const filename=path.split('/').at(-1)||'';
 if(!fileExtension.test(filename)&&!namedFiles.has(filename))return null;
 if(/\s/.test(path.split('/')[0]))return null;
 return {path,...(line?{line}:{})};
}
const OpenFileContext=createContext<((path:string,line?:number)=>void)|undefined>(undefined);
function FileCode({node,children,...props}:ComponentProps<'code'>&ExtraProps){
 const open=useContext(OpenFileContext),text=typeof children==='string'?children:null,reference=text?conversationFileReference(text):null;
 if(open&&reference)return <button type="button" className="conversation-file-reference" aria-label={'Open file '+reference.path+(reference.line?':'+reference.line:'')} title={'Open '+reference.path+(reference.line?':'+reference.line:'')} onClick={()=>open(reference.path,reference.line)}><code {...props}>{children}</code></button>;
 return <defaultComponents.code node={node} {...props}>{children}</defaultComponents.code>;
}
type MarkdownNode={type:string;url?:string;data?:Record<string,unknown>;children?:MarkdownNode[]};
function remarkFileLinks(){return (tree:MarkdownNode)=>{
 const walk=(node:MarkdownNode)=>{
  let reference:ConversationFileReference|null=null;
  if(node.type==='link'&&node.url)try{reference=conversationFileReference(decodeURIComponent(node.url))}catch{/* Keep malformed URLs as normal Markdown. */}
  if(reference)node.data={...node.data,hName:'termx-file-reference',hProperties:{path:reference.path,...(reference.line?{line:reference.line}:{})}};
  node.children?.forEach(walk);
 };walk(tree);
}}
function fileLabel(value:unknown):ReactNode{
 if(typeof value==='string'||typeof value==='number'||isValidElement(value))return value;
 return Array.isArray(value)?value.map(fileLabel):null;
}
function FileLinkTag({path,line,children}:Record<string,unknown>&ExtraProps){
 const open=useContext(OpenFileContext),reference=typeof path==='string'?conversationFileReference(path+(line?':'+line:'')):null;
 if(open&&reference)return <button type="button" className="conversation-file-reference" aria-label={'Open file '+reference.path+(reference.line?':'+reference.line:'')} title={'Open '+reference.path+(reference.line?':'+reference.line:'')} onClick={()=>open(reference.path,reference.line)}>{fileLabel(children)}</button>;
 return <span>{fileLabel(children)}</span>;
}
const components:Components={inlineCode:FileCode,'termx-file-reference':FileLinkTag};
const remarkPlugins=[...Object.values(defaultRemarkPlugins),remarkFileLinks];
const allowedTags={'termx-file-reference':['path','line']};
export default function ConversationResponse({onOpenFile,...props}:ComponentProps<typeof MessageResponse>&{onOpenFile?:(path:string,line?:number)=>void}){
 return <OpenFileContext.Provider value={onOpenFile}><MessageResponse {...props} components={components} remarkPlugins={remarkPlugins} allowedTags={allowedTags}/></OpenFileContext.Provider>;
}
