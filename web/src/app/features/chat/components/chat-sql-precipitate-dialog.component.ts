import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

import { MetadataApiService } from '../../../core/api/metadata-api.service';
import { formatApiError } from '../../../shared/feedback/api-error';
import { AppSnackService } from '../../../shared/feedback/app-snack.service';
import type { SqlPrecipitateDraft } from '../sql-precipitate';

@Component({
  selector: 'app-chat-sql-precipitate-dialog',
  imports: [FormsModule, MatDialogModule],
  templateUrl: './chat-sql-precipitate-dialog.component.html',
  styleUrl: './chat-sql-precipitate-dialog.component.css',
})
export class ChatSqlPrecipitateDialogComponent {
  private readonly dialogRef = inject(MatDialogRef<ChatSqlPrecipitateDialogComponent, boolean>);
  private readonly api = inject(MetadataApiService);
  private readonly snack = inject(AppSnackService);
  private readonly data = inject<SqlPrecipitateDraft>(MAT_DIALOG_DATA);

  protected readonly name = signal(this.data.name);
  protected readonly logic = signal(this.data.logic);
  protected readonly content = signal(this.data.content);
  protected readonly saving = signal(false);

  protected cancel(): void {
    if (this.saving()) {
      return;
    }
    this.dialogRef.close(false);
  }

  protected submit(): void {
    if (this.saving()) {
      return;
    }
    const name = this.name().trim();
    const content = this.content().trim();
    if (!name || !content) {
      this.snack.error('样例名称和 SQL 不能为空');
      return;
    }
    this.saving.set(true);
    this.api
      .precipitateSql({
        name,
        logic: this.logic().trim(),
        content,
      })
      .subscribe({
        next: (result) => {
          this.saving.set(false);
          this.snack.success(precipitateMessage(result.action));
          this.dialogRef.close(true);
        },
        error: (err) => {
          this.saving.set(false);
          this.snack.error(`沉淀失败: ${formatApiError(err)}`);
        },
      });
  }
}

function precipitateMessage(action: string): string {
  if (action === 'merged') {
    return '已合并到相似样例，仍按原审核状态保留';
  }
  if (action === 'skipped') {
    return '已有相近的人工导入样例，未重复新建';
  }
  if (action === 'updated') {
    return '已更新已有样例';
  }
  return '已加入待审核';
}
