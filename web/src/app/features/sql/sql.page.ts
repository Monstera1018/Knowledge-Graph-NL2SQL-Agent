import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { catchError, debounceTime, distinctUntilChanged, of, Subject, switchMap } from 'rxjs';

import { LIST_ALL_PAGE_SIZE, listAllQuery } from '../../core/api/list-query';
import { MetadataApiService } from '../../core/api/metadata-api.service';
import { createRequestGuard } from '../../core/api/request-guard';
import {
  GraphSnapshot,
  KnowledgeRelation,
  SQLNode,
} from '../../core/models/metadata.models';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';
import { ConfirmDialogService } from '../../shared/feedback/confirm-dialog.service';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';
import { GraphSnapshotComponent } from '../../shared/components/graph-snapshot/graph-snapshot.component';
import { SqlHighlightComponent } from '../../shared/components/sql-highlight/sql-highlight.component';
import { AppTruncateTooltipDirective } from '../../shared/directives/truncate-tooltip.directive';
import { SqlFormDraft, SqlFormMode, emptySqlForm, sqlIsEnabled, sqlSourceLabel, sqlTimeLabel, sqlToDraft } from './sql.models';

type SqlDetailView = 'detail' | 'graph';

@Component({
  selector: 'app-sql-page',
  imports: [
    FormsModule,
    MatIconModule,
    MatProgressSpinnerModule,
    EmptyStateComponent,
    GraphSnapshotComponent,
    SqlHighlightComponent,
    AppTruncateTooltipDirective,
  ],
  templateUrl: './sql.page.html',
  styleUrl: './sql.page.css',
})
export class SqlPage {
  private readonly api = inject(MetadataApiService);
  private readonly snack = inject(AppSnackService);
  private readonly confirmDialog = inject(ConfirmDialogService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly search$ = new Subject<string>();

  protected readonly items = signal<SQLNode[]>([]);
  protected readonly loading = signal(false);
  protected readonly refreshing = signal(false);
  protected readonly query = signal('');
  protected readonly total = signal(0);
  protected readonly selected = signal<SQLNode | null>(null);
  protected readonly relations = signal<KnowledgeRelation[]>([]);
  protected readonly relationsLoading = signal(false);
  protected readonly formMode = signal<SqlFormMode>('view');
  protected readonly formDraft = signal<SqlFormDraft>(emptySqlForm());
  protected readonly formSaving = signal(false);
  protected readonly logicExpanded = signal(false);
  protected readonly detailView = signal<SqlDetailView>('detail');
  protected readonly graphSnapshot = signal<GraphSnapshot | null>(null);
  protected readonly graphLoading = signal(false);
  protected readonly graphDepth = signal(1);
  protected readonly graphFocusUuid = signal<string | null>(null);

  protected readonly togglingUuid = signal<string | null>(null);
  protected readonly sourceFilter = signal('');
  protected readonly enabledFilter = signal('');
  protected readonly sort = signal('time_desc');

  protected readonly relationStats = computed(() => {
    const rels = this.relations();
    return {
      tables: rels.length,
      columns: rels.reduce((sum, relation) => sum + relation.column_names.length, 0),
    };
  });

  protected readonly hasLogic = computed(() => {
    return (this.selected()?.logic?.trim() ?? '').length > 0;
  });

  protected readonly hasContent = computed(() => {
    return (this.selected()?.content?.trim() ?? '').length > 0;
  });

  protected readonly longLogic = computed(() => {
    const logic = this.selected()?.logic?.trim() ?? '';
    if (!logic) {
      return false;
    }
    const lineCount = logic.split(/\r?\n/).length;
    return logic.length > 160 || lineCount > 4;
  });

  private refreshSpinTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly listGuard = createRequestGuard();
  private readonly detailGuard = createRequestGuard();
  private readonly graphGuard = createRequestGuard();

  constructor() {
    this.search$
      .pipe(
        debounceTime(300),
        distinctUntilChanged(),
        switchMap((q) => {
          this.loading.set(true);
          return this.api.listSqls(this.buildListQuery(q)).pipe(
            catchError((err) => {
              this.loading.set(false);
              this.handleError('加载 SQL 列表失败', err);
              return of({ items: [] as SQLNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE });
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((page) => {
        this.items.set(page.items);
        this.total.set(page.total);
        this.loading.set(false);
        this.syncSelectionAfterLoad();
      });

    this.fetchItems();
  }

  protected onQueryChange(value: string): void {
    this.query.set(value);
    this.search$.next(value);
  }

  protected onSourceFilterChange(value: string): void {
    this.sourceFilter.set(value);
    this.fetchItems();
  }

  protected onEnabledFilterChange(value: string): void {
    this.enabledFilter.set(value);
    this.fetchItems();
  }

  protected onSortChange(value: string): void {
    this.sort.set(value || 'time_desc');
    this.fetchItems();
  }

  protected selectItem(item: SQLNode): void {
    if (this.formMode() === 'create') {
      return;
    }
    this.formMode.set('view');
    this.formDraft.set(emptySqlForm());
    this.logicExpanded.set(false);
    this.relations.set([]);
    this.detailView.set('detail');
    this.graphSnapshot.set(null);
    this.selected.set(item);
    this.loadDetail(item.uuid);
  }

  protected startCreate(): void {
    this.formMode.set('create');
    this.selected.set(null);
    this.relations.set([]);
    this.formDraft.set(emptySqlForm());
    this.logicExpanded.set(false);
    this.detailView.set('detail');
    this.graphSnapshot.set(null);
  }

  protected startEdit(): void {
    const item = this.selected();
    if (!item) {
      return;
    }
    this.formDraft.set(sqlToDraft(item));
    this.formMode.set('edit');
  }

  protected cancelForm(): void {
    const item = this.selected();
    this.formMode.set('view');
    this.formDraft.set(emptySqlForm());
    if (item) {
      this.loadDetail(item.uuid);
    }
  }

  protected saveForm(event?: Event): void {
    event?.preventDefault();
    if (this.formSaving()) {
      return;
    }

    const draft = this.formDraft();
    const name = draft.name.trim();
    if (!name) {
      this.snack.info('名称不能为空');
      return;
    }

    const body = {
      name,
      logic: draft.logic.trim(),
      content: draft.content,
      dialect: draft.dialect.trim(),
    };

    this.formSaving.set(true);
    const mode = this.formMode();
    const request =
      mode === 'create'
        ? this.api.createSql(body)
        : this.api.updateSql(this.selected()!.uuid, body);

    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (saved) => {
        this.formSaving.set(false);
        this.formMode.set('view');
        this.formDraft.set(emptySqlForm());
        this.fetchItems(this.query());
        this.selected.set(saved);
        this.loadDetail(saved.uuid);
        this.snack.success(mode === 'create' ? 'SQL 已创建' : 'SQL 已更新');
      },
      error: (err) => {
        this.formSaving.set(false);
        this.handleError(mode === 'create' ? '创建失败' : '更新失败', err);
      },
    });
  }

  protected async deleteItem(): Promise<void> {
    const item = this.selected();
    if (!item) {
      return;
    }

    const confirmed = await this.confirmDialog.confirm({
      title: '删除 SQL',
      message: `确定删除「${item.name}」？`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }

    this.api
      .deleteSql(item.uuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.selected.set(null);
          this.relations.set([]);
          this.formMode.set('view');
          this.formDraft.set(emptySqlForm());
          this.detailView.set('detail');
          this.graphSnapshot.set(null);
          this.fetchItems(this.query());
          this.snack.success('SQL 已删除');
        },
        error: (err) => this.handleError('删除失败', err),
      });
  }

  protected refresh(): void {
    this.refreshing.set(true);
    if (this.refreshSpinTimer) {
      clearTimeout(this.refreshSpinTimer);
    }
    this.refreshSpinTimer = setTimeout(() => this.refreshing.set(false), 700);
    this.fetchItems(this.query());
    const item = this.selected();
    if (item) {
      this.loadDetail(item.uuid);
      if (this.detailView() === 'graph') {
        const focus = this.graphFocusUuid() ?? item.uuid;
        this.loadGraph(focus, { depth: this.graphDepth() });
      }
    }
  }

  protected openGraphView(focusUuid: string): void {
    this.graphFocusUuid.set(focusUuid);
    this.detailView.set('graph');
    this.loadGraph(focusUuid, { depth: this.graphDepth() });
  }

  protected closeGraphView(): void {
    this.detailView.set('detail');
  }

  protected onGraphDepthChange(depth: number): void {
    this.graphDepth.set(depth);
    const focus = this.graphFocusUuid();
    if (focus) {
      this.loadGraph(focus, { depth });
    }
  }

  protected toggleLogic(): void {
    this.logicExpanded.update((value) => !value);
  }

  protected sourceLabel(item: SQLNode | null): string {
    return sqlSourceLabel(item);
  }

  protected isEnabled(item: SQLNode | null): boolean {
    return sqlIsEnabled(item);
  }

  protected timeLabel(item: SQLNode | null): string {
    return sqlTimeLabel(item);
  }

  protected toggleEnabled(event: Event, item: SQLNode): void {
    event.preventDefault();
    event.stopPropagation();
    if (this.togglingUuid() === item.uuid) {
      return;
    }
    const nextEnabled = !sqlIsEnabled(item);
    this.togglingUuid.set(item.uuid);
    this.api
      .updateSql(item.uuid, { enabled: nextEnabled })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (saved) => {
          this.togglingUuid.set(null);
          this.items.update((items) =>
            items.map((row) => (row.uuid === saved.uuid ? { ...row, ...saved } : row)),
          );
          if (this.selected()?.uuid === saved.uuid) {
            this.selected.update((current) => (current ? { ...current, ...saved } : saved));
          }
          this.snack.success(nextEnabled ? '已启用，将用于 SQL 生成' : '已停用，不再用于 SQL 生成');
        },
        error: (err) => {
          this.togglingUuid.set(null);
          this.handleError('更新启用状态失败', err);
        },
      });
  }

  protected patchFormField(field: keyof SqlFormDraft, value: string): void {
    this.formDraft.update((draft) => ({ ...draft, [field]: value }));
  }

  private fetchItems(q = this.query()): void {
    const requestId = this.listGuard.next();
    this.loading.set(true);
    this.api
      .listSqls(this.buildListQuery(q))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          if (!this.listGuard.isLatest(requestId)) {
            return;
          }
          this.items.set(page.items);
          this.total.set(page.total);
          this.loading.set(false);
          this.syncSelectionAfterLoad();
        },
        error: (err) => {
          if (!this.listGuard.isLatest(requestId)) {
            return;
          }
          this.loading.set(false);
          this.handleError('加载 SQL 列表失败', err);
        },
      });
  }

  private loadDetail(sqlUuid: string): void {
    const requestId = this.detailGuard.next();
    this.relationsLoading.set(true);
    this.api
      .getSql(sqlUuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (detail) => {
          if (!this.detailGuard.isLatest(requestId) || this.selected()?.uuid !== sqlUuid) {
            return;
          }
          this.relationsLoading.set(false);
          if (!detail) {
            this.relations.set([]);
            this.handleError('加载 SQL 详情失败', '无法获取详情');
            return;
          }
          this.selected.set(detail);
          this.relations.set(detail.relations ?? []);
        },
        error: (err) => {
          if (!this.detailGuard.isLatest(requestId)) {
            return;
          }
          this.relationsLoading.set(false);
          this.handleError('加载 SQL 详情失败', err);
        },
      });
  }

  private syncSelectionAfterLoad(): void {
    if (this.formMode() === 'create') {
      return;
    }
    const current = this.selected();
    if (!current) {
      return;
    }
    const refreshed = this.items().find((item) => item.uuid === current.uuid);
    if (refreshed) {
      this.selected.set(refreshed);
    } else {
      this.selected.set(null);
      this.relations.set([]);
      this.formMode.set('view');
      this.formDraft.set(emptySqlForm());
      this.detailView.set('detail');
      this.graphSnapshot.set(null);
    }
  }

  private loadGraph(nodeUuid: string, options: { depth?: number } = {}): void {
    const requestId = this.graphGuard.next();
    this.graphLoading.set(true);
    this.api
      .getGraph(nodeUuid, {
        depth: options.depth ?? this.graphDepth(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (snapshot) => {
          if (!this.graphGuard.isLatest(requestId)) {
            return;
          }
          this.graphSnapshot.set(snapshot);
          this.graphLoading.set(false);
        },
        error: (err) => {
          if (!this.graphGuard.isLatest(requestId)) {
            return;
          }
          this.graphLoading.set(false);
          this.graphSnapshot.set(null);
          this.handleError('加载关系图谱失败', err);
        },
      });
  }

  private buildListQuery(q = this.query()) {
    const enabledFilter = this.enabledFilter();
    return listAllQuery({
      q: q.trim() || undefined,
      source: this.sourceFilter() || undefined,
      enabled: enabledFilter === '' ? undefined : enabledFilter,
      sort: this.sort() || 'time_desc',
    });
  }

  private handleError(message: string, err: unknown): void {
    this.snack.error(`${message}: ${formatApiError(err)}`);
  }
}
