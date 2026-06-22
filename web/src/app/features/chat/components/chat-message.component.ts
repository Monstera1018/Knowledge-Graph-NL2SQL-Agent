import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { ChatMessage, ArtifactPart, DataPart, TextPart } from '../../../core/models/chat.models';
import { isExecutionTracePart } from '../chat-trace';
import {
  shouldShowArtifactPart,
  shouldShowDataPart,
  shouldShowTextPart,
  mergeDisplayToolParts,
  sortVisibleParts,
} from '../chat-message-parts';
import { ChatPartComponent } from './chat-part.component';

@Component({
  selector: 'app-chat-message',
  imports: [MatIconModule, ChatPartComponent],
  templateUrl: './chat-message.component.html',
  styleUrl: './chat-message.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatMessageComponent {
  readonly message = input.required<ChatMessage>();

  protected readonly userMessage = computed(() => {
    const message = this.message();
    return message.role === 'user' ? message : null;
  });

  protected readonly assistantMessage = computed(() => {
    const message = this.message();
    return message.role === 'assistant' ? message : null;
  });

  protected readonly visibleParts = computed(() => {
    const assistant = this.assistantMessage();
    if (!assistant) {
      return [];
    }
    const streaming = assistant.status === 'streaming';
    const parts = assistant.parts.filter((part) => !isExecutionTracePart(part));
    const visible = parts.filter((part) => {
      if (part.kind === 'data') {
        return shouldShowDataPart(part as DataPart, streaming);
      }
      if (part.kind === 'text') {
        return shouldShowTextPart(part as TextPart, streaming);
      }
      if (part.kind === 'artifact') {
        return shouldShowArtifactPart(part as ArtifactPart);
      }
      return true;
    });
    return sortVisibleParts(mergeDisplayToolParts(visible));
  });
}
