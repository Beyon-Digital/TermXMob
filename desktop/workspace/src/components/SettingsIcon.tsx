import {CirclePlay,Cloud,Cpu,Database,Keyboard,KeyRound,Monitor,Plug,Puzzle,Settings2,ShieldCheck,Sparkles,SunMoon,Users,Webhook,Zap,type LucideIcon} from 'lucide-react';

const icons:Record<string,LucideIcon>={
 general:Settings2,models:Cpu,cloud:Cloud,agents:Users,extensions:Puzzle,
 connections:Plug,memory:Database,hooks:Webhook,automations:Zap,
 runners:CirclePlay,access:KeyRound,characters:Sparkles,appearance:SunMoon,
 shortcuts:Keyboard,safety:ShieldCheck,host:Monitor,
};

export default function SettingsIcon({kind,size=20,className}:{kind:string;size?:number;className?:string}){
 const Icon=Object.hasOwn(icons,kind)?icons[kind]:Settings2;
 return <Icon className={className} size={size} strokeWidth={1.6} aria-hidden="true" focusable="false"/>;
}
