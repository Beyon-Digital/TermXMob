import {useEffect,useState} from 'react';
export type ThemePreference='dark'|'light'|'system';
const shellThemeKey='termx-shell-theme';
// An existing stored choice wins; new profiles follow the device (System).
function initialTheme():ThemePreference{try{const saved=localStorage.getItem(shellThemeKey);return saved==='light'||saved==='dark'||saved==='system'?saved:'system'}catch{return 'system'}}
export function resolveTheme(preference:ThemePreference,systemDark:boolean):'dark'|'light'{return preference==='system'?(systemDark?'dark':'light'):preference}
export function useTheme(){
 const [preference,setPreference]=useState<ThemePreference>(initialTheme);
 useEffect(()=>{try{localStorage.setItem(shellThemeKey,preference)}catch{}const media=window.matchMedia('(prefers-color-scheme: dark)');const apply=()=>{const resolved=resolveTheme(preference,media.matches);const root=document.documentElement;root.dataset.theme=resolved;root.classList.toggle('dark',resolved==='dark');root.dataset.themePreference=preference};apply();media.addEventListener('change',apply);return()=>media.removeEventListener('change',apply)},[preference]);
 return [preference,setPreference] as const;
}
