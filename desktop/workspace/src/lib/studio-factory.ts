export type FactoryPlatform = 'vertical' | 'feed' | 'square' | 'wide';
export type FactoryStyle = 'cinematic' | 'ugc' | 'product' | 'bright' | 'moody';
export const factoryPlatforms: Record<FactoryPlatform, {label: string; ratios: string[]}> = {
  vertical: {label: 'Reels / TikTok / Shorts', ratios: ['9:16']},
  feed: {label: 'Feed post', ratios: ['4:5', '3:4', '1:1']},
  square: {label: 'Square', ratios: ['1:1']},
  wide: {label: 'YouTube / web', ratios: ['16:9']},
};
export const factoryStyles: Record<FactoryStyle, {label: string; prompt: string}> = {
  cinematic: {label: 'Cinematic', prompt: 'cinematic lighting, shallow depth of field, rich colour grade'},
  ugc: {label: 'UGC / handheld', prompt: 'authentic handheld phone-camera look, natural light, casual and real'},
  product: {label: 'Product showcase', prompt: 'clean studio lighting, crisp product detail, smooth motion'},
  bright: {label: 'Bright & playful', prompt: 'bright saturated colours, playful energy, upbeat pacing'},
  moody: {label: 'Moody', prompt: 'low-key lighting, deep shadows, atmospheric and dramatic'},
};
const angles = [
  'Open on a striking visual hook in the first second, then reveal the subject.',
  'Close-up detail shot with slow, deliberate camera movement.',
  'Wide establishing shot with a smooth push-in toward the subject.',
  'Human point of view: someone interacting with the subject, natural reaction.',
  'Before-and-after style reveal with a clear transformation.',
  'Fast, rhythmic montage feel with dynamic motion and a satisfying finish.',
];
/** Adapted from vloop FactoryStudio: editable shot templates, without an AI-writing claim. */
export function buildFactoryScenes(brief: string, style: FactoryStyle, count: number) {
  if (!brief.trim() || brief.length > 10000 || !Number.isInteger(count) || count < 1 || count > 6 || !factoryStyles[style])
    throw new Error('Enter a brief and choose between one and six scenes.');
  return angles.slice(0, count).map((angle, index) => ({
    id: String(index + 1), title: 'Scene ' + (index + 1),
    prompt: [brief.trim(), angle, factoryStyles[style].prompt].join('\n\n'),
  }));
}
export function factoryRatio(platform: FactoryPlatform, supported: string[]) {
  const preference = factoryPlatforms[platform].ratios;
  const ratio = preference.find(value => supported.includes(value)) || supported[0] || '';
  return {ratio, exact: preference[0] === ratio};
}
