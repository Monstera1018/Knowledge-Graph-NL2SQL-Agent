import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

import { AppSnackComponent } from './app-snack.component';
import { AppSnackKind } from './app-snack.models';

export type { AppSnackKind } from './app-snack.models';

@Injectable({ providedIn: 'root' })
export class AppSnackService {
  private readonly snackBar = inject(MatSnackBar);

  show(message: string, kind: AppSnackKind = 'info'): void {
    this.snackBar.openFromComponent(AppSnackComponent, {
      data: { message, kind },
      duration: kind === 'error' ? 5000 : 2400,
      horizontalPosition: 'center',
      verticalPosition: 'top',
      panelClass: ['app-snack-host'],
    });
  }

  success(message: string): void {
    this.show(message, 'success');
  }

  error(message: string): void {
    this.show(message, 'error');
  }

  info(message: string): void {
    this.show(message, 'info');
  }
}
