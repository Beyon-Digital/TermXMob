import {vi} from 'vitest';

/** jsdom has no layout, so @tanstack/react-virtual sees a 0 x 0 scroller and renders no rows. Give grid scrollers a size. */
export function sizeGridScrollers(width = 800, height = 400) {
  const size = (axis: 'offsetWidth' | 'offsetHeight', value: number) => vi.spyOn(HTMLElement.prototype, axis, 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('data-grid-scroll') ? value : 0;
  });
  size('offsetWidth', width); size('offsetHeight', height);
}
