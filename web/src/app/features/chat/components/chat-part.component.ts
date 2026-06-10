import { Component, computed, input } from '@angular/core';

import type { MessagePart } from '../../../core/models/chat.models';
import { ChatArtifactPartComponent } from './chat-artifact-part.component';
import { ChatDataPartComponent } from './chat-data-part.component';
import { ChatErrorPartComponent } from './chat-error-part.component';
import { ChatTextPartComponent } from './chat-text-part.component';
import { ChatToolPartComponent } from './chat-tool-part.component';
import { ChatWorkflowPartComponent } from './chat-workflow-part.component';

@Component({
  selector: 'app-chat-part',
  imports: [
    ChatTextPartComponent,
    ChatWorkflowPartComponent,
    ChatToolPartComponent,
    ChatDataPartComponent,
    ChatArtifactPartComponent,
    ChatErrorPartComponent,
  ],
  templateUrl: './chat-part.component.html',
})
export class ChatPartComponent {
  readonly part = input.required<MessagePart>();
  readonly streaming = input(false);

  protected readonly textPart = computed(() => {
    const part = this.part();
    return part.kind === 'text' ? part : null;
  });

  protected readonly workflowPart = computed(() => {
    const part = this.part();
    return part.kind === 'workflow' ? part : null;
  });

  protected readonly toolPart = computed(() => {
    const part = this.part();
    return part.kind === 'tool' ? part : null;
  });

  protected readonly dataPart = computed(() => {
    const part = this.part();
    return part.kind === 'data' ? part : null;
  });

  protected readonly artifactPart = computed(() => {
    const part = this.part();
    return part.kind === 'artifact' ? part : null;
  });

  protected readonly errorPart = computed(() => {
    const part = this.part();
    return part.kind === 'error' ? part : null;
  });
}
