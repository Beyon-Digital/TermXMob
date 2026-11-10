import {gql} from '../lib/api';

export type DesktopIssue = {code: string; message: string; action: string};
export type DesktopPermission = {state: 'granted' | 'denied' | 'unknown' | 'not_required' | 'unavailable' | string; guidance: string; grant_to: string | null};
export type DesktopInstall = {available: boolean; asset: string | null; size_bytes: number | null; host: string; note: string};
/** schema.graphql `DesktopControlStatus`. */
export type DesktopStatus = {
  installed: boolean; source: 'managed' | 'configured' | 'path' | null; path: string | null; version: string | null; pinned_version: string;
  verified: boolean; helper_ok: boolean | null; platform: string; arch: string; platform_supported: boolean; platform_reason: string | null;
  envelope_version: string | null; ready: boolean; permission: DesktopPermission; supported_surfaces: string[]; issues: DesktopIssue[]; install: DesktopInstall;
  /** The path the owner saved, if any. */
  configured_path: string | null;
  /** True when TERMX_AGENT_DESKTOP is set, which wins over a saved path. */
  setting_locked: boolean;
};

export const DESKTOP_FIELDS = `installed source path version pinned_version verified helper_ok platform arch platform_supported platform_reason envelope_version ready
 permission { state guidance grant_to } supported_surfaces issues { code message action } install { available asset size_bytes host note }
 configured_path setting_locked`;

export async function loadDesktopStatus(refresh: boolean): Promise<DesktopStatus> {
  const result = await gql<{desktop_control_status: DesktopStatus}>(`query DesktopControl($refresh: Boolean!) { desktop_control_status(refresh: $refresh) { ${DESKTOP_FIELDS} } }`, {refresh});
  return result.desktop_control_status;
}

export async function installDesktopControl(version: string): Promise<DesktopStatus> {
  const result = await gql<{desktop_control_install: DesktopStatus}>(`mutation DesktopControlInstall($input: DesktopControlInstallInput!) { desktop_control_install(input: $input) { ${DESKTOP_FIELDS} } }`, {input: {version, consent: true}});
  return result.desktop_control_install;
}

export async function removeDesktopControl(): Promise<DesktopStatus> {
  const result = await gql<{desktop_control_remove: DesktopStatus}>(`mutation DesktopControlRemove { desktop_control_remove { ${DESKTOP_FIELDS} } }`);
  return result.desktop_control_remove;
}

/** An empty path goes back to the managed copy. */
export async function setDesktopControlPath(path: string): Promise<DesktopStatus> {
  const result = await gql<{desktop_control_set_path: DesktopStatus}>(`mutation DesktopControlPath($path: String) { desktop_control_set_path(path: $path) { ${DESKTOP_FIELDS} } }`, {path});
  return result.desktop_control_set_path;
}

/** The non-admin view: the cached, path-free flag the host puts in the machine capabilities. */
export async function loadDesktopReady(): Promise<boolean | null> {
  const result = await gql<{machine?: {capabilities?: {agent_desktop?: {ready?: boolean}}}}>('{ machine { capabilities } }');
  const ready = result.machine?.capabilities?.agent_desktop?.ready;
  return typeof ready === 'boolean' ? ready : null;
}

/** Local check before a path is sent. The host validates again: it is the only one that can open the file. */
export function pathProblem(value: string): string {
  const path = value.trim();
  const absolute = /^\//.test(path) || /^[A-Za-z]:[\\/]/.test(path) || /^\\\\[^\\]/.test(path);
  // eslint-disable-next-line no-control-regex
  if (!path || !absolute || path.length > 1024 || /[\u0000-\u001f\u007f]/.test(path)) return 'Enter the full path to the program: it starts with / on macOS and Linux, or with a drive letter such as C:\\ on Windows.';
  return '';
}

export function megabytes(bytes: number | null): string {
  return bytes ? (bytes / 1_000_000).toFixed(1) + ' MB' : 'size unknown';
}
