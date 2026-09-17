import type { ChatMessage, MessagePart } from '../../core/models/chat.models';

export const EXECUTION_TRACE_TITLE = '执行追踪';

export function isExecutionTracePart(part: MessagePart): boolean {
  if (part.kind !== 'artifact') {
    return false;
  }
  const lang = part.language.trim().toLowerCase();
  return part.title === EXECUTION_TRACE_TITLE && (lang === 'json' || lang === 'md' || lang === 'markdown');
}

export interface ActiveExecutionTrace {
  content: string;
  streaming: boolean;
}

export function findActiveExecutionTrace(
  messages: readonly ChatMessage[],
): ActiveExecutionTrace | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== 'assistant') {
      continue;
    }
    const tracePart = message.parts.find(isExecutionTracePart);
    if (!tracePart || tracePart.kind !== 'artifact') {
      continue;
    }
    return {
      content: tracePart.content,
      streaming: message.status === 'streaming',
    };
  }
  return null;
}
