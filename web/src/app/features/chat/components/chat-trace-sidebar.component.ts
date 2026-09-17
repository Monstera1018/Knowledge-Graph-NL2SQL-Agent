import { Component, HostListener, computed, input, output } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import { parseTraceSteps } from '../chat-trace-parser';
import { ChatTraceStepsComponent } from './chat-trace-steps.component';

@Component({
  selector: 'app-chat-trace-sidebar',
  imports: [MatIconModule, ChatTraceStepsComponent],
  templateUrl: './chat-trace-sidebar.component.html',
  styleUrl: './chat-trace-sidebar.component.css',
})
export class ChatTraceSidebarComponent {
  readonly open = input(false);
  readonly content = input('');
  readonly streaming = input(false);

  readonly openChange = output<boolean>();

  protected readonly stepCount = computed(() => parseTraceSteps(this.content()).length);

  @HostListener('document:keydown.escape')
  protected onEscape(): void {
    if (this.open()) {
      this.close();
    }
  }

  protected close(): void {
    this.openChange.emit(false);
  }
}
