import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';

import { ConfirmDialogComponent } from './confirm-dialog.component';
import { ConfirmDialogData } from './confirm-dialog.models';

@Injectable({ providedIn: 'root' })
export class ConfirmDialogService {
  private readonly dialog = inject(MatDialog);

  confirm(data: ConfirmDialogData): Promise<boolean> {
    const ref = this.dialog.open(ConfirmDialogComponent, {
      data,
      panelClass: data.danger
        ? ['app-confirm-dialog-panel', 'app-confirm-dialog-panel--danger']
        : 'app-confirm-dialog-panel',
      backdropClass: 'app-dialog-backdrop',
      autoFocus: 'first-tabbable',
      restoreFocus: true,
      width: 'min(26rem, calc(100vw - 2rem))',
    });

    return firstValueFrom(ref.afterClosed()).then((result) => result === true);
  }
}
