import {createContext,useContext,isValidElement,type ComponentProps,type ReactNode} from 'react';
import {defaultComponents,defaultRemarkPlugins,type Components,type ExtraProps} from 'streamdown';
import {MessageResponse} from '../components/ai-elements/message';
import {authorizedAssetSource,contentText,safeWebLink} from '../lib/content-preview';
import {ContentAssetPreview,WebLinkPreview} from './ContentPreview';

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
const VisibleContext=createContext(true);
const OpenFileContext=createContext<((path:string,line?:number)=>void)|undefined>(undefined);
function FileCode({node,children,...props}:ComponentProps<'code'>&ExtraProps){
 const open=useContext(OpenFileContext),text=typeof children==='string'?children:null,reference=text?conversationFileReference(text):null;
 if(open&&reference)return <button type="button" className="conversation-file-reference" aria-label={'Open file '+reference.path+(reference.line?':'+reference.line:'')} title={'Open '+reference.path+(reference.line?':'+reference.line:'')} onClick={()=>open(reference.path,reference.line)}><code {...props}>{children}</code></button>;
 return <defaultComponents.code node={node} {...props}>{children}</defaultComponents.code>;
}
type MarkdownNode={type:string;url?:string;value?:string;data?:Record<string,unknown>;children?:MarkdownNode[]};
const markdownLabel=(node:MarkdownNode):string=>node.value||node.children?.map(markdownLabel).join('')||'';
function remarkFileLinks(){return (tree:MarkdownNode)=>{
 const walk=(node:MarkdownNode)=>{
  if(node.type==='paragraph'&&node.children?.length===1&&node.children[0].type==='image'){const image=node.children[0] as MarkdownNode&{alt?:string};node.data={...node.data,hName:'termx-image-preview',hProperties:{src:image.url||'',alt:image.alt||''}};node.children=[];return}
  if(node.type==='paragraph'&&node.children?.length===1&&node.children[0].type==='link'){const link=node.children[0],href=safeWebLink(link.url);if(href){node.data={...node.data,hName:'termx-link-preview',hProperties:{href,label:markdownLabel(link)}};node.children=[];return}}
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
function LinkPreviewTag({href,label}:Record<string,unknown>&ExtraProps){return <WebLinkPreview url={contentText(href)} title={contentText(label)}/>}
function ImagePreviewTag({src,alt}:Record<string,unknown>&ExtraProps){return <ImagePreview src={contentText(src)} alt={contentText(alt)}/>}
function ImagePreview({src,alt}:{src?:string;alt?:string}){
 const visible=useContext(VisibleContext),source=authorizedAssetSource(src);
 if(source)return <ContentAssetPreview asset={{key:source,source,name:alt||'Image attachment',mime:'',kind:'image'}} visible={visible}/>;
 const href=safeWebLink(src);
 return href?<WebLinkPreview url={href} title={alt||'Image link'} source="Remote image link; preview not loaded"/>:<span className="content-preview-note">{alt||'Image preview unavailable'}</span>;
}
function SafeInlineImage({src,alt}:ComponentProps<'img'>&ExtraProps){const href=safeWebLink(src);return href?<a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{alt||'Image link'}</a>:<span>{alt||'Image attachment'}</span>}
const components:Components={inlineCode:FileCode,img:SafeInlineImage,'termx-image-preview':ImagePreviewTag,'termx-file-reference':FileLinkTag,'termx-link-preview':LinkPreviewTag};
const remarkPlugins=[...Object.values(defaultRemarkPlugins),remarkFileLinks];
const allowedTags={'termx-file-reference':['path','line'],'termx-link-preview':['href','label'],'termx-image-preview':['src','alt']};
export default function ConversationResponse({onOpenFile,visible=true,...props}:ComponentProps<typeof MessageResponse>&{onOpenFile?:(path:string,line?:number)=>void;visible?:boolean}){
 return <OpenFileContext.Provider value={onOpenFile}><VisibleContext.Provider value={visible}><MessageResponse {...props} components={components} remarkPlugins={remarkPlugins} allowedTags={allowedTags}/></VisibleContext.Provider></OpenFileContext.Provider>;
}
