import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { TextPart } from '../../../core/models/chat.models';
import { reorderQueryMarkdown } from '../chat-message-parts';
import { ChatMarkdownComponent } from './chat-markdown.component';

@Component({
  selector: 'app-chat-text-part',
  imports: [ChatMarkdownComponent],
  template: `
    <div class="chat-part chat-part--text">
      <app-chat-markdown
        [content]="displayText()"
        [enhanceQueryCards]="!streaming()"
        [streaming]="streaming()"
      />
    </div>
  `,
  styleUrl: './chat-text-part.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatTextPartComponent {
  readonly part = input.required<TextPart>();
  readonly streaming = input(false);

  protected readonly displayText = computed(() => {
    const text = this.part().text;
    if (text) {
      return reorderQueryMarkdown(text);
    }
    return this.streaming() ? '▍' : '';
  });
}
