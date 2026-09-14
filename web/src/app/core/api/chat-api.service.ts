import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { AuthService } from '../auth/auth.service';
import type { AgentListResponse, SendMessageRequest, StreamEvent } from '../models/chat.models';
import { WorkspaceService } from '../workspace/workspace.service';

@Injectable({ providedIn: 'root' })
export class ChatApiService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly workspace = inject(WorkspaceService);

  listAgents() {
    return this.http.get<AgentListResponse>('/api/chat/agents');
  }

  /** 使用 POST + SSE；每次发射一个已解析的协议事件。 */
  streamMessage(sessionId: string, body: SendMessageRequest): Observable<StreamEvent> {
    return new Observable((subscriber) => {
      const controller = new AbortController();

      void (async () => {
        try {
          const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            'X-Workspace-Id': this.workspace.currentWorkspaceId(),
            ...this.auth.authHeaders(),
          };

          const response = await fetch(
            `/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
            {
              method: 'POST',
              headers,
              body: JSON.stringify(body),
              signal: controller.signal,
            },
          );

          if (!response.ok) {
            let detail = response.statusText;
            try {
              const errBody = (await response.json()) as { detail?: string };
              if (errBody.detail) {
                detail = errBody.detail;
              }
            } catch {
              /* 忽略错误响应解析失败 */
            }
            subscriber.error(new Error(detail));
            return;
          }

          const reader = response.body?.getReader();
          if (!reader) {
            subscriber.error(new Error('响应体不可读'));
            return;
          }

          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              break;
            }
            buffer += decoder.decode(value, { stream: true });
            const { events, rest } = parseSseBuffer(buffer);
            buffer = rest;
            for (const event of events) {
              if (subscriber.closed) {
                return;
              }
              subscriber.next(event);
            }
          }

          if (buffer.trim()) {
            const { events } = parseSseBuffer(buffer + '\n\n');
            for (const event of events) {
              if (subscriber.closed) {
                return;
              }
              subscriber.next(event);
            }
          }

          if (!subscriber.closed) {
            subscriber.complete();
          }
        } catch (err) {
          if ((err as Error).name === 'AbortError') {
            subscriber.complete();
            return;
          }
          subscriber.error(err);
        }
      })();

      return () => controller.abort();
    });
  }

  downloadReport(fileId: string, filename = '分析报告.docx') {
    const params = new URLSearchParams({ filename });
    return this.http.get(`/api/chat/reports/${encodeURIComponent(fileId)}?${params.toString()}`, {
      responseType: 'blob',
    });
  }
}

function parseSseBuffer(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = [];
  const blocks = buffer.split('\n\n');
  const rest = blocks.pop() ?? '';

  for (const block of blocks) {
    for (const line of block.split('\n')) {
      if (!line.startsWith('data: ')) {
        continue;
      }
      const payload = line.slice(6).trim();
      if (!payload) {
        continue;
      }
      events.push(JSON.parse(payload) as StreamEvent);
    }
  }

  return { events, rest };
}
