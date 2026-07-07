import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterNextRender,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subscription, finalize } from 'rxjs';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { ChatApiService } from '../../core/api/chat-api.service';
import type {
  AgentDescriptor,
  ChatAssistantMessage,
  ChatMessage,
  ChatUserMessage,
} from '../../core/models/chat.models';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';
import { applyStreamEvent } from './chat-stream';
import { findActiveExecutionTrace } from './chat-trace';
import { parseTraceSteps } from './chat-trace-parser';
import { ChatMessageComponent } from './components/chat-message.component';
import { ChatTraceSidebarComponent } from './components/chat-trace-sidebar.component';

const SESSION_STORAGE_KEY = 'zh_yb_chat_session_id';

const SUGGESTIONS = [
  '有哪些和订单相关的宽表？',
  '解释一下 dws_order 和业务知识里的关联',
  '帮我写一段查询客户维表的 SQL',
];

/** 与 .chat-composer__input 的 min-height 保持一致 */
const COMPOSER_MIN_HEIGHT = '2rem';
const SCROLL_STICK_THRESHOLD_PX = 120;
/** SSE 空闲超时：每次收到事件都会刷新，避免长 SQL 或重试被过早中断 */
const STREAM_IDLE_TIMEOUT_MS = 300_000;

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

function createId(prefix = 'chat'): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  );
}

