import { DestroyRef, signal } from '@angular/core';

/** 跟随系统“减少动态效果”设置，供过渡动画开关使用。 */
export function createReducedMotionSignal(destroyRef?: DestroyRef) {
  const media =
    typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const reduced = signal(Boolean(media?.matches));
  const onChange = (event: MediaQueryListEvent) => reduced.set(event.matches);
  media?.addEventListener('change', onChange);
  destroyRef?.onDestroy(() => media?.removeEventListener('change', onChange));
  return reduced;
}
