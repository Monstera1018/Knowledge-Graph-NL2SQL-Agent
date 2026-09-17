import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { Subscription } from 'rxjs';

import { ChatApiService } from '../api/chat-api.service';
import { AuthService } from '../auth/auth.service';
import type {
  ChatAssistantMessage,
  ChatMessage,
  ChatSession,
  ChatUserMessage,
  StreamEvent,
} from '../models/chat.models';
import { WorkspaceService } from '../workspace/workspace.service';
import { applyStreamEvent } from '../../features/chat/chat-stream';

const STORE_VERSION = 1;
const MAX_SESSIONS = 40;
const STREAM_IDLE_TIMEOUT_MS = 300_000;
const PERSIST_DEBOUNCE_MS = 400;

export interface PersistedChatState {
  version: number;
  currentId: string;
  historyOpen: boolean;
  sessions: ChatSession[];
}

export function chatStorageKey(userId: string, workspaceId: string): string {
  return `tyws.chat.sessions.v${STORE_VERSION}.${userId || 'anon'}.${workspaceId || 'default'}`;
}

export function createChatId(prefix = 'chat'): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  );
}

export function titleFromMessages(messages: ChatMessage[]): string {
  const firstUser = messages.find((msg): msg is ChatUserMessage => msg.role === 'user');
  const text = firstUser?.content.trim() ?? '';
  if (!text) {
    return '新对话';
  }
  return text.length > 28 ? `${text.slice(0, 28)}…` : text;
}

export function parsePersistedChatState(raw: string | null): PersistedChatState | null {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedChatState>;
    if (!parsed || parsed.version !== STORE_VERSION || !Array.isArray(parsed.sessions)) {
      return null;
    }
    const sessions = parsed.sessions
      .filter((item): item is ChatSession => !!item && typeof item.id === 'string')
      .map((item) => ({
        id: item.id,
        title: typeof item.title === 'string' && item.title.trim() ? item.title : '新对话',
        agentId: typeof item.agentId === 'string' ? item.agentId : '',
        createdAt: item.createdAt || new Date().toISOString(),
        updatedAt: item.updatedAt || item.createdAt || new Date().toISOString(),
        messages: Array.isArray(item.messages) ? item.messages : [],
      }));
    return {
      version: STORE_VERSION,
      currentId: typeof parsed.currentId === 'string' ? parsed.currentId : '',
      historyOpen: parsed.historyOpen !== false,
      sessions,
    };
  } catch {
    return null;
  }
}

