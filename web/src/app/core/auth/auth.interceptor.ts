import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';
import { WorkspaceService } from '../workspace/workspace.service';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const workspace = inject(WorkspaceService);
  const router = inject(Router);

  let headers = req.headers;
  const authHeaders = auth.authHeaders();
  for (const [key, value] of Object.entries(authHeaders)) {
    headers = headers.set(key, value);
  }

  if (req.url.startsWith('/api/') && !req.url.startsWith('/api/auth/login')) {
    headers = headers.set('X-Workspace-Id', workspace.currentWorkspaceId());
  }

  return next(req.clone({ headers })).pipe(
    catchError((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 401) {
        auth.clearSession();
        void router.navigateByUrl('/login');
      }
      return throwError(() => error);
    }),
  );
};
