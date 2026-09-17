import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

import { pageTransition } from '../../shared/animations/page-transitions';
import { createReducedMotionSignal } from '../../shared/utils/reduced-motion';
import { AuthService } from '../auth/auth.service';
import { WorkspaceService } from '../workspace/workspace.service';

interface NavItem {
  label: string;
  path: string;
  icon: string;
}

/** 与 CSS --sidebar-duration 保持一致（毫秒） */
const SIDEBAR_ANIM_MS = 380;

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MatIconModule, MatTooltipModule],
  templateUrl: './app-shell.component.html',
  styleUrl: './app-shell.component.css',
  animations: [pageTransition],
})
export class AppShellComponent {
  private readonly auth = inject(AuthService);
  private readonly workspace = inject(WorkspaceService);
  protected readonly reduceMotion = createReducedMotionSignal();

  protected readonly collapsed = signal(true);
  protected readonly animating = signal(false);
  protected readonly user = this.auth.user;
  protected readonly workspaceIds = this.workspace.workspaceIds;
  protected readonly currentWorkspaceId = this.workspace.workspaceId;
  protected readonly canSwitchWorkspace = this.workspace.canSwitchWorkspace;

  protected readonly displayName = computed(
    () => this.user()?.display_name?.trim() || this.user()?.username || '',
  );

  protected readonly username = computed(() => this.user()?.username ?? '');

  protected readonly userInitial = computed(() => {
    const name = this.displayName();
    return name ? name.charAt(0).toUpperCase() : '?';
  });

  protected readonly navItems: NavItem[] = [
    { label: '智能对话', path: '/chat', icon: 'forum' },
    { label: '数据目录', path: '/catalog', icon: 'account_tree' },
    { label: '枚举值', path: '/enums', icon: 'format_list_bulleted' },
    { label: '表关系', path: '/relations', icon: 'hub' },
    { label: '业务知识', path: '/knowledge', icon: 'lightbulb' },
    { label: 'SQL', path: '/sql', icon: 'code' },
    { label: '导入任务', path: '/ingest', icon: 'upload_file' },
  ];

  protected toggleSidebar(): void {
    if (this.animating()) {
      return;
    }

    this.animating.set(true);
    this.collapsed.update((value) => !value);

    window.setTimeout(() => this.animating.set(false), SIDEBAR_ANIM_MS);
  }

  protected selectWorkspace(workspaceId: string): void {
    if (workspaceId === this.workspace.currentWorkspaceId()) {
      return;
    }
    this.workspace.setWorkspaceId(workspaceId);
    window.location.reload();
  }

  protected logout(): void {
    this.auth.logout().subscribe();
  }

  protected pageState(outlet: RouterOutlet): string {
    return outlet?.isActivated ? (outlet.activatedRoute.snapshot.routeConfig?.path ?? '') : '';
  }
}
