"""Bounded human-facing rendered transaction evidence, never model policy."""
from urllib.parse import urlsplit, urlunsplit

from termx.agent.action_review import contains_credentials
from termx.agent.policy import redact

_COLLECT = r"""e => {
 const label=x=>[x.getAttribute('aria-label'),...(x.labels?Array.from(x.labels).slice(0,3).map(l=>l.innerText):[]),x.name,x.id].filter(Boolean).join(' ').slice(0,300);
 const secret=x=>x.type==='password'||x.type==='hidden'||/password|secret|token|cookie|authorization|api.?key|credential|csrf|card.?number|cvv|cvc|security.?code|one.time.code|cc-/i.test(label(x)+' '+(x.autocomplete||''));
 const root=e.form||e.closest('form,[data-transaction]');
 const visible=x=>{if(!x.getClientRects().length||x.closest('[hidden],[inert],[aria-hidden="true"]'))return false;for(let a=x;a;a=a.parentElement){const s=getComputedStyle(a);if(s.visibility!=='visible'||s.display==='none'||Number(s.opacity)===0)return false;}return true;};
 // Hidden protocol state is hashed by the existing document authority but
 // never read into the human/model preview. Visible credential/payment inputs
 // make the automated submission manual-only.
 const submitted=root?Array.from(root.querySelectorAll('input,textarea,select')).filter(x=>!x.disabled&&x.type!=='hidden'):[];
 const controls=submitted.filter(visible);
 const fields=controls.slice(0,21).map(x=>({label:label(x),type:x.type||x.tagName.toLowerCase(),value:secret(x)?null:(['checkbox','radio'].includes(x.type)?String(x.checked):String(x.value||'')).slice(0,1001),sensitive:secret(x),truncated:!secret(x)&&String(x.value||'').length>1000}));
 const marker=(name)=>{const attr=root?.getAttribute('data-'+name);if(attr)return attr.slice(0,301);const x=Array.from(root?.querySelectorAll('[data-'+name+'],[name="'+name+'"],[itemprop="'+(name==='amount'?'price':name==='merchant'?'seller':name)+'"]')||[]).find(x=>x.type!=='hidden'&&visible(x));return x?(x.getAttribute('data-'+name)||x.value||x.innerText||'').slice(0,301):null;};
 return {target_label:visible(e)?(e.getAttribute('aria-label')||e.innerText||label(e)||'').slice(0,501):'',target_tag:e.tagName.toLowerCase(),form_action:root?.action||null,fields,omitted_nonhidden_fields:submitted.length!==controls.length,too_many_fields:controls.length>20,merchant:marker('merchant'),amount:marker('amount')||marker('total'),currency:marker('currency'),account:marker('account')};
}"""


def _safe(value):
    if value is None:return None
    text=str(value)
    return '[redacted]' if contains_credentials(text) else redact(text)


async def approval_preview(page,tab,profile,action,args,effect,document_hash):
    """Unknown/secret/incomplete transactions stay a manual human operation."""
    row={'source':'host-rendered-target/v1','document_hash':document_hash,'effect':effect,
         'origin':urlunsplit((*urlsplit(tab['url'])[:2],'','','')),'profile_id':tab['profile_id'],
         'profile_name':_safe(profile.get('name','')),'operation':action,
         'consequence':{'purchase':'Places this exact order and may charge the shown amount.','send':'Submits the shown data to the shown website.','publish':'Publishes the shown content.','delete':'Deletes the shown target; this may be irreversible.','privilege':'Changes access or permissions for the shown target.','upload':'Uploads the explicitly approved file to this website.','export':'Exports data from this website.'}.get(effect,'The exact effect is unknown; take over to complete it manually.'),
         'manual_required':False,'reason':'Review the host-observed target and submitted fields; page labels are untrusted evidence.'}
    if effect in {'upload','export'}:
        # These use their dedicated explicit transfer reference and diagnostics
        # boundary; avoid guessing a rendered form transaction for a download.
        row['reference_id']=str(args.get('file_id') or args.get('download_id') or '')[:128]
        row['manual_required']=not bool(row['reference_id'])
        if row['manual_required']:row['reason']='No exact transfer reference is available. Take over to inspect and perform this export manually.'
        return row
    selector=args.get('selector')
    if not selector:
        row.update(manual_required=True,reason='No rendered target is available. Take over to inspect and complete this operation manually.')
        return row
    raw=await page.locator(selector).first.evaluate(_COLLECT)
    row.update(target_label=_safe(raw['target_label']),target_tag=raw['target_tag'],fields=[{'label':_safe(f['label']),'type':f['type'],'value':'[redacted]' if f['sensitive'] else _safe(f['value'])} for f in raw['fields']],
               merchant=_safe(raw['merchant']),amount=_safe(raw['amount']),currency=_safe(raw['currency']),website_account=_safe(raw['account']))
    if raw['form_action']:
        p=urlsplit(raw['form_action'])
        if p.username or p.password:
            row['destination']='[redacted credential-bearing destination]';row['manual_required']=True
        else:row['destination']=_safe(urlunsplit((p.scheme,p.netloc,p.path,'','')))
        if (p.scheme,p.netloc)!=(urlsplit(tab['url']).scheme,urlsplit(tab['url']).netloc) or contains_credentials(p.path):row['manual_required']=True
    if not row['target_label'] or len(raw['target_label'])>500 or raw['too_many_fields'] or raw['omitted_nonhidden_fields'] or any(f['sensitive'] or f['truncated'] or contains_credentials(f['value'] or '') for f in raw['fields']):row['manual_required']=True
    if effect=='purchase' and (not all(row.get(k) for k in ('merchant','amount','currency')) or any(len(str(row[k]))>300 for k in ('merchant','amount','currency'))):row['manual_required']=True
    if effect=='unknown':row['manual_required']=True
    if any(contains_credentials(raw.get(k) or '') for k in ('target_label','merchant','amount','currency','account')):row['manual_required']=True
    if row['manual_required']:row['reason']='The host cannot present a complete nonsensitive exact transaction inside this origin. Take over/private login to inspect and complete it manually.'
    return row
