import '@testing-library/jest-dom/vitest';

// jsdom has no OS focus; component tests start in a foreground window.
Object.defineProperty(document, 'hasFocus', {configurable:true, value:()=>true});
