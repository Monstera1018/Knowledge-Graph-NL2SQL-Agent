import { animate, group, query, style, transition, trigger } from '@angular/animations';

const EASE = 'cubic-bezier(0.32, 0.72, 0, 1)';

/** 侧栏内页面切换：旧页上移淡出，新页自下淡入。 */
export const pageTransition = trigger('pageTransition', [
  transition('void => *', []),
  transition('* <=> *', [
    style({ position: 'relative' }),
    query(
      ':enter, :leave',
      [
        style({
          position: 'absolute',
          inset: '0',
          width: '100%',
          height: '100%',
          overflow: 'hidden',
          pointerEvents: 'none',
        }),
      ],
      { optional: true },
    ),
    query(':enter', [style({ opacity: 0, transform: 'translateY(14px)' })], { optional: true }),
    group([
      query(
        ':leave',
        [animate(`200ms ${EASE}`, style({ opacity: 0, transform: 'translateY(-8px)' }))],
        { optional: true },
      ),
      query(
        ':enter',
        [animate(`320ms 30ms ${EASE}`, style({ opacity: 1, transform: 'none' }))],
        { optional: true },
      ),
    ]),
  ]),
]);

/** 对话会话切换：同一滚动容器内淡入，避免打断 ResizeObserver。 */
export const sessionTransition = trigger('sessionTransition', [
  transition(
    (fromState, toState) =>
      Boolean(fromState) &&
      fromState !== 'void' &&
      Boolean(toState) &&
      fromState !== toState,
    [
      style({ opacity: 0, transform: 'translateY(12px)' }),
      animate(`300ms ${EASE}`, style({ opacity: 1, transform: 'none' })),
    ],
  ),
]);
