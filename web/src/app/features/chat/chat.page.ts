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
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { ChatApiService } from '../../core/api/chat-api.service';
import { ChatSessionStore } from '../../core/chat/chat-session.store';
import type { AgentDescriptor } from '../../core/models/chat.models';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';
import { findActiveExecutionTrace } from './chat-trace';
import { parseTraceSteps } from './chat-trace-parser';
import { ChatMessageComponent } from './components/chat-message.component';
import { ChatTraceSidebarComponent } from './components/chat-trace-sidebar.component';
import { sessionTransition } from '../../shared/animations/page-transitions';
import { createReducedMotionSignal } from '../../shared/utils/reduced-motion';

const SUGGESTIONS = [
  '有哪些和订单相关的宽表？',
  '解释一下 dws_order 和业务知识里的关联',
  '帮我写一段查询客户维表的 SQL',
];

/** 与 .chat-composer__input 的 min-height 保持一致 */
const COMPOSER_MIN_HEIGHT = '2rem';
const SCROLL_STICK_THRESHOLD_PX = 120;

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
  animations: [sessionTransition],
})
export class ChatPage {
  private readonly api = inject(ChatApiService);
  private readonly snack = inject(AppSnackService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly sessions = inject(ChatSessionStore);

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
  protected readonly draft = signal('');
  protected readonly agentMenuOpen = signal(false);

  protected readonly suggestions = SUGGESTIONS;
  protected readonly messages = this.sessions.messages;
  protected readonly sending = this.sessions.sending;
  protected readonly historyOpen = this.sessions.historyOpen;
  protected readonly historySessions = this.sessions.historySessions;
  protected readonly currentSessionId = this.sessions.currentId;
  protected readonly sendingSessionId = this.sessions.sendingSessionId;
  protected readonly reduceMotion = createReducedMotionSignal(this.destroyRef);

  protected readonly selectedAgent = computed(() =>
    this.agents().find((agent) => agent.id === this.selectedAgentId()),
  );

  protected agentDisplayName(agent: AgentDescriptor | null | undefined): string {
    if (!agent) {
      return '选择助手';
    }
    if (agent.name === 'TYWS') {
      return '自然语言问数助手';
    }
    return agent.name;
  }

  protected agentSubtitle(agent: AgentDescriptor): string {
    const desc = (agent.description || '').trim();
    const label = this.agentDisplayName(agent);
    if (!desc || desc === label) {
      return '';
    }
    if (desc.startsWith(`${label}；`)) {
      return desc.slice(label.length + 1).trim();
    }
    return desc;
  }

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

  constructor() {
    afterNextRender(() => {
      this.applyComposerHeight();
      this.setupPageVisibilitySync();
      this.setupThreadResizeObserver();
    });

    effect(() => {
      this.draft();
      this.scheduleComposerResize();
    });

    effect(() => {
      this.messages();
      this.currentSessionId();
      if (this.stickToBottom) {
        this.scheduleThreadScrollToBottom();
      }
    });

    this.api
      .listAgents()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          const list = response.agents ?? [];
          this.agents.set(list);
          if (list.length && !this.selectedAgentId()) {
            const currentAgent = this.sessions.currentSession()?.agentId;
            const preferred =
              list.find((a) => a.id === currentAgent) ??
              list.find((a) => a.id === 'copilot') ??
              list.find((a) => a.id === 'data_copilot') ??
              list[0];
            this.selectedAgentId.set(preferred.id);
            this.sessions.setCurrentAgent(preferred.id);
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
    this.sessions.setCurrentAgent(agentId);
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

  protected toggleHistory(): void {
    this.sessions.toggleHistoryOpen();
  }

  protected selectHistorySession(id: string): void {
    this.sessions.selectSession(id);
    const agentId = this.sessions.currentSession()?.agentId;
    if (agentId) {
      this.selectedAgentId.set(agentId);
    }
    this.traceSidebarOpen.set(false);
    this.stickToBottom = true;
    this.scheduleThreadScrollToBottom();
  }

  protected deleteHistorySession(event: Event, id: string): void {
    event.preventDefault();
    event.stopPropagation();
    this.sessions.deleteSession(id);
    const agentId = this.sessions.currentSession()?.agentId;
    if (agentId) {
      this.selectedAgentId.set(agentId);
    }
    this.traceSidebarOpen.set(false);
  }

  protected historyTime(value: string): string {
    const ts = Date.parse(value);
    if (!Number.isFinite(ts)) {
      return '';
    }
    const delta = Date.now() - ts;
    if (delta < 60_000) {
      return '刚刚';
    }
    if (delta < 3_600_000) {
      return `${Math.floor(delta / 60_000)} 分钟前`;
    }
    const date = new Date(ts);
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
  }

  protected newChat(): void {
    this.sessions.newChat(this.selectedAgentId());
    this.traceSidebarOpen.set(false);
    this.stickToBottom = true;
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
    if (!this.sessions.send(content, agentId)) {
      return;
    }
    this.draft.set('');
    this.stickToBottom = true;
    this.scheduleThreadScrollToBottom();
    this.scheduleComposerResize();
    this.focusComposer();
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
}
