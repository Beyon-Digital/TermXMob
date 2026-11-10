/** "Catalog refreshed 5 minutes ago", or a prompt to refresh when discovery has never run. `timestamp` is epoch seconds. */
export function catalogAge(timestamp: number | null | undefined, now = Date.now()) {
  if (timestamp == null) return 'Models have not been discovered yet. Choose Refresh models to load them.';
  const seconds = Math.max(0, Math.round(now / 1000 - timestamp));
  if (seconds < 60) return 'Catalog refreshed just now.';
  const unit = (count: number, name: string) => `Catalog refreshed ${count} ${name}${count === 1 ? '' : 's'} ago.`;
  if (seconds < 3600) return unit(Math.floor(seconds / 60), 'minute');
  if (seconds < 86400) return unit(Math.floor(seconds / 3600), 'hour');
  return unit(Math.floor(seconds / 86400), 'day');
}
