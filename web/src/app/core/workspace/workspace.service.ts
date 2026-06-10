import { Injectable, computed, signal } from '@angular/core';

const workspaceStorageKey = (userId: string) => `tyws.workspace.${userId}`;

@Injectable({ providedIn: 'root' })
export class WorkspaceService {
  private readonly allowedIdsSignal = signal<string[]>([]);
  private readonly workspaceIdSignal = signal('');
  private readonly userIdSignal = signal('');

  readonly workspaceId = this.workspaceIdSignal.asReadonly();
  readonly workspaceIds = this.allowedIdsSignal.asReadonly();
  readonly hasWorkspaces = computed(() => this.allowedIdsSignal().length > 0);
  readonly canSwitchWorkspace = computed(() => this.allowedIdsSignal().length > 1);

  applyUserWorkspaces(
    userId: string,
    workspaceIds: string[],
    defaultWorkspaceId: string,
  ): void {
    const uid = userId.trim();
    const allowed = workspaceIds.map((id) => id.trim()).filter(Boolean);
    this.userIdSignal.set(uid);
    this.allowedIdsSignal.set(allowed);

    const saved = uid ? this.readPersisted(uid) : '';
    const initial =
      saved && allowed.includes(saved)
        ? saved
        : allowed.includes(defaultWorkspaceId.trim())
          ? defaultWorkspaceId.trim()
          : (allowed[0] ?? '');

    if (initial) {
      this.workspaceIdSignal.set(initial);
      if (uid) {
        this.persist(uid, initial);
      }
    } else {
      this.workspaceIdSignal.set('');
    }
  }

  setWorkspaceId(value: string): void {
    const workspaceId = value.trim();
    const allowed = this.allowedIdsSignal();
    if (!workspaceId || !allowed.includes(workspaceId)) {
      return;
    }
    this.workspaceIdSignal.set(workspaceId);
    const uid = this.userIdSignal();
    if (uid) {
      this.persist(uid, workspaceId);
    }
  }

  currentWorkspaceId(): string {
    return this.workspaceIdSignal();
  }

  clear(): void {
    const uid = this.userIdSignal();
    if (uid) {
      sessionStorage.removeItem(workspaceStorageKey(uid));
    }
    this.userIdSignal.set('');
    this.allowedIdsSignal.set([]);
    this.workspaceIdSignal.set('');
  }

  private readPersisted(userId: string): string {
    return sessionStorage.getItem(workspaceStorageKey(userId))?.trim() ?? '';
  }

  private persist(userId: string, workspaceId: string): void {
    sessionStorage.setItem(workspaceStorageKey(userId), workspaceId);
  }
}
