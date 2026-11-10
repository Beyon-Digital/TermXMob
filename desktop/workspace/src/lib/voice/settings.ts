import {gql, request, json} from '../api';

export interface VoiceVoice {
  id: string;
  label: string;
}
export interface Attribution {
  text: string;
  url: string;
}
export interface VoiceEngineStatus {
  id: string;
  label: string;
  kind: 'local' | 'provider';
  state: 'ready' | 'unavailable' | 'not_installed' | 'downloading' | 'error';
  detail: string;
  installed: boolean;
  installable: boolean;
  voices: VoiceVoice[];
  size_bytes?: number;
  progress?: {done: number; total: number} | null;
  revision?: string;
  attribution?: Attribution;
  license?: {name: string; sha256: string; noncommercial: boolean; contact: string; url: string};
  accounts?: {id: string; name: string; base_url?: string | null}[];
}
export interface VoiceSettings {
  engine: 'browser' | 'woof' | 'provider';
  provider: string | null;
  model: string;
  transcribe_model: string;
  voice: string;
  auto_send: boolean;
  auto_speak: boolean;
  cloud_fallback: boolean;
  keep_audio: boolean;
  consent: {provider?: {id: string; at: number}; woof?: {sha256: string; at: number}; live?: {id: string; at: number}};
  live_provider?: string | null;
  live_model?: string;
  live_voice?: string;
  live_transcribe_model?: string;
}
/** Live conversation readiness: accounts that declare realtime support, and whether this owner can start now. */
export interface LiveStatus {
  available: boolean;
  configured: boolean;
  acknowledged: boolean;
  accounts: {id: string; name: string; base_url?: string | null}[];
}
export interface VoiceStatus {
  available: boolean;
  settings: VoiceSettings;
  engines: VoiceEngineStatus[];
  stt: {configured: boolean; provider: string | null};
  live?: LiveStatus;
}
export interface VoiceSettingsChange {
  engine?: VoiceSettings['engine'];
  provider?: string;
  model?: string;
  transcribe_model?: string;
  voice?: string;
  auto_send?: boolean;
  auto_speak?: boolean;
  cloud_fallback?: boolean;
  keep_audio?: boolean;
  consent_provider?: boolean;
  live_provider?: string;
  live_model?: string;
  live_voice?: string;
  live_transcribe_model?: string;
  consent_live?: boolean;
  accept_woof_license?: string;
}
export interface WoofLicense {
  revision: string;
  name: string;
  contact: string;
  license: {text: string; sha256: string};
  notice: {text: string; sha256: string};
  attribution: Attribution;
}

export const BROWSER_ATTRIBUTION_NONE = null;

/** The parsed address of an API account, or null when it has none or is not a URL. */
export function hostOf(url?: string | null) {
  try {
    return url ? new URL(url) : null;
  } catch {
    return null;
  }
}
export const isLocal = (host: string) => ['localhost', '127.0.0.1', '[::1]', '::1'].includes(host);
const CHANGED = 'termx-voice-changed';

/** Tells the composer and the settings page that the saved voice settings or an engine changed. */
export function announceVoiceChange() {
  window.dispatchEvent(new Event(CHANGED));
}
export function onVoiceChange(listener: () => void) {
  window.addEventListener(CHANGED, listener);
  return () => window.removeEventListener(CHANGED, listener);
}

const FIELDS = '{available settings engines stt live}';

export async function loadVoiceStatus(): Promise<VoiceStatus> {
  return (await gql<{voice_status: VoiceStatus}>(`{voice_status${FIELDS}}`)).voice_status;
}

export async function saveVoiceSettings(input: VoiceSettingsChange): Promise<VoiceStatus> {
  const data = await gql<{save_voice_settings: VoiceStatus}>(
    `mutation($input:VoiceSettingsInput!){save_voice_settings(input:$input)${FIELDS}}`,
    {input},
  );
  announceVoiceChange();
  return data.save_voice_settings;
}

export const loadWoofLicense = () => request<WoofLicense>('/api/voice/engines/woof/license');

export async function downloadWoof(licenseSha256: string) {
  await request('/api/voice/engines/woof/download', json('POST', {license_sha256: licenseSha256}));
  announceVoiceChange();
}

export async function removeWoof() {
  await request('/api/voice/engines/woof', {method: 'DELETE'});
  announceVoiceChange();
}
