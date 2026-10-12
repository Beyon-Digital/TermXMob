/* The privileged bridge is restricted to this bundled window by Tauri ACLs
   and a native origin check. Release notes are always rendered as plain text. */
const byId = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const sizes = (bytes) => bytes < 1048576 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
let action = null;
let sending = false;
let refreshTimer;
let actionError = '';
const states = {
  idle: ['Check for updates', 'Get the latest improvements for Termx Desktop.', 'Check for updates', 'check'],
  checking: ['Checking for updates…', 'Connecting to the Termx release service.', 'Checking…'],
  current: ['You’re up to date', 'You’re running the latest available version of Termx Desktop.', 'Check again', 'check'],
  available: ['An update is available', 'Download the latest version. You’ll choose when to install it.', 'Download update', 'download'],
  downloading: ['Downloading update…', 'You can continue working while the update downloads.', 'Downloading…'],
  verifying: ['Verifying download…', 'Checking the update’s signature before it can be installed.', 'Verifying…'],
  downloaded: ['Ready to install', 'The download is verified. Save your work before installing.', 'Install update', 'install'],
  installing: ['Installing update…', 'Applying the verified update. Please keep Termx open.', 'Installing…'],
  ready: ['Restart to finish', 'The update is installed. Save your work, then restart Termx to use the new version.', 'Restart Termx', 'restart'],
  restarting: ['Restarting Termx…', 'Your updated workspace will open shortly.', 'Restarting…'],
  error: ['Update couldn’t finish', 'Check your connection and try again. The details below explain which step failed.', 'Try again', 'check'],
};
function render(snapshot) {
  const [title, description, label, next] = states[snapshot.phase] || states.error;
  byId('title').textContent = title;
  byId('description').textContent = snapshot.windows && snapshot.phase === 'downloaded'
    ? 'Save your work. Installing will close Termx and launch the Windows installer.' : description;
  byId('current').textContent = snapshot.currentVersion || '—';
  byId('available-row').hidden = !snapshot.version;
  byId('available').textContent = snapshot.version || '';
  byId('release-notes').hidden = !snapshot.notes;
  byId('notes').textContent = snapshot.notes || '';
  byId('error').hidden = !(snapshot.error || actionError);
  byId('error').textContent = snapshot.error || actionError;
  const busy = ['checking', 'downloading', 'verifying', 'installing', 'restarting'].includes(snapshot.phase);
  byId('progress-region').hidden = !busy;
  const progress = byId('progress');
  const known = snapshot.phase === 'downloading' && snapshot.total > 0;
  if (known) {
    progress.max = snapshot.total;
    progress.value = Math.min(snapshot.downloaded, snapshot.total);
  } else {
    progress.removeAttribute('value');
    progress.removeAttribute('max');
  }
  byId('bytes').textContent = snapshot.phase === 'downloading'
    ? `${sizes(snapshot.downloaded)}${known ? ` of ${sizes(snapshot.total)} · ${Math.min(100, Math.floor(snapshot.downloaded / snapshot.total * 100))}%` : ' downloaded · total size not provided'}` : '';
  // Avoid announcing every downloaded chunk to screen readers.
  const status = snapshot.version ? `Termx ${snapshot.version} · ${title}` : title;
  if (byId('status').textContent !== status) byId('status').textContent = status;
  action = next || null;
  byId('action').textContent = label;
  byId('action').disabled = sending || !action;
  byId('hint').textContent = ['installing', 'restarting'].includes(snapshot.phase)
    ? 'Please wait for this step to finish.'
    : 'You can close this window. Reopen Check for Updates to return here.';
}
async function refresh() {
  clearTimeout(refreshTimer);
  try {
    render(await invoke('desktop_update_state'));
  } catch (error) {
    byId('title').textContent = 'Could not load update status';
    byId('error').hidden = false;
    byId('error').textContent = String(error);
    byId('action').textContent = 'Reconnect';
    byId('action').disabled = sending;
    action = 'reconnect';
  }
  refreshTimer = setTimeout(refresh, 500);
}
byId('action').addEventListener('click', async () => {
  if (sending || !action) return;
  sending = true;
  byId('action').disabled = true;
  try {
    actionError = '';
    if (action !== 'reconnect') await invoke('desktop_update_action', { action });
  } catch (error) {
    actionError = String(error);
  } finally {
    sending = false;
    await refresh();
  }
});
void refresh();
