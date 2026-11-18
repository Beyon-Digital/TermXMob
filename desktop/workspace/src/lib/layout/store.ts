import {LIMITS, validateBook, type Book, type ValidateOptions} from './model';
import {migrateV1} from './migrate-v1';

/**
 * Where the layout book is kept: `termx-layout-v2:<json [owner, device]>`, the same owner and device keying as the v1
 * presets (`termx-layout-presets-v1:...`) and the same device id, so a profile finds its own layout on this machine.
 * The v1 key is read once, when no v2 book exists yet, and is never written or removed (it is the rollback copy).
 */

const DEVICE_KEY = 'termx-layout-device-v1';
export const V1_PREFIX = 'termx-layout-presets-v1:';
export const V2_PREFIX = 'termx-layout-v2:';

export function layoutDevice(storage: Storage = localStorage): string {
  let value = storage.getItem(DEVICE_KEY);
  if (!value || !/^[a-zA-Z0-9-]{8,128}$/.test(value)) {
    value = crypto.randomUUID();
    storage.setItem(DEVICE_KEY, value);
  }
  return value;
}

export class LayoutBookStore {
  readonly key: string;
  readonly v1Key: string;

  constructor(
    readonly owner: string,
    readonly storage: Storage = localStorage,
    readonly device: string = layoutDevice(storage),
    private readonly options: ValidateOptions = {},
  ) {
    const scope = JSON.stringify([owner, device]);
    this.key = V2_PREFIX + scope;
    this.v1Key = V1_PREFIX + scope;
  }

  /** The stored book, else the migration of the v1 book, else a fresh one. Never throws and never writes. */
  read(): Book {
    try {
      const raw = this.storage.getItem(this.key);
      if (raw && raw.length <= LIMITS.bytes) {
        const book = validateBook(JSON.parse(raw), this.options);
        if (book) return book;
      }
    } catch {
      // A corrupt v2 book falls through to the migration, which also yields a usable default.
    }
    try {
      return migrateV1(this.storage.getItem(this.v1Key), this.owner, this.device);
    } catch {
      return migrateV1(null, this.owner, this.device);
    }
  }

  write(book: Book): boolean {
    try {
      const text = JSON.stringify(book);
      if (text.length > LIMITS.bytes) return false;
      this.storage.setItem(this.key, text);
      return true;
    } catch {
      return false;
    }
  }
}
