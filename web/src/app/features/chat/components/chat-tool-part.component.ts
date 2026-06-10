import { JsonPipe } from '@angular/common';
import { Component, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { ToolPart } from '../../../core/models/chat.models';

@Component({
  selector: 'app-chat-tool-part',
  imports: [MatIconModule, JsonPipe],
  templateUrl: './chat-tool-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatToolPartComponent {
  readonly part = input.required<ToolPart>();
}
