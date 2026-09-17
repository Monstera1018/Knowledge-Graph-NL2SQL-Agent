import { NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import {
  catchError,
  debounceTime,
  distinctUntilChanged,
  forkJoin,
  of,
  Subject,
  switchMap,
} from 'rxjs';

import { LIST_ALL_PAGE_SIZE, listAllQuery } from '../../core/api/list-query';
import { MetadataApiService } from '../../core/api/metadata-api.service';
import { createRequestGuard } from '../../core/api/request-guard';
import { GraphSnapshot, KnowledgeNode, KnowledgeRelation } from '../../core/models/metadata.models';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';
import { ConfirmDialogService } from '../../shared/feedback/confirm-dialog.service';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';
import { GraphSnapshotComponent } from '../../shared/components/graph-snapshot/graph-snapshot.component';
import { KnowledgeRelationDialogComponent } from './knowledge-relation-dialog.component';
import { KnowledgeRelationDialogResult } from './knowledge-relation-dialog.models';
import {
  KnowledgeFormDraft,
  KnowledgeFormMode,
  KnowledgeRelationDraft,
  cloneRelations,
  emptyKnowledgeForm,
  relationsFromGraph,
} from './knowledge.models';

type KnowledgeDetailView = 'detail' | 'graph';

@Component({
  selector: 'app-knowledge-page',
  imports: [
    NgTemplateOutlet,
    FormsModule,
    MatIconModule,
    MatProgressSpinnerModule,
    EmptyStateComponent,
    GraphSnapshotComponent,
  ],
  templateUrl: './knowledge.page.html',
  styleUrl: './knowledge.page.css',
})
export class KnowledgePage {
  private readonly api = inject(MetadataApiService);
  private readonly snack = inject(AppSnackService);
  private readonly confirmDialog = inject(ConfirmDialogService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);
  private readonly search$ = new Subject<string>();

  protected readonly items = signal<KnowledgeNode[]>([]);
  protected readonly loading = signal(false);
  protected readonly refreshing = signal(false);
  protected readonly query = signal('');
  protected readonly total = signal(0);
  protected readonly selected = signal<KnowledgeNode | null>(null);
  protected readonly relations = signal<KnowledgeRelationDraft[]>([]);
  protected readonly relationsLoading = signal(false);
  protected readonly formMode = signal<KnowledgeFormMode>('view');
  protected readonly formDraft = signal<KnowledgeFormDraft>(emptyKnowledgeForm());
  protected readonly formSaving = signal(false);
  protected readonly descriptionExpanded = signal(false);
  protected readonly detailView = signal<KnowledgeDetailView>('detail');
  protected readonly overviewSnapshot = signal<GraphSnapshot | null>(null);
  protected readonly graphSnapshot = signal<GraphSnapshot | null>(null);
  protected readonly graphLoading = signal(false);
  protected readonly graphDepth = signal(1);
  protected readonly graphFocusUuid = signal<string | null>(null);
  protected readonly relationStats = computed(() => {
    const rels = this.formMode() === 'view' ? this.relations() : this.formDraft().relations;
    return {
      tables: rels.length,
      columns: rels.reduce((sum, relation) => sum + relation.column_names.length, 0),
    };
  });

  protected readonly hasDescription = computed(() => {
    const description = this.selected()?.description?.trim() ?? '';
    return description.length > 0;
  });

  protected readonly longDescription = computed(() => {
    const description = this.selected()?.description?.trim() ?? '';
    if (!description) {
      return false;
    }
    const lineCount = description.split(/\r?\n/).length;
    return description.length > 160 || lineCount > 4;
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
          return this.api.listKnowledge(listAllQuery({ q: q || undefined })).pipe(
            catchError((err) => {
              this.loading.set(false);
              this.handleError('加载知识条目失败', err);
              return of({ items: [] as KnowledgeNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE });
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

  protected selectItem(item: KnowledgeNode): void {
    if (this.formMode() === 'create') {
      return;
    }
    this.formMode.set('view');
    this.formDraft.set(emptyKnowledgeForm());
    this.descriptionExpanded.set(false);
    this.detailView.set('detail');
    this.relations.set([]);
    this.overviewSnapshot.set(null);
    this.selected.set(item);
    this.loadDetail(item.uuid);
  }

  protected startCreate(): void {
    this.formMode.set('create');
    this.selected.set(null);
    this.relations.set([]);
    this.formDraft.set(emptyKnowledgeForm());
    this.descriptionExpanded.set(false);
    this.detailView.set('detail');
    this.overviewSnapshot.set(null);
    this.graphSnapshot.set(null);
  }

  protected startEdit(): void {
    const item = this.selected();
    if (!item) {
      return;
    }
    const relationDraft = cloneRelations(this.relations());
    this.formDraft.set({
      name: item.name,
      description: item.description ?? '',
      relations: relationDraft,
    });
    this.formMode.set('edit');
  }

  protected cancelForm(): void {
    this.formMode.set('view');
    this.formDraft.set(emptyKnowledgeForm());
    const item = this.selected();
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
      description: draft.description.trim(),
      relations: this.serializeRelations(draft.relations),
    };

    this.formSaving.set(true);
    const mode = this.formMode();

    const request =
      mode === 'create'
        ? this.api.createKnowledge(body)
        : this.api.updateKnowledge(this.selected()!.uuid, body);

    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (saved) => {
        this.formSaving.set(false);
        this.formMode.set('view');
        this.formDraft.set(emptyKnowledgeForm());
        this.fetchItems(this.query());
        this.selected.set(saved);
        this.loadDetail(saved.uuid);
        this.snack.success(mode === 'create' ? '知识条目已创建' : '知识条目已更新');
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
      title: '删除知识条目',
      message: `确定删除「${item.name}」？`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }

    this.api
      .deleteKnowledge(item.uuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.selected.set(null);
          this.relations.set([]);
          this.formMode.set('view');
          this.formDraft.set(emptyKnowledgeForm());
          this.detailView.set('detail');
          this.overviewSnapshot.set(null);
          this.graphSnapshot.set(null);
          this.fetchItems(this.query());
          this.snack.success('知识条目已删除');
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

  protected toggleDescription(): void {
    this.descriptionExpanded.update((value) => !value);
  }

  protected patchFormField(field: 'name' | 'description', value: string): void {
    this.formDraft.update((draft) => ({ ...draft, [field]: value }));
  }

  protected openRelationDialog(editIndex?: number): void {
    const relations = this.formDraft().relations;
    const mode = editIndex === undefined ? 'add' : 'edit';
    const initial =
      editIndex !== undefined
        ? {
            table_name: relations[editIndex].table_name,
            column_names: [...relations[editIndex].column_names],
          }
        : undefined;
    const blockedTableKeys = relations
      .filter((_, index) => index !== editIndex)
      .map((relation) => this.tableKey(relation.table_name));

    const ref = this.dialog.open(KnowledgeRelationDialogComponent, {
      data: { mode, initial, blockedTableKeys },
      panelClass: 'app-knowledge-relation-dialog-panel',
      backdropClass: 'app-dialog-backdrop',
      autoFocus: 'first-tabbable',
      restoreFocus: true,
      width: 'min(56rem, calc(100vw - 2rem))',
      maxHeight: '90vh',
    });

    ref
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result: KnowledgeRelationDialogResult) => {
        if (!result) {
          return;
        }
        this.applyRelationDialogResult(result, editIndex);
      });
  }

  protected removeRelation(index: number): void {
    this.formDraft.update((draft) => ({
      ...draft,
      relations: draft.relations.filter((_, i) => i !== index),
    }));
  }

  private applyRelationDialogResult(result: KnowledgeRelationDraft, editIndex?: number): void {
    const key = this.tableKey(result.table_name);
    if (editIndex !== undefined) {
      this.formDraft.update((draft) => ({
        ...draft,
        relations: draft.relations.map((relation, index) =>
          index === editIndex
            ? {
                table_name: result.table_name,
                column_names: [...result.column_names],
              }
            : relation,
        ),
      }));
      return;
    }

    const exists = this.formDraft().relations.some(
      (relation) => this.tableKey(relation.table_name) === key,
    );
    if (exists) {
      this.snack.info('该表已添加');
      return;
    }

    this.formDraft.update((draft) => ({
      ...draft,
      relations: [
        ...draft.relations,
        {
          table_name: result.table_name,
          column_names: [...result.column_names],
        },
      ],
    }));
  }

  private serializeRelations(relations: KnowledgeRelationDraft[]): KnowledgeRelation[] {
    return relations.map((relation) => ({
      table_name: relation.table_name,
      column_names: [...relation.column_names],
    }));
  }

  private tableKey(tableName: string): string {
    return tableName.trim().toLowerCase();
  }

  private loadDetail(knowledgeUuid: string): void {
    const requestId = this.detailGuard.next();
    this.relationsLoading.set(true);
    forkJoin({
      detail: this.api.getKnowledge(knowledgeUuid).pipe(catchError(() => of(null))),
      graph: this.api.getGraph(knowledgeUuid, { depth: 1 }).pipe(catchError(() => of(null))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ detail, graph }) => {
          if (!this.detailGuard.isLatest(requestId) || this.selected()?.uuid !== knowledgeUuid) {
            return;
          }
          if (!detail && !graph) {
            this.relationsLoading.set(false);
            this.relations.set([]);
            this.overviewSnapshot.set(null);
            this.handleError('加载知识详情失败', '无法获取详情与图谱');
            return;
          }

          this.overviewSnapshot.set(graph);
          const apiRelations = cloneRelations(detail?.relations);
          const graphRelations = relationsFromGraph(graph);
          const merged = apiRelations.length > 0 ? apiRelations : graphRelations;

          if (detail) {
            this.selected.set(detail);
          }
          this.relations.set(merged);
          this.relationsLoading.set(false);
        },
        error: (err) => {
          if (!this.detailGuard.isLatest(requestId)) {
            return;
          }
          this.relationsLoading.set(false);
          this.relations.set([]);
          this.overviewSnapshot.set(null);
          this.handleError('加载知识详情失败', err);
        },
      });
  }

  private fetchItems(q = ''): void {
    const requestId = this.listGuard.next();
    this.loading.set(true);
    this.api
      .listKnowledge(listAllQuery({ q: q || undefined }))
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
          this.handleError('加载知识条目失败', err);
        },
      });
  }

  private syncSelectionAfterLoad(): void {
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
      this.formDraft.set(emptyKnowledgeForm());
      this.detailView.set('detail');
      this.overviewSnapshot.set(null);
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

  private handleError(message: string, err: unknown): void {
    this.loading.set(false);
    this.snack.error(`${message}: ${formatApiError(err)}`);
  }
}
