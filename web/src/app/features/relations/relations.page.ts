import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { catchError, debounceTime, of, Subject, switchMap } from 'rxjs';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { MetadataApiService } from '../../core/api/metadata-api.service';
import type { TableJoinRelation } from '../../core/models/metadata.models';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';

@Component({
  selector: 'app-relations-page',
  imports: [FormsModule, MatIconModule, MatProgressSpinnerModule, EmptyStateComponent],
  templateUrl: './relations.page.html',
  styleUrl: './relations.page.css',
})
export class RelationsPage {
  protected readonly pageSizeOptions = [10, 20, 30, 50, 100];
  protected readonly pageSize = signal(50);
  private readonly api = inject(MetadataApiService);
  private readonly snack = inject(AppSnackService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly search$ = new Subject<string>();

  protected readonly items = signal<TableJoinRelation[]>([]);
  protected readonly total = signal(0);
  protected readonly page = signal(1);
  protected readonly query = signal('');
  protected readonly loading = signal(false);
  protected readonly totalPages = signal(1);

  constructor() {
    this.search$
      .pipe(
        debounceTime(300),
        switchMap((q) => this.loadRelations(q)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();

    this.search$.next('');
  }

  protected onQueryChange(value: string): void {
    this.query.set(value);
    this.page.set(1);
    this.search$.next(value);
  }

  protected refresh(): void {
    this.search$.next(this.query());
  }

  protected onPageSizeChange(value: number): void {
    this.pageSize.set(Number(value));
    this.page.set(1);
    this.search$.next(this.query());
  }

  protected prevPage(): void {
    if (this.page() <= 1 || this.loading()) {
      return;
    }
    this.page.update((value) => value - 1);
    this.search$.next(this.query());
  }

  protected nextPage(): void {
    if (this.page() >= this.totalPages() || this.loading()) {
      return;
    }
    this.page.update((value) => value + 1);
    this.search$.next(this.query());
  }

  private loadRelations(q = '') {
    this.loading.set(true);
    return this.api
      .listJoinRelations({
        page: this.page(),
        page_size: this.pageSize(),
        q: q || undefined,
      })
      .pipe(
        catchError((err) => {
          this.snack.error(`加载表关系失败：${formatApiError(err)}`);
          return of({
            items: [] as TableJoinRelation[],
            total: 0,
            page: 1,
            page_size: this.pageSize(),
          });
        }),
        switchMap((page) => {
          this.items.set(page.items);
          this.total.set(page.total);
          this.page.set(page.page);
          this.totalPages.set(Math.max(1, Math.ceil(page.total / page.page_size)));
          this.loading.set(false);
          return of(page);
        }),
      );
  }
}
