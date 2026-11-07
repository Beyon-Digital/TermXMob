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

// jsdom has no OS focus; component tests start in a foreground window.
Object.defineProperty(document, 'hasFocus', {configurable:true, value:()=>true});
