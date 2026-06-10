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
        parts: [...message.parts, event.part],
      };
    case 'part_delta':
      if (!event.part_id || !event.patch) {
        return message;
      }
      return {
        ...message,
        parts: message.parts.map((part) =>
          part.id === event.part_id ? patchPart(part, event.patch!) : part,
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
  return part;
}
