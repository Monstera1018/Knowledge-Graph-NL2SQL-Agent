import type {
  ChatAssistantMessage,
  MessagePart,
  PartPatch,
  StreamEvent,
  WorkflowStatus,
} from '../../core/models/chat.models';

export function applyStreamEvent(
  message: ChatAssistantMessage,
  event: StreamEvent,
): ChatAssistantMessage {
  switch (event.type) {
    case 'part_start':
      if (!event.part) {
        return message;
      }
      return {
        ...message,
        parts: [...message.parts, stampToolStart(event.part, event.ts)],
      };
    case 'part_delta':
      if (!event.part_id || !event.patch) {
        return message;
      }
      return {
        ...message,
        parts: message.parts.map((part) =>
          part.id === event.part_id ? stampToolTiming(patchPart(part, event.patch!), event.ts) : part,
        ),
      };
    case 'part_end':
      return message;
    case 'error':
      return {
        ...message,
        status: 'error',
        error: event.message ?? '未知错误',
      };
    case 'turn_end':
      return {
        ...message,
        status: event.status === 'error' ? 'error' : 'done',
      };
    default:
      return message;
  }
}

function patchPart(part: MessagePart, patch: PartPatch): MessagePart {
  if (patch.op === 'text_append' && patch.text) {
    if (part.kind === 'text') {
      return { ...part, text: part.text + patch.text };
    }
    if (part.kind === 'artifact') {
      return { ...part, content: part.content + patch.text };
    }
  }

  if (patch.op === 'field_set' && patch.path) {
    return setField(part, patch.path, patch.value);
  }

  if (patch.op === 'payload_merge' && patch.payload && part.kind === 'data') {
    return {
      ...part,
      payload: { ...part.payload, ...patch.payload },
    };
  }

  return part;
}

function setField(part: MessagePart, path: string, value: unknown): MessagePart {
  if (path === 'text' && part.kind === 'text') {
    return { ...part, text: String(value ?? '') };
  }
  if (path === 'content' && part.kind === 'artifact') {
    return { ...part, content: String(value ?? '') };
  }
  if (path === 'status' && part.kind === 'workflow') {
    return { ...part, status: value as WorkflowStatus };
  }
  if (path === 'detail' && part.kind === 'workflow') {
    return { ...part, detail: String(value ?? '') };
  }
  if (path === 'phase' && part.kind === 'tool') {
    return { ...part, phase: value as 'call' | 'result' };
  }
  if (path === 'output' && part.kind === 'tool') {
    return { ...part, output: value };
  }
  if (path === 'started_at' && part.kind === 'tool') {
    return { ...part, started_at: String(value ?? '') };
  }
  if (path === 'ended_at' && part.kind === 'tool') {
    return { ...part, ended_at: String(value ?? '') };
  }
  if (path === 'duration_ms' && part.kind === 'tool') {
    const parsed = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(parsed) ? { ...part, duration_ms: Math.max(0, Math.round(parsed)) } : part;
  }
  return part;
}

function stampToolStart(part: MessagePart, eventTs?: string): MessagePart {
  if (part.kind !== 'tool' || part.started_at || !eventTs) {
    return part;
  }
  return { ...part, started_at: eventTs };
}

function stampToolTiming(part: MessagePart, eventTs?: string): MessagePart {
  if (part.kind !== 'tool') {
    return part;
  }
  let next = part;
  if (!next.started_at && eventTs) {
    next = { ...next, started_at: eventTs };
  }
  if (next.phase !== 'result' || next.duration_ms != null) {
    return next;
  }
  const endedAt = next.ended_at || eventTs;
  if (!next.started_at || !endedAt) {
    return next;
  }
  const started = Date.parse(next.started_at);
  const ended = Date.parse(endedAt);
  if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) {
    return next.ended_at ? next : { ...next, ended_at: endedAt };
  }
  return {
    ...next,
    ended_at: next.ended_at || endedAt,
    duration_ms: ended - started,
  };
}
