import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subject, catchError, debounceTime, distinctUntilChanged, of, switchMap } from 'rxjs';

import { LIST_ALL_PAGE_SIZE, listAllQuery } from '../../core/api/list-query';
import { MetadataApiService } from '../../core/api/metadata-api.service';
import { WorkspaceService } from '../../core/workspace/workspace.service';
import { AppTruncateTooltipDirective } from '../../shared/directives/truncate-tooltip.directive';
import { ColumnNode, TableNode } from '../../core/models/metadata.models';
import {
  KnowledgeRelationDialogData,
  KnowledgeRelationDialogResult,
} from './knowledge-relation-dialog.models';

@Component({
  selector: 'app-knowledge-relation-dialog',
  imports: [
    FormsModule,
    MatDialogModule,
    MatIconModule,
    MatProgressSpinnerModule,
    AppTruncateTooltipDirective,
  ],
  templateUrl: './knowledge-relation-dialog.component.html',
  styleUrl: './knowledge-relation-dialog.component.css',
})
export class KnowledgeRelationDialogComponent implements OnInit {
  private readonly workspace = inject(WorkspaceService);
  private readonly api = inject(MetadataApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly dialogRef = inject(MatDialogRef<KnowledgeRelationDialogComponent, KnowledgeRelationDialogResult>);
  protected readonly data = inject<KnowledgeRelationDialogData>(MAT_DIALOG_DATA);

  protected readonly tableQuery = signal('');
  protected readonly tables = signal<TableNode[]>([]);
  protected readonly tablesTotal = signal(0);
  protected readonly tablesLoading = signal(false);
  protected readonly selectedTable = signal<TableNode | null>(null);
  protected readonly columns = signal<ColumnNode[]>([]);
  protected readonly columnsLoading = signal(false);
  protected readonly wholeTable = signal(false);
  protected readonly selectedColumns = signal<Set<string>>(new Set());

  private readonly tableSearch$ = new Subject<string>();

  protected readonly dialogTitle = this.data.mode === 'edit' ? '编辑关联' : '添加关联表与列';

  protected readonly submitLabel = this.data.mode === 'edit' ? '保存' : '添加';

  ngOnInit(): void {
    this.tableSearch$
      .pipe(
        debounceTime(300),
        distinctUntilChanged(),
        switchMap((q) => {
          this.tablesLoading.set(true);
          const keyword = q.trim();
          return this.api
            .listTables(listAllQuery({ q: keyword || undefined }))
            .pipe(
              catchError(() =>
                of({ items: [] as TableNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE }),
              ),
            );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((page) => {
        this.tables.set(this.visibleTables(page.items));
        this.tablesTotal.set(page.total);
        this.tablesLoading.set(false);
      });

    this.tableSearch$.next('');

    if (this.data.initial) {
      this.wholeTable.set(this.data.initial.column_names.length === 0);
      this.selectedColumns.set(new Set(this.data.initial.column_names));
      this.api
        .listTables(listAllQuery({ q: this.data.initial.table_name }))
        .pipe(
          catchError(() =>
            of({ items: [] as TableNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE }),
          ),
          takeUntilDestroyed(this.destroyRef),
        )
        .subscribe((page) => {
          const match = page.items.find(
            (item) => this.tableKey(item.name) === this.tableKey(this.data.initial!.table_name),
          );
          const table: TableNode = match ?? {
            uuid: '',
            workspace_id: this.workspace.currentWorkspaceId(),
            type: 'TABLE',
            name: this.data.initial!.table_name,
            comment: '',
            description: '',
          };
          this.selectedTable.set(table);
          this.loadColumnsForTable(table.name, table.uuid || undefined);
        });
    }
  }

  protected onTableQueryChange(value: string): void {
    this.tableQuery.set(value);
    this.tableSearch$.next(value);
  }

  protected selectTable(table: TableNode): void {
    this.selectedTable.set(table);
    this.wholeTable.set(false);
    this.selectedColumns.set(new Set());
    this.loadColumnsForTable(table.name, table.uuid);
  }

  protected toggleWholeTable(checked: boolean): void {
    this.wholeTable.set(checked);
    if (checked) {
      this.selectedColumns.set(new Set());
    }
  }

  protected isColumnChecked(name: string): boolean {
    return this.selectedColumns().has(name);
  }

  protected toggleColumn(name: string, checked: boolean): void {
    if (this.wholeTable()) {
      return;
    }
    const next = new Set(this.selectedColumns());
    if (checked) {
      next.add(name);
    } else {
      next.delete(name);
    }
    this.selectedColumns.set(next);
  }

  protected selectAllColumns(): void {
    if (this.wholeTable()) {
      return;
    }
    this.selectedColumns.set(new Set(this.columns().map((column) => column.name)));
  }

  protected clearColumns(): void {
    this.selectedColumns.set(new Set());
  }

  protected cancel(): void {
    this.dialogRef.close(undefined);
  }

  protected confirm(): void {
    const table = this.selectedTable();
    if (!table) {
      return;
    }
    const columnNames = this.wholeTable()
      ? []
      : [...this.selectedColumns()].sort();
    this.dialogRef.close({
      table_name: table.name,
      column_names: columnNames,
    });
  }

  protected canSubmit(): boolean {
    return this.selectedTable() !== null;
  }

  private visibleTables(items: TableNode[]): TableNode[] {
    const blocked = new Set(this.data.blockedTableKeys);
    return items.filter((table) => !blocked.has(this.tableKey(table.name)));
  }

  private tableKey(name: string): string {
    return name.trim().toLowerCase();
  }

  private loadColumnsForTable(tableName: string, tableUuid?: string): void {
    this.columnsLoading.set(true);
    this.columns.set([]);

    const resolveUuid = (): Promise<string | null> => {
      if (tableUuid) {
        return Promise.resolve(tableUuid);
      }
      return new Promise((resolve) => {
        this.api
          .listTables(listAllQuery({ q: tableName }))
          .pipe(
            catchError(() =>
              of({ items: [] as TableNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE }),
            ),
          )
          .subscribe((page) => {
            const match = page.items.find((item) => this.tableKey(item.name) === this.tableKey(tableName));
            resolve(match?.uuid ?? null);
          });
      });
    };

    resolveUuid().then((uuid) => {
      if (!uuid) {
        this.columnsLoading.set(false);
        return;
      }
      this.fetchAllColumns(uuid, 1, []);
    });
  }

  private fetchAllColumns(tableUuid: string, page: number, acc: ColumnNode[]): void {
    this.api
      .listColumns({ ...listAllQuery({ table_uuid: tableUuid }), page })
      .pipe(
        catchError(() =>
          of({ items: [] as ColumnNode[], total: 0, page: 1, page_size: LIST_ALL_PAGE_SIZE }),
        ),
      )
      .subscribe({
        next: (res) => {
          const merged = [...acc, ...res.items];
          const done = merged.length >= res.total || res.items.length === 0;
          if (done) {
            this.columns.set(merged);
            this.columnsLoading.set(false);
          } else {
            this.fetchAllColumns(tableUuid, page + 1, merged);
          }
        },
        error: () => {
          this.columnsLoading.set(false);
        },
      });
  }
}
