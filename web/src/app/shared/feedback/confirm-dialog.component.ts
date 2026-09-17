import { Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

import { ConfirmDialogData } from './confirm-dialog.models';

@Component({
  selector: 'app-confirm-dialog',
  imports: [MatDialogModule],
  template: `
    <div
      class="app-confirm"
      [class.app-confirm--danger]="data.danger"
      role="alertdialog"
      [attr.aria-labelledby]="titleId"
      [attr.aria-describedby]="messageId"
    >
      <h2 class="app-confirm__title" [id]="titleId">{{ data.title }}</h2>
      <p class="app-confirm__message" [id]="messageId">{{ data.message }}</p>
      <footer class="app-confirm__actions">
        <button type="button" class="app-btn" (click)="cancel()">
          {{ data.cancelLabel ?? '取消' }}
        </button>
        <button
          type="button"
          class="app-btn app-confirm__submit"
          [class.app-confirm__submit--danger]="data.danger"
          (click)="confirm()"
        >
          {{ data.confirmLabel ?? '确定' }}
        </button>
      </footer>
    </div>
  `,
  styleUrl: './confirm-dialog.component.css',
})
export class ConfirmDialogComponent {
  protected readonly data = inject<ConfirmDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<ConfirmDialogComponent, boolean>);
  private readonly dialogInstanceId = ConfirmDialogComponent.nextId();

  protected readonly titleId = `app-confirm-title-${this.dialogInstanceId}`;
  protected readonly messageId = `app-confirm-message-${this.dialogInstanceId}`;

  private static idSeq = 0;

  private static nextId(): number {
    ConfirmDialogComponent.idSeq += 1;
    return ConfirmDialogComponent.idSeq;
  }

  protected cancel(): void {
    this.dialogRef.close(false);
  }

  protected confirm(): void {
    this.dialogRef.close(true);
  }
}
