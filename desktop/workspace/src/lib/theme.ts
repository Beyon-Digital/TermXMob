import {useEffect,useState} from 'react';
export type ThemePreference='dark'|'light'|'system';
export function resolveTheme(preference:ThemePreference,systemDark:boolean):'dark'|'light'{return preference==='system'?(systemDark?'dark':'light'):preference}
export function useTheme(){
 const [preference,setPreference]=useState<ThemePreference>('dark');
 useEffect(()=>{const media=window.matchMedia('(prefers-color-scheme: dark)');const apply=()=>{document.documentElement.dataset.theme=resolveTheme(preference,media.matches);document.documentElement.dataset.themePreference=preference};apply();media.addEventListener('change',apply);return()=>media.removeEventListener('change',apply)},[preference]);
 return [preference,setPreference] as const;
}
