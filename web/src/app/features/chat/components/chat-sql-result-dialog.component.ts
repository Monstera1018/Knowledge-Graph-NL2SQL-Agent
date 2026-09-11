import { Component, computed, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';

import type { ChatSqlResultDialogData } from './chat-sql-result-dialog.models';

@Component({
  selector: 'app-chat-sql-result-dialog',
  imports: [MatDialogModule, MatIconModule],
  templateUrl: './chat-sql-result-dialog.component.html',
  styleUrl: './chat-sql-result-dialog.component.css',
})
export class ChatSqlResultDialogComponent {
  private readonly dialogRef = inject(MatDialogRef<ChatSqlResultDialogComponent>);
  protected readonly data = inject<ChatSqlResultDialogData>(MAT_DIALOG_DATA);

  protected readonly subtitle = computed(() => {
    const rowLabel = `共 ${this.data.rows.length} 行`;
    const setCount = this.data.setCount ?? 1;
    const setIndex = this.data.setIndex ?? 0;
    if (setCount > 1) {
      return `结果集 ${setIndex + 1} / ${setCount} · ${rowLabel}`;
    }
    return rowLabel;
  });

  protected close(): void {
    this.dialogRef.close();
  }
}
