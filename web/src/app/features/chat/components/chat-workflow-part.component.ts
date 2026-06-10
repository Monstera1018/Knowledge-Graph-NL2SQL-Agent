import { Component, computed, input, signal } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { WorkflowPart } from '../../../core/models/chat.models';
import {
  countWorkflowItems,
  parseWorkflowSteps,
  resolveActiveWorkflowStep,
} from '../workflow-steps';

@Component({
  selector: 'app-chat-workflow-part',
  imports: [MatIconModule],
  templateUrl: './chat-workflow-part.component.html',
  styleUrl: './chat-workflow-part.component.css',
})
export class ChatWorkflowPartComponent {
  readonly part = input.required<WorkflowPart>();

  protected readonly hover = signal(false);
  protected readonly pinned = signal(false);

  protected readonly steps = computed(() =>
    parseWorkflowSteps(this.part().detail, this.part().status),
  );

  protected readonly activeStep = computed(() =>
    resolveActiveWorkflowStep(this.steps(), this.part().status),
  );

  protected readonly expandable = computed(() => countWorkflowItems(this.steps()) > 1);

  protected readonly panelOpen = computed(
    () => this.expandable() && (this.hover() || this.pinned()),
  );

  protected onHover(value: boolean): void {
    this.hover.set(value);
    if (!value) {
      this.pinned.set(false);
    }
  }

  protected onCompactClick(): void {
    if (!this.expandable()) {
      return;
    }
    this.pinned.update((open) => !open);
  }
}
