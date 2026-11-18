export const wallpaperColourDefault = 0;

export const spectrumLooks = {
  selection:'tab',
  select:'trim',
  input:'wick',
  filter:'underline',
  notice:'stitch',
  primary:'spectrum',
  small:'open',
  status:'signal',
  advanced:'vein',
};

export function applySpectrumTheme(body: HTMLElement) {
  body.classList.add('paper-spectrum');
  for (const [name,value] of Object.entries(spectrumLooks)) body.dataset[name+'Look'] = value;
}
