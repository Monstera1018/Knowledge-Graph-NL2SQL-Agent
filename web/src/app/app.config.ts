import {
  ApplicationConfig,
  ErrorHandler,
  Injectable,
  importProvidersFrom,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { MARKED_OPTIONS, provideMarkdown } from 'ngx-markdown';
import { MAT_ICON_DEFAULT_OPTIONS, MatIconRegistry } from '@angular/material/icon';
import { MatDialogModule } from '@angular/material/dialog';
import { MatSnackBarModule } from '@angular/material/snack-bar';

import { routes } from './app.routes';
import { authInterceptor } from './core/auth/auth.interceptor';
import { AuthService } from './core/auth/auth.service';

function isResizeObserverLoopError(error: unknown): boolean {
  const parts: string[] = [];
  if (error instanceof Error) {
    parts.push(error.message);
    if (error.cause != null) {
      parts.push(String(error.cause));
    }
  } else if (typeof error === 'object' && error != null && 'message' in error) {
    parts.push(String((error as ErrorEvent).message));
  }
  parts.push(String(error));
  return parts.some((part) => part.includes('ResizeObserver loop'));
}

@Injectable()
class AppErrorHandler implements ErrorHandler {
  handleError(error: unknown): void {
    if (isResizeObserverLoopError(error)) {
      return;
    }
    console.error(error);
  }
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    { provide: ErrorHandler, useClass: AppErrorHandler },
    provideAnimations(),
    provideRouter(routes),
    provideHttpClient(withFetch(), withInterceptors([authInterceptor])),
    provideAppInitializer(() => inject(AuthService).bootstrap()),
    provideMarkdown(),
    {
      provide: MARKED_OPTIONS,
      useValue: { gfm: true, breaks: true },
    },
    importProvidersFrom(MatSnackBarModule, MatDialogModule),
    provideAppInitializer(() => {
      window.addEventListener(
        'error',
        (event) => {
          if (isResizeObserverLoopError(event.error ?? event.message)) {
            event.stopImmediatePropagation();
            event.preventDefault();
          }
        },
        true,
      );
    }),
    provideAppInitializer(() => {
      const registry = inject(MatIconRegistry);
      registry.registerFontClassAlias(
        'material-symbols-outlined',
        'material-symbols-outlined mat-ligature-font',
      );
      registry.setDefaultFontSetClass('material-symbols-outlined', 'mat-ligature-font');
    }),
    {
      provide: MAT_ICON_DEFAULT_OPTIONS,
      useValue: { fontSet: 'material-symbols-outlined' },
    },
  ],
};
