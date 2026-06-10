import { Component, inject } from '@angular/core';
import { MAT_SNACK_BAR_DATA, MatSnackBarRef } from '@angular/material/snack-bar';
import { MatIconModule } from '@angular/material/icon';

import { AppSnackData } from './app-snack.models';

@Component({
  selector: 'app-snack-toast',
  imports: [MatIconModule],
  template: `
    <div class="app-toast" [class]="'app-toast--' + data.kind" role="status">
      <span class="app-toast__icon" aria-hidden="true">
        <mat-icon>{{ iconName }}</mat-icon>
      </span>
      <p class="app-toast__message">{{ data.message }}</p>
      @if (data.kind === 'error') {
        <button type="button" class="app-toast__dismiss" (click)="dismiss()">关闭</button>
      }
    </div>
  `,
  styleUrl: './app-snack.component.css',
})
export class AppSnackComponent {
  protected readonly data = inject<AppSnackData>(MAT_SNACK_BAR_DATA);
  private readonly snackRef = inject(MatSnackBarRef);

  protected get iconName(): string {
    switch (this.data.kind) {
      case 'success':
        return 'check_circle';
      case 'error':
        return 'error';
      default:
        return 'info';
    }
  }

  protected dismiss(): void {
    this.snackRef.dismiss();
  }
}
