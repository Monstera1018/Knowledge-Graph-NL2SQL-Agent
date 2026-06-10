import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import type { LoginRequest, LoginResponse, MeResponse } from './auth.models';

@Injectable({ providedIn: 'root' })
export class AuthApiService {
  private readonly http = inject(HttpClient);

  login(body: LoginRequest): Observable<LoginResponse> {
    return this.http.post<LoginResponse>('/api/auth/login', body);
  }

  logout(token: string): Observable<void> {
    return this.http.post<void>('/api/auth/logout', null, {
      headers: { Authorization: `Bearer ${token}` },
    });
  }

  me(token: string): Observable<MeResponse> {
    return this.http.get<MeResponse>('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
  }
}
