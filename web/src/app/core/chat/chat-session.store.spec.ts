import { describe, expect, it } from 'vitest';

import type { ChatMessage } from '../models/chat.models';
import {
  chatStorageKey,
  parsePersistedChatState,
  titleFromMessages,
} from './chat-session.store';

describe('chat-session persistence helpers', () => {
  it('builds a workspace-scoped storage key', () => {
    expect(chatStorageKey('local-admin', 'default')).toBe(
      'tyws.chat.sessions.v1.local-admin.default',
    );
  });

  it('uses the first user question as the session title', () => {
    const messages: ChatMessage[] = [
      {
        id: 'u1',
        role: 'user',
        content: '金华地区电费未结清用户清单',
        createdAt: '2026-09-11T00:00:00.000Z',
      },
    ];
    expect(titleFromMessages(messages)).toBe('金华地区电费未结清用户清单');
  });

  it('restores persisted sessions and ignores invalid payloads', () => {
    expect(parsePersistedChatState('not-json')).toBeNull();
    const restored = parsePersistedChatState(
      JSON.stringify({
        version: 1,
        currentId: 's1',
        historyOpen: true,
        sessions: [
          {
            id: 's1',
            title: '金华未结清',
            agentId: 'copilot',
            createdAt: '2026-09-11T00:00:00.000Z',
            updatedAt: '2026-09-11T00:01:00.000Z',
            messages: [
              {
                id: 'u1',
                role: 'user',
                content: '金华未结清',
                createdAt: '2026-09-11T00:00:00.000Z',
              },
            ],
          },
        ],
      }),
    );
    expect(restored?.currentId).toBe('s1');
    expect(restored?.sessions).toHaveLength(1);
    expect(restored?.sessions[0]?.messages).toHaveLength(1);
  });
});