function emptySession(agentId = ''): ChatSession {
  const now = new Date().toISOString();
  return {
    id: createChatId('session'),
    title: '新对话',
    agentId,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

function finalizeAssistantTurn(
  messages: ChatMessage[],
  assistantId: string,
  turnEnded: boolean,
): ChatMessage[] {
  if (turnEnded) {
    return messages;
  }
  return messages.map((msg) => {
    if (msg.id !== assistantId || msg.role !== 'assistant' || msg.status !== 'streaming') {
      return msg;
    }
    return { ...msg, status: 'done' as const };
  });
}

@Injectable({ providedIn: 'root' })
export class ChatSessionStore {
  private readonly api = inject(ChatApiService);
  private readonly auth = inject(AuthService);
  private readonly workspace = inject(WorkspaceService);

  private readonly sessionsSignal = signal<ChatSession[]>([]);
  private readonly currentIdSignal = signal('');
  private readonly historyOpenSignal = signal(true);
  private readonly sendingSignal = signal(false);
  private readonly sendingSessionIdSignal = signal('');

  private streamSub: Subscription | undefined;
  private persistTimer: ReturnType<typeof setTimeout> | undefined;
  private loadedKey = '';
  private pendingFreshChat = false;

  readonly sessions = this.sessionsSignal.asReadonly();
  readonly currentId = this.currentIdSignal.asReadonly();
  readonly historyOpen = this.historyOpenSignal.asReadonly();
  readonly sending = this.sendingSignal.asReadonly();
  readonly sendingSessionId = this.sendingSessionIdSignal.asReadonly();

  readonly currentSession = computed(() => {
    const id = this.currentIdSignal();
    return this.sessionsSignal().find((item) => item.id === id) ?? null;
  });

  readonly messages = computed(() => this.currentSession()?.messages ?? []);

  readonly historySessions = computed(() =>
    this.sessionsSignal()
      .filter((item) => item.messages.length > 0)
      .slice()
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
  );

  constructor() {
    effect(() => {
      const userId = this.auth.user()?.id ?? '';
      const workspaceId = this.workspace.workspaceId();
      this.loadBucket(userId, workspaceId);
    });
  }

  /** 登录后进入对话页时切到空白新对话，历史会话仍保留。 */
  prepareFreshChat(): void {
    this.pendingFreshChat = true;
    const userId = this.auth.user()?.id ?? '';
    const workspaceId = this.workspace.workspaceId();
    const key = chatStorageKey(userId, workspaceId);
    if (this.loadedKey === key) {
      this.pendingFreshChat = false;
      this.newChat();
    }
  }

  toggleHistoryOpen(): void {
    this.historyOpenSignal.update((open) => !open);
    this.persistSoon();
  }

  setHistoryOpen(open: boolean): void {
    this.historyOpenSignal.set(open);
    this.persistSoon();
  }

  newChat(agentId = ''): void {
    if (this.sendingSignal()) {
      return;
    }
    const current = this.currentSession();
    if (current && current.messages.length === 0) {
      if (agentId && current.agentId !== agentId) {
        this.patchSession(current.id, (session) => ({ ...session, agentId }));
      }
      return;
    }
    const session = emptySession(agentId || current?.agentId || '');
    this.sessionsSignal.update((items) => [session, ...items]);
    this.currentIdSignal.set(session.id);
    this.persistSoon();
  }

  selectSession(id: string): void {
    if (!id || id === this.currentIdSignal()) {
      return;
    }
    const exists = this.sessionsSignal().some((item) => item.id === id);
    if (!exists) {
      return;
    }
    this.currentIdSignal.set(id);
    this.persistSoon();
  }

  deleteSession(id: string): void {
    if (this.sendingSignal() && this.sendingSessionIdSignal() === id) {
      return;
    }
    const remaining = this.sessionsSignal().filter((item) => item.id !== id);
    this.sessionsSignal.set(remaining);
    if (this.currentIdSignal() === id) {
      const next = remaining.find((item) => item.messages.length > 0) ?? remaining[0];
      if (next) {
        this.currentIdSignal.set(next.id);
      } else {
        const created = emptySession();
        this.sessionsSignal.set([created]);
        this.currentIdSignal.set(created.id);
      }
    }
    this.persistNow();
  }

  setCurrentAgent(agentId: string): void {
    const current = this.currentSession();
    if (!current || !agentId || current.agentId === agentId) {
      return;
    }
    this.patchSession(current.id, (session) => ({ ...session, agentId }));
  }

  send(content: string, agentId: string): boolean {
    const text = content.trim();
    if (!text || !agentId || this.sendingSignal()) {
      return false;
    }

    let session = this.currentSession();
    if (!session) {
      this.newChat(agentId);
      session = this.currentSession();
    }
    if (!session) {
      return false;
    }

    const sessionId = session.id;
    const userMessage: ChatUserMessage = {
      id: createChatId('user'),
      role: 'user',
      content: text,
      createdAt: new Date().toISOString(),
    };
    const assistantId = createChatId('assistant');
    const assistantMessage: ChatAssistantMessage = {
      id: assistantId,
      role: 'assistant',
      parts: [],
      status: 'streaming',
      createdAt: new Date().toISOString(),
    };

    this.patchSession(sessionId, (item) => {
      const messages = [...item.messages, userMessage, assistantMessage];
      return {
        ...item,
        agentId,
        title: item.messages.length ? item.title : titleFromMessages(messages),
        updatedAt: new Date().toISOString(),
        messages,
      };
    });

    this.sendingSignal.set(true);
    this.sendingSessionIdSignal.set(sessionId);
    this.persistNow();

    let turnEnded = false;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const clearIdle = () => {
      if (idleTimer !== undefined) {
        clearTimeout(idleTimer);
        idleTimer = undefined;
      }
    };
    const resetIdle = () => {
      clearIdle();
      idleTimer = setTimeout(() => this.streamSub?.unsubscribe(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();

    this.streamSub?.unsubscribe();
    this.streamSub = this.api.streamMessage(sessionId, { agent_id: agentId, content: text }).subscribe({
      next: (event: StreamEvent) => {
        resetIdle();
        this.patchSession(sessionId, (item) => ({
          ...item,
          updatedAt: new Date().toISOString(),
          messages: item.messages.map((msg) => {
            if (msg.id !== assistantId || msg.role !== 'assistant') {
              return msg;
            }
            return applyStreamEvent(msg, event);
          }),
        }));
        if (event.type === 'turn_end') {
          turnEnded = true;
        }
        this.persistSoon();
      },
      error: (err: unknown) => {
        turnEnded = true;
        this.patchSession(sessionId, (item) => ({
          ...item,
          updatedAt: new Date().toISOString(),
          messages: item.messages.map((msg) => {
            if (msg.id !== assistantId || msg.role !== 'assistant') {
              return msg;
            }
            return {
              ...msg,
              status: 'error' as const,
              error: err instanceof Error ? err.message : '发送失败',
            };
          }),
        }));
        this.finishStream(sessionId, assistantId, turnEnded);
        clearIdle();
      },
      complete: () => {
        this.finishStream(sessionId, assistantId, turnEnded);
        clearIdle();
      },
    });
    return true;
  }

  private finishStream(sessionId: string, assistantId: string, turnEnded: boolean): void {
    this.patchSession(sessionId, (item) => ({
      ...item,
      updatedAt: new Date().toISOString(),
      messages: finalizeAssistantTurn(item.messages, assistantId, turnEnded),
    }));
    this.sendingSignal.set(false);
    this.sendingSessionIdSignal.set('');
    this.streamSub = undefined;
    this.persistNow();
  }

  private patchSession(id: string, updater: (session: ChatSession) => ChatSession): void {
    this.sessionsSignal.update((items) =>
      items.map((item) => (item.id === id ? updater(item) : item)),
    );
  }

  private loadBucket(userId: string, workspaceId: string): void {
    const key = chatStorageKey(userId, workspaceId);
    if (key === this.loadedKey) {
      return;
    }
    this.loadedKey = key;
    const saved = parsePersistedChatState(localStorage.getItem(key));
    const restored = saved?.sessions ?? [];
    const currentId = saved?.currentId ?? '';
    const current = restored.find((item) => item.id === currentId);
    if (current) {
      this.sessionsSignal.set(restored);
      this.currentIdSignal.set(current.id);
    } else if (restored.length) {
      const created = emptySession();
      this.sessionsSignal.set([created, ...restored]);
      this.currentIdSignal.set(created.id);
    } else {
      const created = emptySession();
      this.sessionsSignal.set([created]);
      this.currentIdSignal.set(created.id);
    }
    this.historyOpenSignal.set(saved?.historyOpen !== false);
    if (this.pendingFreshChat) {
      this.pendingFreshChat = false;
      this.newChat();
    }
  }

  private persistSoon(): void {
    if (this.persistTimer !== undefined) {
      clearTimeout(this.persistTimer);
    }
    this.persistTimer = setTimeout(() => {
      this.persistTimer = undefined;
      this.persistNow();
    }, PERSIST_DEBOUNCE_MS);
  }

  private persistNow(): void {
    if (this.persistTimer !== undefined) {
      clearTimeout(this.persistTimer);
      this.persistTimer = undefined;
    }
    const key = this.loadedKey;
    if (!key) {
      return;
    }
    const sessions = this.trimSessions(this.sessionsSignal());
    const payload: PersistedChatState = {
      version: STORE_VERSION,
      currentId: this.currentIdSignal(),
      historyOpen: this.historyOpenSignal(),
      sessions,
    };
    this.writePayload(key, payload);
  }

  private trimSessions(sessions: ChatSession[]): ChatSession[] {
    const currentId = this.currentIdSignal();
    const withMessages = sessions.filter((item) => item.messages.length > 0);
    const emptyCurrent = sessions.find((item) => item.id === currentId && item.messages.length === 0);
    const sorted = withMessages.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    const kept = sorted.slice(0, MAX_SESSIONS);
    return emptyCurrent ? [emptyCurrent, ...kept] : kept;
  }

  private writePayload(key: string, payload: PersistedChatState): void {
    const variants: PersistedChatState[] = [payload];
    if (payload.sessions.length > 8) {
      variants.push({
        ...payload,
        sessions: payload.sessions.slice(0, Math.max(8, Math.floor(payload.sessions.length / 2))),
      });
    }
    for (const candidate of variants) {
      try {
        localStorage.setItem(key, JSON.stringify(candidate));
        if (candidate !== payload) {
          this.sessionsSignal.set(candidate.sessions);
        }
        return;
      } catch {
        /* 配额不足时缩小后再写 */
      }
    }
  }
}
