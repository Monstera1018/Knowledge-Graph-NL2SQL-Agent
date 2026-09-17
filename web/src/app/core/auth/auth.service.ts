import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, map, of, tap } from 'rxjs';

import { WorkspaceService } from '../workspace/workspace.service';
import { AuthApiService } from './auth-api.service';
import type { AuthUser, LoginRequest } from './auth.models';

const TOKEN_STORAGE_KEY = 'tyws.auth.token';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(AuthApiService);
  private readonly workspace = inject(WorkspaceService);
  private readonly router = inject(Router);

  private readonly tokenSignal = signal<string | null>(this.readToken());
  private readonly userSignal = signal<AuthUser | null>(null);
  private readonly bootstrappedSignal = signal(false);

  readonly token = this.tokenSignal.asReadonly();
  readonly user = this.userSignal.asReadonly();
  readonly bootstrapped = this.bootstrappedSignal.asReadonly();
  readonly isAuthenticated = computed(() => !!this.tokenSignal() && !!this.userSignal());

  bootstrap(): Promise<void> {
    const token = this.tokenSignal();
    if (!token) {
      this.bootstrappedSignal.set(true);
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      this.api.me(token).subscribe({
        next: ({ user }) => {
          this.applySession(token, user);
          this.bootstrappedSignal.set(true);
          resolve();
        },
        error: () => {
          this.clearSession();
          this.bootstrappedSignal.set(true);
          resolve();
        },
      });
    });
  }

  login(body: LoginRequest) {
    return this.api.login(body).pipe(
      tap(({ token, user }) => {
        this.applySession(token, user);
      }),
      map(() => void 0),
    );
  }

  logout() {
    const token = this.tokenSignal();
    const request$ = token
      ? this.api.logout(token).pipe(catchError(() => of(void 0)))
      : of(void 0);

    return request$.pipe(
      tap(() => {
        this.clearSession();
        void this.router.navigateByUrl('/login');
      }),
    );
  }

  applySession(token: string, user: AuthUser): void {
    this.tokenSignal.set(token);
    this.userSignal.set(user);
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
    this.workspace.applyUserWorkspaces(
      user.id,
      user.workspace_ids,
      user.default_workspace_id,
    );
  }

  clearSession(): void {
    this.tokenSignal.set(null);
    this.userSignal.set(null);
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    this.workspace.clear();
  }

  authHeaders(): Record<string, string> {
    const token = this.tokenSignal();
    if (!token) {
      return {};
    }
    return { Authorization: `Bearer ${token}` };
  }

  private readToken(): string | null {
    const token = localStorage.getItem(TOKEN_STORAGE_KEY)?.trim();
    return token || null;
  }
}
