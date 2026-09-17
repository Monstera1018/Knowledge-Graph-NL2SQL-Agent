import { Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { ArtifactPart } from '../../../core/models/chat.models';
import { SqlHighlightComponent } from '../../../shared/components/sql-highlight/sql-highlight.component';
import { ChatMarkdownComponent } from './chat-markdown.component';

@Component({
  selector: 'app-chat-artifact-part',
  imports: [MatIconModule, SqlHighlightComponent, ChatMarkdownComponent],
  templateUrl: './chat-artifact-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatArtifactPartComponent {
  readonly part = input.required<ArtifactPart>();

  protected readonly isSql = computed(
    () => this.part().language.trim().toLowerCase() === 'sql',
  );

  protected readonly isThinking = computed(
    () => this.part().language.trim().toLowerCase() === 'thinking',
  );

  protected readonly isMarkdown = computed(() => {
    const lang = this.part().language.trim().toLowerCase();
    return lang === 'markdown' || lang === 'md' || lang === 'thinking';
  });
}
