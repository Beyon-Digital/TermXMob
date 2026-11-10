import {Select} from '../../components/ui/select';
import type {AssignmentRow, CharacterPack, CompanionPreferences} from '../../lib/characters';
import {Section} from './Section';
import type {SavePreferences} from './use-character-library';
import {ActionError, useAction} from './use-action';

interface Props {
  rows: AssignmentRow[];
  packs: CharacterPack[];
  preferences: CompanionPreferences;
  save: SavePreferences;
}

export function Assignments({rows, packs, preferences, save}: Props) {
  const action = useAction();
  const change = (build: Parameters<SavePreferences>[0]) => void action.run(() => save(build));
  const usable = packs.filter(pack => pack.accepted || pack.accepted_revision);

  return (
    <Section title="Agent characters" description="Choose a character for each conversation. Pin the ones you want nearby.">
      <ActionError action={action} />
      {rows.length === 0 ? (
        <p>Start a conversation to meet its character.</p>
      ) : (
        <div className="character-table-scroll">
          <table className="character-assignments">
            <thead>
              <tr>
                <th scope="col">Conversation</th>
                <th scope="col">Character</th>
                <th scope="col">Pinned</th>
                <th scope="col">Visible</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id}>
                  <th scope="row">{row.title}</th>
                  <td>
                    <Select
                      aria-label={'Character for ' + row.title}
                      value={preferences.assignments[row.id] || ''}
                      onChange={event => {
                        const choice = event.target.value;
                        change(current => {
                          const assignments = {...current.assignments};
                          if (choice) assignments[row.id] = choice;
                          else delete assignments[row.id];
                          return {assignments};
                        });
                      }}
                    >
                      <option value="">TermX character</option>
                      {usable.map(pack => (
                        <option key={pack.id} value={pack.id}>
                          {pack.name}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={'Pin ' + row.title}
                      checked={preferences.pinned.includes(row.id)}
                      onChange={() =>
                        change(current => {
                          const others = current.pinned.filter(id => id !== row.id);
                          return {pinned: current.pinned.includes(row.id) ? others : [...others, row.id]};
                        })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={'Show ' + row.title}
                      checked={!preferences.hidden.includes(row.id)}
                      onChange={() =>
                        change(current => {
                          const others = current.hidden.filter(id => id !== row.id);
                          return {hidden: current.hidden.includes(row.id) ? others : [...others, row.id]};
                        })
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
