import '@testing-library/jest-dom/vitest';

// jsdom's Blob has no arrayBuffer(); browsers and the desktop webview do.
if (typeof Blob.prototype.arrayBuffer !== 'function') {
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob) {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

// jsdom has no matchMedia; tests that care about a query install their own.
if (typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// CodeMirror measures selections and cursors with Range geometry that jsdom does not implement.
if (typeof Range !== 'undefined') {
  const empty = () => ({top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {return {};}});
  if (!Range.prototype.getClientRects) Range.prototype.getClientRects = (() => ({length: 0, item: () => null, [Symbol.iterator]: function* () {}})) as unknown as Range['getClientRects'];
  if (!Range.prototype.getBoundingClientRect) Range.prototype.getBoundingClientRect = empty as unknown as Range['getBoundingClientRect'];
}

// jsdom has no OS focus; component tests start in a foreground window.
Object.defineProperty(document, 'hasFocus', {configurable:true, value:()=>true});
