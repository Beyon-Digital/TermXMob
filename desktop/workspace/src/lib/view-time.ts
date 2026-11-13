/** Dates in the Audit panel and conflict messages: "13 Nov 2026, 10:01" in the person's own time zone, or just the time of day. */
export function formatWhen(iso: string, timeOnly = false): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const time = date.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});
  return timeOnly ? time : date.toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}) + ', ' + time;
}
/** "0.3 s", "12 s", "1 min 5 s". Null when the run has not finished. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return '';
  if (ms < 1000) return (ms / 1000).toFixed(1) + ' s';
  const seconds = Math.round(ms / 1000);
  return seconds < 60 ? seconds + ' s' : Math.floor(seconds / 60) + ' min ' + (seconds % 60) + ' s';
}
