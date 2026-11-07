const keys = new WeakMap<object, string>();
let next = 0;

/** A React key for list items that have no id: tied to the object, so it survives removals and reordering. */
export function stableKey(item: object): string {
  let key = keys.get(item);
  if (!key) { key = 'k' + ++next; keys.set(item, key); }
  return key;
}
