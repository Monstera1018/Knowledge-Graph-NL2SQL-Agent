import { describe, expect, it } from 'vitest';

import type { ToolPart } from '../../core/models/chat.models';
import { formatToolDurationMs, mergeToolTiming, toolPartDurationMs } from './tool-duration';

function toolPart(overrides: Partial<ToolPart> = {}): ToolPart {
  return {
    kind: 'tool',
    id: 't1',
    name: 'search',
    phase: 'result',
    title: '检索上下文',
    ...overrides,
  };
}

describe('tool duration', () => {
  it('formats sub-10s with one decimal second', () => {
    expect(formatToolDurationMs(0)).toBe('0.0 秒');
    expect(formatToolDurationMs(1234)).toBe('1.2 秒');
    expect(formatToolDurationMs(9800)).toBe('9.8 秒');
  });

  it('formats minutes after one minute', () => {
    expect(formatToolDurationMs(12_400)).toBe('12 秒');
    expect(formatToolDurationMs(65_000)).toBe('1 分 05 秒');
  });

  it('prefers duration_ms over started/ended timestamps', () => {
    expect(
      toolPartDurationMs(
        toolPart({
          started_at: '2026-09-14T03:00:00.000Z',
          ended_at: '2026-09-14T03:00:05.000Z',
          duration_ms: 800,
        }),
      ),
    ).toBe(800);
  });

  it('sums merged completed steps and uses the running step start while in progress', () => {
    const completed = toolPart({
      id: 'planner',
      name: 'planner',
      started_at: '2026-09-14T03:00:00.000Z',
      ended_at: '2026-09-14T03:00:00.400Z',
      duration_ms: 400,
    });
    const running = toolPart({
      id: 'parse',
      name: 'parse_plan',
      phase: 'call',
      started_at: '2026-09-14T03:00:08.000Z',
    });
    expect(mergeToolTiming([completed, running])).toEqual({
      started_at: '2026-09-14T03:00:08.000Z',
    });
    expect(
      mergeToolTiming([
        completed,
        toolPart({
          id: 'parse',
          name: 'parse_plan',
          duration_ms: 250,
        }),
      ]),
    ).toEqual({
      started_at: '2026-09-14T03:00:00.000Z',
      ended_at: '2026-09-14T03:00:00.400Z',
      duration_ms: 650,
    });
  });
});
