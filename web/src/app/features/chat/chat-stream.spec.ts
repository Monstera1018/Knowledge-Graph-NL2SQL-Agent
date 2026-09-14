import { describe, expect, it } from 'vitest';

import type { ChatAssistantMessage, StreamEvent, ToolPart } from '../../core/models/chat.models';
import { applyStreamEvent } from './chat-stream';

function message(parts: ChatAssistantMessage['parts'] = []): ChatAssistantMessage {
  return {
    id: 'a1',
    role: 'assistant',
    parts,
    status: 'streaming',
    createdAt: '2026-09-14T03:00:00.000Z',
  };
}

function event(partial: Partial<StreamEvent> & Pick<StreamEvent, 'type'>): StreamEvent {
  return {
    v: 1,
    session_id: 's1',
    turn_id: 't1',
    ...partial,
  };
}

describe('applyStreamEvent tool timing', () => {
  it('fills started_at from part_start event ts when missing', () => {
    const next = applyStreamEvent(
      message(),
      event({
        type: 'part_start',
        ts: '2026-09-14T03:00:01.000Z',
        part: {
          kind: 'tool',
          id: 'tool-search',
          name: 'search',
          phase: 'call',
          title: '检索上下文',
        },
      }),
    );
    const part = next.parts[0] as ToolPart;
    expect(part.started_at).toBe('2026-09-14T03:00:01.000Z');
  });

  it('records duration when phase becomes result', () => {
    const started = applyStreamEvent(
      message(),
      event({
        type: 'part_start',
        ts: '2026-09-14T03:00:01.000Z',
        part: {
          kind: 'tool',
          id: 'tool-search',
          name: 'search',
          phase: 'call',
          title: '检索上下文',
          started_at: '2026-09-14T03:00:01.000Z',
        },
      }),
    );
    const finished = applyStreamEvent(
      started,
      event({
        type: 'part_delta',
        ts: '2026-09-14T03:00:02.250Z',
        part_id: 'tool-search',
        patch: { op: 'field_set', path: 'phase', value: 'result' },
      }),
    );
    const part = finished.parts[0] as ToolPart;
    expect(part.phase).toBe('result');
    expect(part.duration_ms).toBe(1250);
    expect(part.ended_at).toBe('2026-09-14T03:00:02.250Z');
  });

  it('applies backend duration_ms field_set', () => {
    const started = applyStreamEvent(
      message(),
      event({
        type: 'part_start',
        part: {
          kind: 'tool',
          id: 'tool-search',
          name: 'search',
          phase: 'call',
          title: '检索上下文',
          started_at: '2026-09-14T03:00:01.000Z',
        },
      }),
    );
    const withPhase = applyStreamEvent(
      started,
      event({
        type: 'part_delta',
        part_id: 'tool-search',
        patch: { op: 'field_set', path: 'phase', value: 'result' },
      }),
    );
    const withDuration = applyStreamEvent(
      withPhase,
      event({
        type: 'part_delta',
        part_id: 'tool-search',
        patch: { op: 'field_set', path: 'duration_ms', value: 880 },
      }),
    );
    expect((withDuration.parts[0] as ToolPart).duration_ms).toBe(880);
  });
});
