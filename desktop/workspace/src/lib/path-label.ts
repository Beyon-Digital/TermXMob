/** Last folder of a host path, for compact display; the full path belongs in a tooltip. */
export function folderName(path?: string | null) {
  const trimmed = (path || '').replace(/[\\/]+$/, '');
  return trimmed.split(/[\\/]/).pop() || trimmed;
}
