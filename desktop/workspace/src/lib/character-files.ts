/** Largest character pack upload the host accepts. Mirrors the 32 MiB limit in src/termx/characters/http.py. */
export const MAX_CHARACTER_FILE_BYTES = 32 * 1024 * 1024;
/** Largest PNG (sprite sheet or frame) the host decodes. Mirrors MAX_ATLAS_BYTES in src/termx/characters/packs.py. */
export const MAX_PNG_BYTES = 16 * 1024 * 1024;
/** Longest atlas side the host decodes. Mirrors MAX_ATLAS_EDGE in src/termx/characters/packs.py; 4096 x 4096 is exactly its 16-megapixel cap. */
export const MAX_IMAGE_EDGE = 4096;

export type CharacterFileKind = 'pack' | 'png';

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const ZIP_SIGNATURE = [0x50, 0x4b];
const HEAD_BYTES = 24;

const readBytes = async (blob: Blob) => new Uint8Array(await blob.arrayBuffer());

const startsWith = (bytes: Uint8Array, signature: number[]) => signature.every((byte, index) => bytes[index] === byte);

/**
 * Checks a file before it is uploaded and returns the message to show, or null when it looks usable.
 * The host validates again; this only saves a slow upload that is certain to fail.
 */
export async function validateCharacterFile(file: File, kind: CharacterFileKind): Promise<string | null> {
  if (file.size === 0) return 'That file is empty.';
  const limit = kind === 'pack' ? MAX_CHARACTER_FILE_BYTES : MAX_PNG_BYTES;
  if (file.size > limit) return 'That file is larger than ' + limit / (1024 * 1024) + ' MiB.';
  let head: Uint8Array;
  try {
    head = await readBytes(file.slice(0, HEAD_BYTES));
  } catch {
    return 'Could not read that file.';
  }
  if (kind === 'pack') {
    const named = /\.(termxpet|zip)$/i.test(file.name);
    if (!named || !startsWith(head, ZIP_SIGNATURE)) return 'Choose a .termxpet or .zip character pack.';
    return null;
  }
  const typed = file.type === 'image/png' || (!file.type && /\.png$/i.test(file.name));
  if (!typed || !startsWith(head, PNG_SIGNATURE)) return 'Choose a PNG image.';
  if (head.length < HEAD_BYTES) return 'That PNG image is incomplete.';
  const view = new DataView(head.buffer, head.byteOffset, head.byteLength);
  const width = view.getUint32(16);
  const height = view.getUint32(20);
  if (width < 1 || height < 1) return 'That PNG image has no pixels.';
  if (width > MAX_IMAGE_EDGE || height > MAX_IMAGE_EDGE) return `That image is ${width} by ${height} pixels. Each side can be at most ${MAX_IMAGE_EDGE}.`;
  return null;
}
