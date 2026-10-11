export function taskState(status = '') {
 const labels:Record<string,string>={queued:'Queued',planning:'Planning',running:'Running',awaiting_approval:'Needs approval',paused:'Paused',recovering:'Recovering',recovery_confirmation_required:'Needs recovery decision',cancelling:'Stopping',cancelled:'Cancelled',failed:'Failed',completed:'Completed'};
 return {label:labels[status]||'Ready to start',attention:['awaiting_approval','recovery_confirmation_required','failed','paused'].includes(status),active:['queued','planning','running','recovering','cancelling'].includes(status),tone:status==='failed'?'failed':status==='completed'?'completed':['awaiting_approval','recovery_confirmation_required','paused'].includes(status)?'attention':'running'};
}
