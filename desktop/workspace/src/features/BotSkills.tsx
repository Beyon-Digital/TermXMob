import {useState} from 'react';
import {json,request,type Session} from '../lib/api';
import {botTools} from '../lib/bots';
import {loadBotSkills,type BotTabProps,type SkillExtension} from './bot-api';
import {Empty} from './manager-ui';
import {useManagerLoad} from './use-manager';

/** Plain words for the permission ids a skill can ask for; an id with no entry is shown as the host names it. */
const permissionWords: Record<string, string> = {'desktop-control': 'control the computer'};

/** What a skill may do on this computer, in words. Skill text is untrusted; only the host's own fields are used here. */
function reach(extension: SkillExtension) {
  const permissions = extension.permissions || [];
  const parts = [permissions.length ? `Permissions: ${permissions.map(name => permissionWords[name] || name).join(', ')}` : 'No extra permissions'];
  if (extension.computer_apps?.length) parts.push(`Controls only: ${extension.computer_apps.join(', ')}`);
  else if (permissions.includes('desktop-control')) parts.push('Controls any application you have allowed');
  return parts.join(' · ');
}

/** An in-page link to another settings category. A plain click moves within Settings; a modified click opens the page normally. */
export function SettingsLink({to, onSection, children}: {to: string; onSection?: (name: string) => void; children: string}) {
  return <a className="bot-link" href={'/manage/' + to} onClick={event => {
    if (!onSection || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onSection(to);
  }}>{children}</a>;
}

/**
 * Skills are the host's installed extensions. The choice is stored on the bot's own conversation. Every skill listed here
 * was installed through a permission review and is enabled; choosing one grants nothing beyond what that review showed.
 */
export default function BotSkills({bot, mutation, onSection, onTab}: BotTabProps & {onSection?: (name: string) => void; onTab?: (tab: 'permissions') => void}) {
  const {data, loading, error, reload} = useManagerLoad(() => loadBotSkills(bot));
  const {busy, mutate} = mutation;
  const [override, setOverride] = useState<Session | null>(null);
  const session = override || data?.session;
  const chosen = session?.extension_ids || [];
  const running = bot.active_tasks > 0;
  const tools = botTools(bot);
  async function change(id: string, on: boolean) {
    if (!session) return;
    const next = on ? [...chosen, id] : chosen.filter(item => item !== id);
    const result = await mutate(() => request<Session>(`/api/workspace/sessions/${session.id}`, json('PATCH', {revision: session.revision, changes: {extension_ids: next}})), 'Skills saved', {refresh: false});
    if (result) setOverride(result); else reload();
  }
  if (error) return <div role="alert" className="manager-error">{error}</div>;
  if (loading && !data) return <Empty>Loading skills…</Empty>;
  const importLink = <SettingsLink to="extensions" onSection={onSection}>Import or install skills</SettingsLink>;
  if (!data?.extensions.length) return <Empty>No skills are installed. Add some under Plugins &amp; skills, then choose which ones this bot uses. {importLink}</Empty>;
  const cannotRead = chosen.length > 0 && !tools.includes('read_skill');
  return <>
    <p className="manager-help">
      Every skill listed here is installed, reviewed and switched on; ticking one only lets this bot use it. Small skills are added to the
      prompt as they are. A large skill is listed by file instead, and the bot reads only the files it needs, when it needs them.
      Skill text is advice for the bot and never gives it more access. {importLink}.
    </p>
    {cannotRead && <p className="manager-help bot-skill-warning">
      {bot.ceiling.tools.includes('read_skill')
        ? <>This bot cannot read large skills yet because “Read skills on demand” is off. <button type="button" className="bot-link" onClick={() => onTab?.('permissions')}>Turn it on under Permissions</button>.</>
        : <>This bot cannot read large skills because its ceiling does not include “Read skills on demand”. Add it to the ceiling under Permissions first.</>}
    </p>}
    <fieldset className="bot-tools" disabled={running || busy} data-settings-autosave>
      <legend>Skills this bot uses</legend>
      {running && <p className="manager-help">Wait for the current run to finish before changing skills.</p>}
      {data.extensions.map(extension => <label key={extension.id} className="bot-check">
        <input type="checkbox" checked={chosen.includes(extension.id)} onChange={event => void change(extension.id, event.target.checked)} />
        <span>
          {extension.id}
          {extension.description && <small>{extension.description}</small>}
          {extension.version && <small>{'Version ' + extension.version}</small>}
          <small>{reach(extension)}</small>
        </span>
      </label>)}
    </fieldset>
  </>;
}
