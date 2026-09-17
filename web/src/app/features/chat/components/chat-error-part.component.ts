import { Component, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { ErrorPart } from '../../../core/models/chat.models';

@Component({
  selector: 'app-chat-error-part',
  imports: [MatIconModule],
  templateUrl: './chat-error-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatErrorPartComponent {
  readonly part = input.required<ErrorPart>();
}
