import {describe, expect, it} from 'vitest';
import {MAX_CHARACTER_FILE_BYTES, MAX_PNG_BYTES, validateCharacterFile} from './character-files';

function png(width: number, height: number, options: {name?: string; type?: string} = {}) {
  const bytes = new Uint8Array(33);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return new File([bytes], options.name ?? 'sheet.png', {type: options.type ?? 'image/png'});
}

const zip = (name: string) => new File([new Uint8Array([0x50, 0x4b, 3, 4, 0, 0])], name);
const oversize = (file: File, bytes: number) => Object.defineProperty(file, 'size', {value: bytes});

describe('character pack files', () => {
  it('accepts a .termxpet or .zip archive', async () => {
    expect(await validateCharacterFile(zip('friend.termxpet'), 'pack')).toBeNull();
    expect(await validateCharacterFile(zip('friend.ZIP'), 'pack')).toBeNull();
  });

  it('rejects empty files, files over 32 MiB, wrong extensions and non-zip content', async () => {
    expect(await validateCharacterFile(new File([], 'empty.zip'), 'pack')).toBe('That file is empty.');
    expect(await validateCharacterFile(oversize(zip('big.zip'), MAX_CHARACTER_FILE_BYTES + 1), 'pack')).toBe('That file is larger than 32 MiB.');
    expect(await validateCharacterFile(oversize(zip('exact.zip'), MAX_CHARACTER_FILE_BYTES), 'pack')).toBeNull();
    expect(await validateCharacterFile(zip('notes.txt'), 'pack')).toBe('Choose a .termxpet or .zip character pack.');
    expect(await validateCharacterFile(new File(['plain text'], 'fake.zip'), 'pack')).toBe('Choose a .termxpet or .zip character pack.');
  });
});

describe('PNG files', () => {
  it('accepts a PNG within the atlas limits, including exactly 4096 pixels on a side', async () => {
    expect(await validateCharacterFile(png(256, 128), 'png')).toBeNull();
    expect(await validateCharacterFile(png(4096, 4096), 'png')).toBeNull();
    expect(await validateCharacterFile(png(4097, 1), 'png')).toMatch(/4097 by 1 pixels/);
  });

  it('accepts an untyped file that is named .png', async () => {
    expect(await validateCharacterFile(png(32, 32, {type: ''}), 'png')).toBeNull();
  });

  it('rejects the wrong type, a bad signature, and dimensions the host would refuse', async () => {
    expect(await validateCharacterFile(png(32, 32, {type: 'image/jpeg'}), 'png')).toBe('Choose a PNG image.');
    expect(await validateCharacterFile(new File(['not a png at all, just text'], 'a.png', {type: 'image/png'}), 'png')).toBe('Choose a PNG image.');
    expect(await validateCharacterFile(png(5000, 100), 'png')).toMatch(/5000 by 100 pixels/);
    expect(await validateCharacterFile(png(0, 100), 'png')).toBe('That PNG image has no pixels.');
    expect(await validateCharacterFile(oversize(png(32, 32), MAX_PNG_BYTES + 1), 'png')).toBe('That file is larger than 16 MiB.');
    expect(await validateCharacterFile(oversize(png(32, 32), MAX_PNG_BYTES), 'png')).toBeNull();
  });

  it('rejects a PNG cut off before its header ends', async () => {
    const head = png(32, 32);
    expect(await validateCharacterFile(new File([head.slice(0, 20)], 'cut.png', {type: 'image/png'}), 'png')).toBe('That PNG image is incomplete.');
  });
});