@Component({
  selector: 'app-chat-page',
  imports: [
    FormsModule,
    MatIconModule,
    MatProgressSpinnerModule,
    ChatMessageComponent,
    ChatTraceSidebarComponent,
  ],
  templateUrl: './chat.page.html',
  styleUrl: './chat.page.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatPage {
  private readonly api = inject(ChatApiService);
  private readonly snack = inject(AppSnackService);
  private readonly destroyRef = inject(DestroyRef);

  private readonly threadEl = viewChild<ElementRef<HTMLElement>>('thread');
  private readonly threadInnerEl = viewChild<ElementRef<HTMLElement>>('threadInner');
  private readonly threadBottomEl = viewChild<ElementRef<HTMLElement>>('threadBottom');
  private readonly chatInputEl = viewChild<ElementRef<HTMLTextAreaElement>>('chatInput');
  private stickToBottom = true;
  private scrollScheduled = false;
  private pageHidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
  private syncBottomOnVisible = false;
  private threadResizeObserver: ResizeObserver | null = null;

  protected readonly agents = signal<AgentDescriptor[]>([]);
  protected readonly agentsLoading = signal(true);
  protected readonly selectedAgentId = signal('');
  protected readonly messages = signal<ChatMessage[]>([]);
  protected readonly draft = signal('');
  protected readonly sending = signal(false);
  protected readonly agentMenuOpen = signal(false);

  protected readonly suggestions = SUGGESTIONS;

  protected readonly selectedAgent = computed(() =>
    this.agents().find((agent) => agent.id === this.selectedAgentId()),
  );

  protected readonly hasMessages = computed(() => this.messages().length > 0);
  protected readonly showScrollDown = signal(false);
  protected readonly traceSidebarOpen = signal(false);

  protected readonly activeTrace = computed(() =>
    findActiveExecutionTrace(this.messages()),
  );

  protected readonly hasTrace = computed(() => this.activeTrace() !== null);

  protected readonly traceStepCount = computed(() =>
    parseTraceSteps(this.activeTrace()?.content ?? '').length,
  );

  private sessionId = '';
  private activeStreamSub: Subscription | undefined;

  constructor() {
    this.sessionId = this.loadOrCreateSessionId();

    afterNextRender(() => {
      this.applyComposerHeight();
      this.setupPageVisibilitySync();
      this.setupThreadResizeObserver();
    });

    effect(() => {
      this.draft();
      this.scheduleComposerResize();
    });

    this.api
      .listAgents()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          const list = response.agents ?? [];
          this.agents.set(list);
          if (list.length && !this.selectedAgentId()) {
            const preferred =
              list.find((a) => a.id === 'data_copilot') ??
              list.find((a) => a.id === 'metadata_demo') ??
              list[0];
            this.selectedAgentId.set(preferred.id);
          }
          this.agentsLoading.set(false);
        },
        error: (err) => {
          this.agentsLoading.set(false);
          this.snack.error(formatApiError(err));
        },
      });
  }

  protected onThreadScroll(): void {
    const el = this.threadEl()?.nativeElement;
    if (!el) {
      return;
    }
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    this.stickToBottom = distance <= SCROLL_STICK_THRESHOLD_PX;
    this.showScrollDown.set(distance > SCROLL_STICK_THRESHOLD_PX);
  }

  protected scrollToBottom(): void {
    this.stickToBottom = true;
    const el = this.threadEl()?.nativeElement;
    if (el) {
      this.scrollThreadToBottom();
    }
    this.showScrollDown.set(false);
  }

  protected toggleAgentMenu(): void {
    this.agentMenuOpen.update((open) => !open);
  }

  protected selectAgent(agentId: string): void {
    this.selectedAgentId.set(agentId);
    this.agentMenuOpen.set(false);
  }

  protected useSuggestion(text: string): void {
    this.draft.set(text);
  }

  protected onDraftChange(value: string): void {
    this.draft.set(value);
  }

  /** 与 ngModel 并行：保证删字时 DOM 已更新再测高 */
  protected onComposerInput(): void {
    this.scheduleComposerResize();
  }

  protected toggleTraceSidebar(): void {
    this.traceSidebarOpen.update((open) => !open);
  }

  protected setTraceSidebarOpen(open: boolean): void {
    this.traceSidebarOpen.set(open);
  }

  protected newChat(): void {
    if (this.sending()) {
      return;
    }
    this.messages.set([]);
    this.traceSidebarOpen.set(false);
    this.sessionId = createId('session');
    localStorage.setItem(SESSION_STORAGE_KEY, this.sessionId);
  }

  protected onComposerKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!this.sending()) {
        void this.send();
      }
    }
  }

  protected async send(): Promise<void> {
    const content = this.draft().trim();
    const agentId = this.selectedAgentId();
    if (!content || !agentId || this.sending()) {
      return;
    }
    this.sending.set(true);

    const userMessage: ChatUserMessage = {
      id: createId('user'),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    };

    const assistantId = createId('assistant');
    const assistantMessage: ChatAssistantMessage = {
      id: assistantId,
      role: 'assistant',
      parts: [],
      status: 'streaming',
      createdAt: new Date().toISOString(),
    };

    this.messages.update((items) => [...items, userMessage, assistantMessage]);
    this.draft.set('');
    this.stickToBottom = true;
    this.scheduleThreadScrollToBottom();

    let turnEnded = false;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdleTimer = () => {
      if (idleTimer !== undefined) {
        clearTimeout(idleTimer);
      }
      idleTimer = setTimeout(() => {
        if (!turnEnded) {
          this.activeStreamSub?.unsubscribe();
        }
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdleTimer();

    this.activeStreamSub?.unsubscribe();
    this.activeStreamSub = this.api
      .streamMessage(this.sessionId, { agent_id: agentId, content })
      .pipe(
        finalize(() => {
          if (idleTimer !== undefined) {
            clearTimeout(idleTimer);
          }
          this.sending.set(false);
          this.messages.update((items) => finalizeAssistantTurn(items, assistantId, turnEnded));
          this.scheduleComposerResize();
          this.focusComposer();
          if (this.stickToBottom) {
            this.scheduleThreadScrollToBottom();
          }
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (event) => {
          resetIdleTimer();
          this.messages.update((items) =>
            items.map((msg) => {
              if (msg.id !== assistantId || msg.role !== 'assistant') {
                return msg;
              }
              return applyStreamEvent(msg, event);
            }),
          );
          if (this.stickToBottom) {
            this.scheduleThreadScrollToBottom();
          }
          if (this.pageHidden && this.stickToBottom) {
            this.syncBottomOnVisible = true;
          }
          if (event.type === 'turn_end') {
            turnEnded = true;
          }
        },
        error: (err) => {
          turnEnded = true;
          this.messages.update((items) =>
            items.map((msg) => {
              if (msg.id !== assistantId || msg.role !== 'assistant') {
                return msg;
              }
              return {
                ...msg,
                status: 'error' as const,
                error: err instanceof Error ? err.message : formatApiError(err),
              };
            }),
          );
        },
      });
  }

  private focusComposer(): void {
    requestAnimationFrame(() => {
      this.chatInputEl()?.nativeElement?.focus();
    });
  }

  private scheduleThreadScrollToBottom(): void {
    if (!this.stickToBottom || this.scrollScheduled) {
      return;
    }
    this.scrollScheduled = true;
    requestAnimationFrame(() => {
      this.scrollScheduled = false;
      if (!this.stickToBottom) {
        return;
      }
      this.scrollThreadToBottom();
      this.onThreadScroll();
    });
  }

  private scheduleThreadScrollToBottomAfterLayout(): void {
    if (this.scrollScheduled) {
      return;
    }
    this.scrollScheduled = true;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        this.scrollScheduled = false;
        if (!this.stickToBottom) {
          return;
        }
        this.scrollThreadToBottom();
        this.onThreadScroll();
      });
    });
  }

  private scrollThreadToBottom(): void {
    const el = this.threadEl()?.nativeElement;
    if (!el) {
      return;
    }
    const bottom = this.threadBottomEl()?.nativeElement;
    if (bottom) {
      bottom.scrollIntoView({ block: 'end', behavior: 'auto' });
      return;
    }
    el.scrollTo({ top: el.scrollHeight, behavior: 'auto' });
  }

  /** 页面从后台恢复后重新校准滚动位置 */
  private setupPageVisibilitySync(): void {
    const handleVisibleAgain = () => {
      this.pageHidden = false;
      this.recalibrateThreadAfterVisibilityReturn();
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        this.pageHidden = true;
        this.syncBottomOnVisible = this.stickToBottom || this.sending();
        return;
      }
      handleVisibleAgain();
    };

    const onFocus = () => {
      if (!this.pageHidden) {
        this.recalibrateThreadAfterVisibilityReturn();
      }
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('focus', onFocus);
    this.destroyRef.onDestroy(() => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('focus', onFocus);
    });
  }

  private setupThreadResizeObserver(): void {
    const inner = this.threadInnerEl()?.nativeElement;
    if (!inner || typeof ResizeObserver === 'undefined') {
      return;
    }

    this.threadResizeObserver = new ResizeObserver(() => {
      if (this.pageHidden) {
        if (this.stickToBottom || this.sending()) {
          this.syncBottomOnVisible = true;
        }
        return;
      }

      if (this.stickToBottom) {
        this.scheduleThreadScrollToBottomAfterLayout();
        return;
      }
      requestAnimationFrame(() => this.onThreadScroll());
    });
    this.threadResizeObserver.observe(inner);
    this.destroyRef.onDestroy(() => this.threadResizeObserver?.disconnect());
  }

  private recalibrateThreadAfterVisibilityReturn(): void {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (this.syncBottomOnVisible || this.stickToBottom) {
          this.scrollToBottom();
        } else {
          this.onThreadScroll();
        }
        this.syncBottomOnVisible = false;
      });
    });
  }

  /** 等 DOM 与 ngModel 同步后再测量，发送清空输入框也要走这里 */
  private scheduleComposerResize(): void {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => this.applyComposerHeight());
    });
  }

  private applyComposerHeight(): void {
    const el = this.chatInputEl()?.nativeElement;
    if (!el) {
      return;
    }

    const text = this.draft();
    const maxPx = parseFloat(getComputedStyle(el).maxHeight) || 200;

    if (!text.trim()) {
      el.style.height = COMPOSER_MIN_HEIGHT;
      el.style.overflowY = 'hidden';
      return;
    }

    el.style.height = COMPOSER_MIN_HEIGHT;
    el.style.overflowY = 'hidden';

    const measured = el.scrollHeight;
    if (measured <= maxPx) {
      el.style.height = `${measured}px`;
      el.style.overflowY = 'hidden';
    } else {
      el.style.height = `${maxPx}px`;
      el.style.overflowY = 'auto';
    }
  }

  private loadOrCreateSessionId(): string {
    let id = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
      id = createId('session');
      localStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    return id;
  }
}
