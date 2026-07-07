import { NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { catchError, debounceTime, distinctUntilChanged, map, of, Subject, switchMap } from 'rxjs';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { LIST_ALL_PAGE_SIZE, listAllQuery } from '../../core/api/list-query';
import { MetadataApiService } from '../../core/api/metadata-api.service';
import { createRequestGuard } from '../../core/api/request-guard';
import {
  ColumnNode,
  EnumNode,
  GraphNodeView,
  GraphSnapshot,
  TableJoinRelation,
  TableNode,
} from '../../core/models/metadata.models';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';
import { ConfirmDialogService } from '../../shared/feedback/confirm-dialog.service';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';
import { GraphSnapshotComponent } from '../../shared/components/graph-snapshot/graph-snapshot.component';
import { AppTruncateTooltipDirective } from '../../shared/directives/truncate-tooltip.directive';
import { CatalogJoinRelationsEditorComponent } from './catalog-join-relations-editor.component';
import {
  CatalogFormMode,
  ColumnFormDraft,
  TableFormDraft,
  TableJoinDraft,
  countRelatedTables,
  emptyColumnForm,
  emptyTableForm,
  createJoinRelationDraft,
  joinRelationToDraft,
  serializeJoinRelations,
} from './catalog.models';
import {
  joinRelationPeerTooltip as formatJoinRelationPeerTooltip,
  joinRelationViewMeta,
  tableCommentForName,
} from './catalog.utils';

type CatalogDetailView = 'detail' | 'graph';
type TableDetailTab = 'overview' | 'relations' | 'knowledge' | 'sql';

@Component({
  selector: 'app-catalog-page',
  imports: [
    FormsModule,
    MatIconModule,
    MatProgressSpinnerModule,
    NgTemplateOutlet,
    EmptyStateComponent,
    GraphSnapshotComponent,
    AppTruncateTooltipDirective,
    CatalogJoinRelationsEditorComponent,
  ],
  templateUrl: './catalog.page.html',
  styleUrl: './catalog.page.css',
})
export class CatalogPage {
  private readonly api = inject(MetadataApiService);
  private readonly snack = inject(AppSnackService);
  private readonly confirmDialog = inject(ConfirmDialogService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly tableSearch$ = new Subject<string>();
  private readonly columnSearch$ = new Subject<{ tableUuid: string; q: string }>();

  protected readonly tables = signal<TableNode[]>([]);
  protected readonly columns = signal<ColumnNode[]>([]);
  protected readonly enums = signal<EnumNode[]>([]);
  protected readonly selectedTable = signal<TableNode | null>(null);
  protected readonly selectedColumn = signal<ColumnNode | null>(null);
  protected readonly tableQuery = signal('');
  protected readonly columnQuery = signal('');
  protected readonly tablesLoading = signal(false);
  protected readonly columnsLoading = signal(false);
  protected readonly enumsLoading = signal(false);
  protected readonly tablesTotal = signal(0);
  protected readonly columnsTotal = signal(0);
  protected readonly refreshing = signal(false);
  protected readonly tableFormMode = signal<CatalogFormMode>('view');
  protected readonly tableFormDraft = signal<TableFormDraft>(emptyTableForm());
  protected readonly tableFormSaving = signal(false);
  protected readonly columnFormMode = signal<CatalogFormMode>('view');
  protected readonly columnFormDraft = signal<ColumnFormDraft>(emptyColumnForm());
  protected readonly columnFormSaving = signal(false);
  protected readonly enumFormMode = signal<'view' | 'create' | 'edit'>('view');
  protected readonly enumEditingUuid = signal<string | null>(null);
  protected readonly enumDraft = signal('');
  protected readonly enumFormSaving = signal(false);
  protected readonly detailView = signal<CatalogDetailView>('detail');
  protected readonly tableDetailTab = signal<TableDetailTab>('overview');
  protected readonly tableOverviewSnapshot = signal<GraphSnapshot | null>(null);
  protected readonly graphSnapshot = signal<GraphSnapshot | null>(null);
  protected readonly graphLoading = signal(false);
  protected readonly graphDepth = signal(1);
  protected readonly graphFocusUuid = signal<string | null>(null);
  protected readonly tableJoinRelations = signal<TableJoinRelation[]>([]);
  protected readonly joinRelationsLoading = signal(false);

  protected readonly overviewStats = computed(() => {
    const table = this.selectedTable();
    const snapshot = this.tableOverviewSnapshot();
    if (snapshot) {
      const count = (type: GraphNodeView['type']) =>
        snapshot.nodes.filter((node) => node.type === type).length;
      return {
        columns: count('COLUMN'),
        relatedTables: table ? countRelatedTables(table.name, this.tableJoinRelations()) : 0,
        knowledge: count('KNOWLEDGE'),
        sql: count('SQL'),
      };
    }
    return {
      columns: this.columnsTotal(),
      relatedTables: table ? countRelatedTables(table.name, this.tableJoinRelations()) : 0,
      knowledge: 0,
      sql: 0,
    };
  });

  protected readonly hasDescription = computed(() => {
    const description = this.selectedTable()?.description?.trim() ?? '';
    return description.length > 0;
  });

  protected readonly overviewKnowledgeNodes = computed(() =>
    this.tableOverviewSnapshot()?.nodes.filter((node) => node.type === 'KNOWLEDGE') ?? [],
  );

  protected readonly overviewSqlNodes = computed(() =>
    this.tableOverviewSnapshot()?.nodes.filter((node) => node.type === 'SQL') ?? [],
  );

  private refreshSpinTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly tablesGuard = createRequestGuard();
  private readonly columnsGuard = createRequestGuard();
  private readonly overviewGuard = createRequestGuard();
  private readonly joinRelationsGuard = createRequestGuard();
  private readonly graphGuard = createRequestGuard();

  constructor() {
    this.tableSearch$
      .pipe(
        debounceTime(300),
        distinctUntilChanged(),
        switchMap((q) => {
          this.tablesLoading.set(true);
          return this.api.listTables(listAllQuery({ q: q || undefined })).pipe(
            catchError((err) => {
              this.tablesLoading.set(false);
              this.handleError('加载表列表失败', err);
              return of({ items: [] as TableNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE });
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((page) => {
        this.tables.set(page.items);
        this.tablesTotal.set(page.total);
        this.tablesLoading.set(false);
        this.syncSelectionAfterTableLoad();
      });

    this.columnSearch$
      .pipe(
        debounceTime(300),
        switchMap(({ tableUuid, q }) => {
          this.columnsLoading.set(true);
          return this.api
            .listColumns(listAllQuery({ table_uuid: tableUuid, q: q || undefined }))
            .pipe(
              map((page) => ({ page, tableUuid })),
              catchError((err) => {
                this.columnsLoading.set(false);
                this.handleError('加载列失败', err);
                return of({
                  page: { items: [] as ColumnNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE },
                  tableUuid,
                });
              }),
            );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(({ page, tableUuid }) => {
        if (this.selectedTable()?.uuid !== tableUuid) {
          return;
        }
        this.columns.set(page.items);
        this.columnsTotal.set(page.total);
        this.columnsLoading.set(false);
        this.syncSelectionAfterColumnLoad();
      });

    this.fetchTables();
  }

  protected onTableQueryChange(value: string): void {
    this.tableQuery.set(value);
    this.tableSearch$.next(value);
  }

  protected onColumnQueryChange(value: string): void {
    this.columnQuery.set(value);
    const table = this.selectedTable();
    if (table) {
      this.columnSearch$.next({ tableUuid: table.uuid, q: value });
    }
  }

  protected selectTable(table: TableNode): void {
    if (this.tableFormMode() === 'create') {
      return;
    }
    this.resetForms();
    this.selectedTable.set(table);
    this.selectedColumn.set(null);
    this.enums.set([]);
    this.detailView.set('detail');
    this.tableDetailTab.set('overview');
    this.graphDepth.set(1);
    this.columnQuery.set('');
    this.fetchColumns(table.uuid);
    this.loadTableOverview(table.uuid);
    this.loadTableJoinRelations(table.uuid);
  }

  protected selectColumn(column: ColumnNode): void {
    this.columnFormMode.set('view');
    this.cancelEnumForm();
    this.selectedColumn.set(column);
    this.detailView.set('detail');
    this.loadEnums(column.uuid);
  }

  protected startCreateTable(): void {
    this.resetForms();
    this.selectedTable.set(null);
    this.selectedColumn.set(null);
    this.columns.set([]);
    this.columnsTotal.set(0);
    this.enums.set([]);
    this.detailView.set('detail');
    this.tableDetailTab.set('overview');
    this.tableFormDraft.set(emptyTableForm());
    this.tableFormMode.set('create');
  }

  protected startEditTable(): void {
    const table = this.selectedTable();
    if (!table) {
      return;
    }
    this.columnFormMode.set('view');
    this.cancelEnumForm();
    this.tableFormDraft.set({
      name: table.name,
      comment: table.comment ?? '',
      description: table.description ?? '',
      joinRelations: this.tableJoinRelations().map((relation) =>
        joinRelationToDraft(table.name, relation),
      ),
    });
    this.tableFormMode.set('edit');
  }

  protected cancelTableForm(): void {
    this.tableFormMode.set('view');
    this.tableFormDraft.set(emptyTableForm());
  }

  protected saveTableForm(event?: Event): void {
    event?.preventDefault();
    if (this.tableFormSaving()) {
      return;
    }

    const draft = this.tableFormDraft();
    const name = draft.name.trim();
    if (!name) {
      this.snack.info('表名不能为空');
      return;
    }

    const joinRelations = serializeJoinRelations(name, draft.joinRelations);
    for (const relation of joinRelations) {
      if (relation.from_table_name === relation.to_table_name) {
        this.snack.info('表间关联的主表与从表不能相同');
        return;
      }
    }

    const tableBody = {
      name,
      comment: draft.comment.trim(),
      description: draft.description.trim(),
    };

    this.tableFormSaving.set(true);
    const mode = this.tableFormMode();
    const previousUuid = mode === 'edit' ? this.selectedTable()!.uuid : null;

    const request =
      mode === 'create'
        ? this.api.createTable(tableBody).pipe(
            switchMap((saved) =>
              joinRelations.length
                ? this.api.replaceTableJoinRelations(saved.uuid, joinRelations).pipe(
                    map((relations) => ({ saved, relations, previousUuid: null })),
                  )
                : of({ saved, relations: [] as TableJoinRelation[], previousUuid: null }),
            ),
          )
        : this.api.updateTable(previousUuid!, tableBody).pipe(
            switchMap((saved) =>
              this.api.replaceTableJoinRelations(saved.uuid, joinRelations).pipe(
                map((relations) => ({ saved, relations, previousUuid })),
              ),
            ),
          );

    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: ({ saved, relations, previousUuid }) => {
        this.tableFormSaving.set(false);
        this.tableFormMode.set('view');
        this.tableFormDraft.set(emptyTableForm());
        this.tableJoinRelations.set(relations);
        this.fetchTables(this.tableQuery());

        if (mode === 'create') {
          this.selectedTable.set(saved);
          this.fetchColumns(saved.uuid);
          this.loadTableOverview(saved.uuid);
          this.snack.success('表已创建');
        } else {
          this.applyTableUpdate(saved, previousUuid ?? undefined);
          this.snack.success('表已更新');
        }
      },
      error: (err) => {
        this.tableFormSaving.set(false);
        this.handleError(mode === 'create' ? '创建表失败' : '更新表失败', err);
      },
    });
  }

  protected async deleteTable(): Promise<void> {
    const table = this.selectedTable();
    if (!table) {
      return;
    }

    const confirmed = await this.confirmDialog.confirm({
      title: '删除表',
      message: `确定删除表「${table.name}」？关联的列与枚举也会一并删除。`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }

    this.api
      .deleteTable(table.uuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.selectedTable.set(null);
          this.selectedColumn.set(null);
          this.columns.set([]);
          this.columnsTotal.set(0);
          this.enums.set([]);
          this.tableOverviewSnapshot.set(null);
          this.tableJoinRelations.set([]);
          this.graphSnapshot.set(null);
          this.resetForms();
          this.fetchTables(this.tableQuery());
          this.snack.success('表已删除');
        },
        error: (err) => this.handleError('删除表失败', err),
      });
  }

  protected startCreateColumn(): void {
    const table = this.selectedTable();
    if (!table) {
      return;
    }
    this.selectedColumn.set(null);
    this.enums.set([]);
    this.cancelEnumForm();
    this.columnFormDraft.set(emptyColumnForm());
    this.columnFormMode.set('create');
  }

  protected startEditColumn(): void {
    const column = this.selectedColumn();
    if (!column) {
      return;
    }
    this.cancelEnumForm();
    this.columnFormDraft.set({
      name: column.name,
      comment: column.comment ?? '',
      dtype: column.dtype ?? '',
    });
    this.columnFormMode.set('edit');
  }

  protected cancelColumnForm(): void {
    this.columnFormMode.set('view');
    this.columnFormDraft.set(emptyColumnForm());
  }

  protected saveColumnForm(event?: Event): void {
    event?.preventDefault();
    const table = this.selectedTable();
    if (!table || this.columnFormSaving()) {
      return;
    }

    const draft = this.columnFormDraft();
    const name = draft.name.trim();
    if (!name) {
      this.snack.info('列名不能为空');
      return;
    }

    const body = {
      name,
      comment: draft.comment.trim(),
      dtype: draft.dtype.trim(),
    };

    this.columnFormSaving.set(true);
    const mode = this.columnFormMode();

    const request =
      mode === 'create'
        ? this.api.createColumn({ table_uuid: table.uuid, ...body })
        : this.api.updateColumn(this.selectedColumn()!.uuid, body);

    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (saved) => {
        this.columnFormSaving.set(false);
        this.columnFormMode.set('view');
        this.columnFormDraft.set(emptyColumnForm());
        this.fetchColumns(table.uuid, this.columnQuery());
        this.loadTableOverview(table.uuid);

        if (mode === 'create') {
          this.selectedColumn.set(saved);
          this.loadEnums(saved.uuid);
          this.snack.success('列已创建');
        } else {
          this.selectedColumn.set(saved);
          this.snack.success('列已更新');
        }
      },
      error: (err) => {
        this.columnFormSaving.set(false);
        this.handleError(mode === 'create' ? '创建列失败' : '更新列失败', err);
      },
    });
  }

  protected async deleteColumn(): Promise<void> {
    const column = this.selectedColumn();
    const table = this.selectedTable();
    if (!column || !table) {
      return;
    }

    const confirmed = await this.confirmDialog.confirm({
      title: '删除列',
      message: `确定删除列「${column.name}」？关联的枚举值也会一并删除。`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }

    this.api
      .deleteColumn(column.uuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.selectedColumn.set(null);
          this.enums.set([]);
          this.columnFormMode.set('view');
          this.cancelEnumForm();
          this.fetchColumns(table.uuid, this.columnQuery());
          this.loadTableOverview(table.uuid);
          this.snack.success('列已删除');
        },
        error: (err) => this.handleError('删除列失败', err),
      });
  }

  protected startCreateEnum(): void {
    if (!this.selectedColumn()) {
      return;
    }
    this.enumEditingUuid.set(null);
    this.enumDraft.set('');
    this.enumFormMode.set('create');
  }

  protected startEditEnum(item: EnumNode): void {
    this.enumEditingUuid.set(item.uuid);
    this.enumDraft.set(item.value);
    this.enumFormMode.set('edit');
  }

  protected cancelEnumForm(): void {
    this.enumFormMode.set('view');
    this.enumEditingUuid.set(null);
    this.enumDraft.set('');
  }

  protected saveEnumForm(event?: Event): void {
    event?.preventDefault();
    const column = this.selectedColumn();
    if (!column || this.enumFormSaving()) {
      return;
    }

    const value = this.enumDraft().trim();
    if (!value) {
      this.snack.info('枚举值不能为空');
      return;
    }

    this.enumFormSaving.set(true);
    const mode = this.enumFormMode();

    const request =
      mode === 'create'
        ? this.api.createEnum({ column_uuid: column.uuid, value })
        : this.api.updateEnum(this.enumEditingUuid()!, { value });

    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.enumFormSaving.set(false);
        this.cancelEnumForm();
        this.loadEnums(column.uuid);
        this.snack.success(mode === 'create' ? '枚举值已添加' : '枚举值已更新');
      },
      error: (err) => {
        this.enumFormSaving.set(false);
        this.handleError(mode === 'create' ? '添加枚举值失败' : '更新枚举值失败', err);
      },
    });
  }

  protected async deleteEnum(item: EnumNode): Promise<void> {
    const column = this.selectedColumn();
    if (!column) {
      return;
    }

    const confirmed = await this.confirmDialog.confirm({
      title: '删除枚举值',
      message: `确定删除枚举值「${item.value}」？`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }

    this.api
      .deleteEnum(item.uuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          if (this.enumEditingUuid() === item.uuid) {
            this.cancelEnumForm();
          }
          this.loadEnums(column.uuid);
          this.snack.success('枚举值已删除');
        },
        error: (err) => this.handleError('删除枚举值失败', err),
      });
  }

  protected openGraphView(focusUuid: string): void {
    this.graphFocusUuid.set(focusUuid);
    this.detailView.set('graph');
    const depth = focusUuid === this.selectedColumn()?.uuid ? 1 : this.graphDepth();
    if (focusUuid === this.selectedColumn()?.uuid) {
      this.graphDepth.set(1);
    }
    this.loadGraph(focusUuid, { depth });
  }

  protected closeGraphView(): void {
    this.detailView.set('detail');
  }

  protected selectTableDetailTab(tab: TableDetailTab): void {
    this.tableDetailTab.set(tab);
  }

  protected onGraphDepthChange(depth: number): void {
    this.graphDepth.set(depth);
    const focus = this.graphFocusUuid();
    if (focus) {
      this.loadGraph(focus, { depth });
    }
  }

  protected refresh(): void {
    this.refreshing.set(true);
    if (this.refreshSpinTimer) {
      clearTimeout(this.refreshSpinTimer);
    }
    this.refreshSpinTimer = setTimeout(() => this.refreshing.set(false), 700);

    this.fetchTables(this.tableQuery());
    const table = this.selectedTable();
    if (table) {
      this.fetchColumns(table.uuid, this.columnQuery());
      this.loadTableOverview(table.uuid);
      if (this.detailView() === 'graph') {
        const focus = this.graphFocusUuid() ?? this.selectedColumn()?.uuid ?? table.uuid;
        this.loadGraph(focus, { depth: this.graphDepth() });
      }
    }
    const column = this.selectedColumn();
    if (column) {
      this.loadEnums(column.uuid);
    }
  }

  protected patchTableFormField(field: keyof Omit<TableFormDraft, 'joinRelations'>, value: string): void {
    this.tableFormDraft.update((draft) => ({ ...draft, [field]: value }));
  }

  protected addJoinRelation(): void {
    this.tableFormDraft.update((draft) => ({
      ...draft,
      joinRelations: [createJoinRelationDraft(), ...draft.joinRelations],
    }));
  }

  protected removeJoinRelation(relationId: string): void {
    this.tableFormDraft.update((draft) => ({
      ...draft,
      joinRelations: draft.joinRelations.filter((relation) => relation.id !== relationId),
    }));
  }

  protected patchJoinRelation(event: { id: string; patch: Partial<TableJoinDraft> }): void {
    this.tableFormDraft.update((draft) => ({
      ...draft,
      joinRelations: draft.joinRelations.map((relation) =>
        relation.id === event.id ? { ...relation, ...event.patch } : relation,
      ),
    }));
  }

  protected joinRelationPeerTooltip(meta: { peer: string; peerComment: string }): string {
    return formatJoinRelationPeerTooltip(meta.peer, meta.peerComment);
  }

  protected joinRelationLabel(tableName: string, relation: TableJoinRelation) {
    return joinRelationViewMeta(tableName, relation, (name) =>
      tableCommentForName(this.tables(), name),
    );
  }

  protected patchColumnFormField(field: keyof ColumnFormDraft, value: string): void {
    this.columnFormDraft.update((draft) => ({ ...draft, [field]: value }));
  }

  private resetForms(): void {
    this.tableFormMode.set('view');
    this.tableFormDraft.set(emptyTableForm());
    this.columnFormMode.set('view');
    this.columnFormDraft.set(emptyColumnForm());
    this.cancelEnumForm();
  }

  private fetchTables(q = ''): void {
    const requestId = this.tablesGuard.next();
    this.tablesLoading.set(true);
    this.api
      .listTables(listAllQuery({ q: q || undefined }))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          if (!this.tablesGuard.isLatest(requestId)) {
            return;
          }
          this.tables.set(page.items);
          this.tablesTotal.set(page.total);
          this.tablesLoading.set(false);
          this.syncSelectionAfterTableLoad();
        },
        error: (err) => {
          if (!this.tablesGuard.isLatest(requestId)) {
            return;
          }
          this.tablesLoading.set(false);
          this.handleError('加载表列表失败', err);
        },
      });
  }

  private fetchColumns(tableUuid: string, q = ''): void {
    const requestId = this.columnsGuard.next();
    this.columnsLoading.set(true);
    this.api
      .listColumns(listAllQuery({ table_uuid: tableUuid, q: q || undefined }))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          if (!this.columnsGuard.isLatest(requestId) || this.selectedTable()?.uuid !== tableUuid) {
            return;
          }
          this.columns.set(page.items);
          this.columnsTotal.set(page.total);
          this.columnsLoading.set(false);
          this.syncSelectionAfterColumnLoad();
        },
        error: (err) => {
          if (!this.columnsGuard.isLatest(requestId)) {
            return;
          }
          this.columnsLoading.set(false);
          this.handleError('加载列失败', err);
        },
      });
  }

  private loadTableJoinRelations(tableUuid: string): void {
    const requestId = this.joinRelationsGuard.next();
    this.joinRelationsLoading.set(true);
    this.api
      .listTableJoinRelations(tableUuid)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (relations) => {
          if (
            !this.joinRelationsGuard.isLatest(requestId) ||
            this.selectedTable()?.uuid !== tableUuid
          ) {
            return;
          }
          this.tableJoinRelations.set(relations);
          this.joinRelationsLoading.set(false);
        },
        error: (err) => {
          if (!this.joinRelationsGuard.isLatest(requestId)) {
            return;
          }
          this.joinRelationsLoading.set(false);
          this.tableJoinRelations.set([]);
          this.handleError('加载表间关系失败', err);
        },
      });
  }

  private loadTableOverview(tableUuid: string): void {
    const requestId = this.overviewGuard.next();
    this.api
      .getGraph(tableUuid, { depth: 1 })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (snapshot) => {
          if (!this.overviewGuard.isLatest(requestId) || this.selectedTable()?.uuid !== tableUuid) {
            return;
          }
          this.tableOverviewSnapshot.set(snapshot);
        },
        error: (err) => {
          if (!this.overviewGuard.isLatest(requestId)) {
            return;
          }
          this.tableOverviewSnapshot.set(null);
          this.handleError('加载表统计失败', err);
        },
      });
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

  private loadEnums(columnUuid: string): void {
    this.enumsLoading.set(true);
    this.api
      .listEnums(listAllQuery({ column_uuid: columnUuid }))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          this.enums.set(page.items);
          this.enumsLoading.set(false);
        },
        error: (err) => this.handleError('加载枚举失败', err),
      });
  }

  private syncSelectionAfterTableLoad(): void {
    const current = this.selectedTable();
    if (!current) {
      return;
    }
    const refreshed = this.tables().find((item) => item.uuid === current.uuid);
    if (refreshed) {
      this.selectedTable.set(refreshed);
    } else {
      this.selectedTable.set(null);
      this.selectedColumn.set(null);
      this.columns.set([]);
      this.columnsTotal.set(0);
      this.enums.set([]);
      this.detailView.set('detail');
          this.tableOverviewSnapshot.set(null);
          this.tableJoinRelations.set([]);
          this.graphSnapshot.set(null);
          this.resetForms();
    }
  }

  private applyTableUpdate(updated: TableNode, previousUuid?: string): void {
    const matchUuid = previousUuid ?? updated.uuid;
    this.selectedTable.set(updated);
    this.tables.update((items) =>
      items.map((item) => (item.uuid === matchUuid ? updated : item)),
    );
  }

  private syncSelectionAfterColumnLoad(): void {
    const current = this.selectedColumn();
    if (!current) {
      return;
    }
    const refreshed = this.columns().find((item) => item.uuid === current.uuid);
    if (refreshed) {
      this.selectedColumn.set(refreshed);
    } else {
      this.selectedColumn.set(null);
      this.enums.set([]);
    }
  }

  private handleError(message: string, err: unknown): void {
    this.tablesLoading.set(false);
    this.columnsLoading.set(false);
    this.enumsLoading.set(false);
    this.snack.error(`${message}: ${formatApiError(err)}`);
  }
}
