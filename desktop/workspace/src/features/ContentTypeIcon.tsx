import {AudioLines,File,FileArchive,FileCode2,FileJson2,FileSpreadsheet,FileText,Folder,Globe,Image,Video} from 'lucide-react';
import type {ContentKind} from '../lib/content-preview';
const icons={image:Image,video:Video,audio:AudioLines,pdf:FileText,document:FileText,spreadsheet:FileSpreadsheet,code:FileCode2,json:FileJson2,archive:FileArchive,file:File,folder:Folder,website:Globe};
export default function ContentTypeIcon({kind,size=20}:{kind:ContentKind;size?:number}){
 const Icon=icons[kind];
 return <span className={'content-type-icon content-type-'+kind} aria-hidden="true"><Icon size={size} strokeWidth={1.5}/>{kind==='pdf'&&<span className="content-type-extension">PDF</span>}</span>;
}
