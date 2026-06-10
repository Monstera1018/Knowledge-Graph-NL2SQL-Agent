import {
  Component,
  effect,
  input,
  linkedSignal,
  signal,
} from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import {
  defaultExpandedStepIds,
  parseTraceSteps,
  sectionMarkdown,
  type TraceStep,
} from '../chat-trace-parser';
import { ChatMarkdownComponent } from './chat-markdown.component';

@Component({
  selector: 'app-chat-trace-steps',
  imports: [MatIconModule, ChatMarkdownComponent],
  templateUrl: './chat-trace-steps.component.html',
  styleUrl: './chat-trace-steps.component.css',
})
export class ChatTraceStepsComponent {
  readonly content = input('');
  readonly streaming = input(false);

  protected readonly steps = linkedSignal(() => parseTraceSteps(this.content()));
  protected readonly sectionMarkdown = sectionMarkdown;

  private readonly expandedIds = signal<Set<string>>(new Set());
  private lastStepCount = 0;

  constructor() {
    effect(() => {
      const steps = this.steps();
      const streaming = this.streaming();
      const defaults = defaultExpandedStepIds(steps, streaming);

      if (steps.length !== this.lastStepCount) {
        this.lastStepCount = steps.length;
        this.expandedIds.set(defaults);
        return;
      }

      if (streaming && steps.length) {
        this.expandedIds.update((current) => {
          const next = new Set<string>();
          next.add(steps[steps.length - 1].id);
          return next;
        });
      }
    });
  }

  protected isExpanded(step: TraceStep): boolean {
    return this.expandedIds().has(step.id);
  }

  protected toggleStep(step: TraceStep): void {
    this.expandedIds.update((current) => {
      const next = new Set(current);
      if (next.has(step.id)) {
        next.delete(step.id);
      } else {
        next.add(step.id);
      }
      return next;
    });
  }

  protected stepStatus(step: TraceStep, index: number): 'done' | 'active' | 'error' {
    const steps = this.steps();
    if (step.status === 'error') {
      return 'error';
    }
    if (this.streaming() && index === steps.length - 1) {
      return 'active';
    }
    return step.status;
  }
}
