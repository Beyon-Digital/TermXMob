import {useCallback,useEffect,useState} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {request} from '../lib/api';

type Skill={id:string;version:string;description?:string;permissions:string[];computer_apps?:string[]};

/** Installed skills that can control this computer, and the applications each one is limited to. */
export default function ComputerSkills(){
  const [skills,setSkills]=useState<Skill[]|null>(null);
  const [error,setError]=useState('');
  const load=useCallback(async()=>{
    setError('');
    try{
      const result=await request<{extensions?:Skill[]}>('/api/workspace/available-extensions');
      const rows=Array.isArray(result?.extensions)?result.extensions:[];
      setSkills(rows.filter(row=>Array.isArray(row.permissions)&&row.permissions.includes('desktop-control')));
    }catch(e){
      setSkills(null);
      setError(e instanceof Error?e.message:String(e));
    }
  },[]);
  useEffect(()=>{void load()},[load]);
  if(error)return <section className="computer-skills" aria-label="Computer skills">
    <div role="alert" className="inline-error">Computer skills could not be loaded. {error}<Button variant="outline" size="sm" onClick={()=>void load()}>Reload computer skills</Button></div>
  </section>;
  if(skills===null)return <section className="computer-skills" aria-label="Computer skills"><p role="status" className="computer-skills-note">Loading computer skills…</p></section>;
  return <section className="computer-skills" aria-label="Computer skills">
    <h2>Computer skills</h2>
    {skills.length===0?<p className="computer-skills-note">No computer skills are installed. Install one under Plugins &amp; skills, and review the applications it asks for.</p>:<>
      <ul className="computer-skill-list">{skills.map(skill=><li key={skill.id} aria-label={skill.id}>
        <div className="computer-skill-head"><strong>{skill.id}</strong><small>version {skill.version}</small><Badge variant="outline">Controls this computer</Badge></div>
        {skill.description&&<p className="computer-skills-note">{skill.description}</p>}
        {skill.computer_apps?.length?<ul className="computer-skill-apps" aria-label={`Applications for ${skill.id}`}>{skill.computer_apps.map(app=><li key={app}><code>{app}</code></li>)}</ul>
          :<p className="computer-skills-note">No application list. It can act in whichever application is in front.</p>}
      </li>)}</ul>
      <p className="computer-skills-note">The application in front is checked before each batch of input, between its actions and after it. The check after a batch cannot undo input that was already sent. Only macOS reports the application in front; on other systems the check is best effort. When several lists apply, each must name an application the same way, such as Notes in all of them.</p>
    </>}
  </section>;
}
