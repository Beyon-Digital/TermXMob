import {useEffect, useState} from 'react';
import CharacterSprite from '../../components/CharacterSprite';
import {request} from '../../lib/api';
import {parseCompanionPets, type CompanionPet, type CompanionRuntime} from '../../lib/characters';

/**
 * The character beside the Main agent row, when the person turned it on in Settings > Characters. It is one still frame: no
 * walking, no looping animation, and it is hidden from the accessibility tree because it carries no information.
 */
export default function MainAgentSprite({primaryId}: {primaryId: string}) {
  const [pet, setPet] = useState<CompanionPet | null>(null);
  useEffect(() => {
    let live = true;
    request<CompanionRuntime>('/api/companions/runtime')
      .then(value => {
        if (!live) return;
        const pets = parseCompanionPets(value);
        setPet(pets.find(row => row.session_id === primaryId) ?? pets[0] ?? null);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [primaryId]);
  if (!pet) return null;
  return (
    <span className="agent-sprite" aria-hidden="true">
      <CharacterSprite packId={pet.pack_id} revision={pet.pack_revision} state="idle" size={24} frame={0} paused reducedMotion lazy />
    </span>
  );
}
