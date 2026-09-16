import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { finalize } from 'rxjs';

import { AuthService } from '../../core/auth/auth.service';
import { ChatSessionStore } from '../../core/chat/chat-session.store';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';

@Component({
  selector: 'app-login-page',
  imports: [FormsModule, MatIconModule, MatProgressSpinnerModule],
  templateUrl: './login.page.html',
  styleUrl: './login.page.css',
})
export class LoginPage {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly snack = inject(AppSnackService);
  private readonly chats = inject(ChatSessionStore);

  protected readonly username = signal('admin');
  protected readonly password = signal('admin');
  protected readonly submitting = signal(false);
  protected readonly error = signal('');

  protected submit(): void {
    if (this.submitting()) {
      return;
    }
    const username = this.username().trim();
    const password = this.password();
    if (!username || !password) {
      this.error.set('请输入用户名和密码');
      return;
    }

    this.submitting.set(true);
    this.error.set('');
    this.auth
      .login({ username, password })
      .pipe(finalize(() => this.submitting.set(false)))
      .subscribe({
        next: () => {
          this.chats.prepareFreshChat();
          void this.router.navigateByUrl('/chat');
        },
        error: (err) => {
          this.error.set(formatApiError(err) || '登录失败');
          this.snack.info(this.error());
        },
      });
  }
}
