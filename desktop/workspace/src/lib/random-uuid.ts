/**
 * `crypto.randomUUID` only exists in secure contexts. Safari and WebKit webviews
 * on `http://127.0.0.1`, and any plain-HTTP LAN address, leave it undefined, and
 * several call sites run while the app mounts. `getRandomValues` is available
 * everywhere, so build RFC 4122 version 4 identifiers from it.
 */
export function randomUuidV4(source: Pick<Crypto, 'getRandomValues'> = globalThis.crypto): string {
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
}

export function installRandomUuid(target: Crypto | undefined = globalThis.crypto): void {
  if (!target || typeof target.randomUUID === 'function') return;
  Object.defineProperty(target, 'randomUUID', {
    value: () => randomUuidV4(target),
    configurable: true,
    writable: true,
  });
}

installRandomUuid();
