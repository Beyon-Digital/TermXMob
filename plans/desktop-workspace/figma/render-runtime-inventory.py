"""Render the human design handoff from the source-mapped inventory.

Keeps original design provenance, actual external node identity and scoped runtime
qualification separate. Run from any directory; no Figma API or runtime effects.
"""
from pathlib import Path
import json,os
BASE=Path(__file__).resolve().parents[1]
ROOT=BASE.parents[1]
def links(values):
    return ', '.join(f'[{Path(value).name}]({os.path.relpath(ROOT/value,BASE)})' for value in dict.fromkeys(values)) or 'No independent record is claimed.'
def main():
    inventory=json.loads((BASE/'design-runtime-coverage.json').read_text());external=json.loads((BASE/'verification/advanced-figma.json').read_text())
    state_count=external['canonical_state_frames'];edge_count=external['actual_prototype_edges']
    out=['# TermX desktop runtime design handoff','',f"Inventory `{inventory['inventory_id']}` maps all seven section 12 design families of the [approved v0.3 HTML](plan-v0.3.html#figma) to actual routes, fields, records, permissions, states and connected journeys.",'',f'The [original manifest](design-manifest.json) preserves eleven core screen IDs, sixty-six components and their first-pass review status. The advanced extension is a separately verified external Figma design: **{state_count} editable connected states, four compact layouts, four shared components and {edge_count} stored navigation edges**. All states are reachable from entry; original 63 prototype edges remain intact. [Actual node/prototype verification](verification/advanced-figma.json) establishes design provenance; runtime fixtures and installed native/provider qualification establish their own independent scope.','','The [machine-readable coverage](design-runtime-coverage.json) and [schema](design-runtime-coverage-schema.json) map stable `DF-*`, `UI-*` and `J-*` identifiers. This inventory complements [acceptance](acceptance.json) and the [support matrix](support-matrix.md). Credentials remain host/OS owned; no external click analytics are invented.']
    for family in inventory['families']:
        out+=['',f"## {family['id']} · {family['title']}"]
        for s in family['surfaces']:
            out+=['',f"### {s['id']} · {s['title']}",'',f"Coverage: **{s['coverage']}**. Original Figma screens: {', '.join(s['figma_screen_ids']) or 'No original core frame assigned; advanced nodes below are independently verified.'}",'']
            if s['endpoints']:out.append('**Entry points:** '+ '; '.join(s['endpoints'])+'.')
            else:out.append('**Entry points:** Authenticated workspace controls and saved preferences in the mapped source; no independent HTTP endpoint.')
            out+=['','**Records:** '+('; '.join(s['data_records']) or 'State is managed by the enclosing workspace surface.')+'.','','**Fields:** '+ '; '.join(s['fields'])+'.','','**Permission contract:** '+ '; '.join(s['permissions'])+'.','','**States:** '+ '; '.join(s['states'])+'.','','**Code:** '+links(s['code_paths'])+'.','','**Evidence:** '+links(s['proof_links'])+'.']
            ids=s.get('advanced_figma_state_ids',[]);nodes=s.get('advanced_figma_node_ids',[])
            if ids:out+=['','**Editable advanced states:** '+', '.join(f'[{sid}](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id={node.replace(":","-")})' for sid,node in zip(ids,nodes))+'.']
            out+=['','**Connected journeys:** '+', '.join(s['journey_ids'])+'. Local execution/security records: '+links(s['local_event_links'])+' Per-click analytics are not claimed.']
            if s['limits'].strip():out+=['','**Limits:** '+s['limits'].strip()]
    out+=['','## Connected runtime and editable design journeys']
    for j in inventory['journeys']:
        actual=next(x for x in external['journeys'] if x['id']==j['id'])
        out+=['',f"### {j['id']} · {j['title']}",'',' → '.join(j['steps'])+'.','',f"Surfaces: {', '.join(j['surfaces'])}. Evidence: {links(j['proof_links'])}.",'',f"[Editable prototype]({actual['prototype_url']}) · {actual['state_count']} verified state frames."]
    out+=['','## Provenance and qualification boundaries','','[Connected canvas](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-2) reuses original semantic tokens, Geist/Geist Mono typography, workspace panels and editable form patterns. No flattened screenshot substitutes for these states. [Exact node metadata](figma/advanced-node-map.json) separates the canonical connected page from preserved references. Prototype fields are illustrative and do not submit credentials or execute tools.','','[Inspected renders](figma/advanced-renders/) include entry failure, delivery review, Voice, detached draft conflicts, remembered-rule edits, supervised children, failed skill tests, native-source CAS conflicts, HTTPS provenance review and compact layouts.']
    for gate in inventory['release_gates']:out+=['','- '+gate]
    if inventory['omissions']:
        out+=['','## Assigned runtime omissions']
        for item in inventory['omissions']:out+=['',f"- **{item['id']}** ({item['status']}): {item['description']}"]
    out+=['','## Resolved audit findings']
    for item in inventory.get('resolved_audit_findings',[]):out+=['',f"- **{item['id']}**: {item['resolution']} Evidence: {links(item['proof_links'])}."]
    (BASE/'design-runtime-coverage.md').write_text('\n'.join(out)+'\n')
if __name__=='__main__':main()
