import {describe, expect, it} from 'vitest';
import {installRandomUuid, randomUuidV4} from './random-uuid';

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('random uuid fallback', () => {
  it('builds RFC 4122 version 4 identifiers that do not repeat', () => {
    const ids = new Set(Array.from({length: 200}, () => randomUuidV4()));
    expect(ids.size).toBe(200);
    for (const id of ids) expect(id).toMatch(V4);
  });

  it('sets the version and variant bits whatever the random bytes are', () => {
    for (const fill of [0x00, 0xff]) {
      const constant = {getRandomValues: (array: Uint8Array) => array.fill(fill)} as unknown as Pick<Crypto, 'getRandomValues'>;
      const id = randomUuidV4(constant);
      expect(id).toMatch(V4);
    }
  });

  it('adds randomUUID where the context lacks it and leaves a native one alone', () => {
    const insecure = {getRandomValues: crypto.getRandomValues.bind(crypto)} as unknown as Crypto;
    expect(typeof insecure.randomUUID).toBe('undefined');
    installRandomUuid(insecure);
    expect(insecure.randomUUID()).toMatch(V4);

    const native = () => '00000000-0000-4000-8000-000000000000' as const;
    const secure = {getRandomValues: crypto.getRandomValues.bind(crypto), randomUUID: native} as unknown as Crypto;
    installRandomUuid(secure);
    expect(secure.randomUUID).toBe(native);
  });
});
