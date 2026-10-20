import {useEffect,useRef,useState} from 'react';
import {request} from '../lib/api';
import {transfer} from '../lib/transfers';
import {characterDimensions,characterPath,clipFor,frameAt,type CharacterManifest} from '../lib/characters';
import {useSystemReducedMotion} from '../lib/use-system-reduced-motion';

type Assets={manifest:CharacterManifest;images:HTMLImageElement[]};
type CacheEntry={users:number;promise:Promise<Assets>;urls:string[]};
const cache=new Map<string,CacheEntry>();
function release(key:string,entry:CacheEntry){entry.users--;if(!entry.users){cache.delete(key);void entry.promise.finally(()=>entry.urls.forEach(url=>URL.revokeObjectURL(url))).catch(()=>{})}}
function acquire(packId:string,revision:number){
 const key=packId+':'+revision,current=cache.get(key);if(current){current.users++;return {key,entry:current}}
 const entry:CacheEntry={users:1,urls:[],promise:Promise.resolve(null as unknown as Assets)};
 entry.promise=(async()=>{
  const manifest=await request<CharacterManifest>(characterPath(packId)+'/manifest?revision='+revision);
  const images=await Promise.all(manifest.atlases.map(async atlas=>{
   const blob=await transfer(characterPath(packId)+'/assets/'+encodeURIComponent(atlas.file)+'?revision='+revision);if(!entry.users)throw new Error('Character artwork was closed.');const url=URL.createObjectURL(blob);entry.urls.push(url);
   return new Promise<HTMLImageElement>((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('Could not decode character artwork.'));image.src=url});
  }));return {manifest,images};
 })();cache.set(key,entry);return {key,entry};
}
export function useCharacterManifest(packId:string,revision:number){
 const [assets,setAssets]=useState<Assets|null>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;setAssets(null);setError('');if(!packId||!Number.isInteger(revision)||revision<1)return;const {key,entry}=acquire(packId,revision);entry.promise.then(value=>{if(active)setAssets(value)}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:String(reason))});return()=>{active=false;release(key,entry)}},[packId,revision]);
 return {assets,error};
}
export default function CharacterSprite({packId,revision,state='idle',size=96,frame,paused=false,reducedMotion=false,flip=false,onManifest}:{packId:string;revision:number;state?:string;size?:number;frame?:number;paused?:boolean;reducedMotion?:boolean;flip?:boolean;onManifest?:(value:CharacterManifest)=>void}){
 const systemReduced=useSystemReducedMotion();reducedMotion ||= systemReduced;
 const canvas=useRef<HTMLCanvasElement>(null),{assets,error}=useCharacterManifest(packId,revision),notify=useRef(onManifest);notify.current=onManifest;
 useEffect(()=>{if(assets)notify.current?.(assets.manifest)},[assets]);
 useEffect(()=>{
  const node=canvas.current;if(!node||!assets)return;const context=node.getContext('2d');if(!context)return;
  const {manifest,images}=assets,clip=clipFor(manifest,state);if(!clip)return;
  const ratio=Math.min(2,window.devicePixelRatio||1),dimensions=characterDimensions(manifest.logical_size,size);
  node.width=Math.ceil(dimensions.width*ratio);node.height=Math.ceil(dimensions.height*ratio);let animation=0,started=performance.now(),hiddenAt=0;
  function draw(now:number){
   context!.clearRect(0,0,node!.width,node!.height);
   const index=frame===undefined?frameAt(clip,reducedMotion?0:now-started):Math.min(Math.max(0,frame),clip.frames.length-1),current=clip.frames[index];
   if(!current)return;const atlas=typeof current.atlas==='number'?current.atlas:manifest.atlases.findIndex(atlas=>atlas.file===current.atlas),image=images[atlas];if(!image)return;
   context!.save();context!.scale(ratio*dimensions.scale,ratio*dimensions.scale);if(flip){context!.translate(manifest.logical_size.width,0);context!.scale(-1,1)}
   context!.drawImage(image,current.x,current.y,current.width,current.height,0,0,manifest.logical_size.width,manifest.logical_size.height);context!.restore();
   if(!paused&&!reducedMotion&&frame===undefined&&!document.hidden)animation=requestAnimationFrame(draw);
  }
  const visibility=()=>{cancelAnimationFrame(animation);if(document.hidden)hiddenAt=performance.now();else{if(hiddenAt)started+=performance.now()-hiddenAt;draw(performance.now())}};
  draw(performance.now());document.addEventListener('visibilitychange',visibility);return()=>{cancelAnimationFrame(animation);document.removeEventListener('visibilitychange',visibility)};
 },[assets,state,size,frame,paused,reducedMotion,flip]);
 const dimensions=characterDimensions(assets?.manifest.logical_size||{width:1,height:1},size);
 return <span className="character-sprite" style={{width:dimensions.width,height:dimensions.height}}>{error?<span className="character-art-error" role="img" aria-label={error}>?</span>:<canvas ref={canvas} style={{width:dimensions.width,height:dimensions.height}} role="img" aria-label={assets?assets.manifest.name+' '+state:'Loading character artwork'}/>}</span>;
}
