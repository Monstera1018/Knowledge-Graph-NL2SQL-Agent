import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MetadataApiService } from '../../core/api/metadata-api.service';
import { createRequestGuard } from '../../core/api/request-guard';
import type { ImportStage } from '../../core/models/ingest.models';
import {
  IMPORT_STAGE_OPTIONS,
  jobStatusLabel,
  progressEntityLabel,
} from '../../core/models/ingest.models';
import { JobSnapshot, ProgressLevel } from '../../core/models/metadata.models';
import { formatApiError } from '../../shared/feedback/api-error';
import { AppSnackService } from '../../shared/feedback/app-snack.service';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';
import { AppTruncateTooltipDirective } from '../../shared/directives/truncate-tooltip.directive';

const ACCEPTED_EXTENSIONS = ['.xlsx', '.xls'];
const POLL_INTERVAL_MS = 1500;

type IngestDetailPanel = 'upload' | 'job';

@Component({
  selector: 'app-ingest-page',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    MatIconModule,
    MatProgressSpinnerModule,
    EmptyStateComponent,
    AppTruncateTooltipDirective,
  ],
  templateUrl: './ingest.page.html',
  styleUrl: './ingest.page.css',
})
export class IngestPage {
  private readonly api = inject(MetadataApiService);
  private readonly snack = inject(AppSnackService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly stageOptions = IMPORT_STAGE_OPTIONS;
  protected readonly jobs = signal<JobSnapshot[]>([]);
  protected readonly loading = signal(false);
  protected readonly refreshing = signal(false);
  protected readonly query = signal('');
  protected readonly selected = signal<JobSnapshot | null>(null);
  protected readonly detailPanel = signal<IngestDetailPanel>('upload');
  protected readonly detailLoading = signal(false);
  protected readonly uploadStage = signal<ImportStage>('IMPORT_TABLE_SCHEMA');
  protected readonly uploadFile = signal<File | null>(null);
  protected readonly uploadDragOver = signal(false);
  protected readonly uploading = signal(false);

  protected readonly filteredJobs = computed(() => {
    const keyword = this.query().trim().toLowerCase();
    const items = this.jobs();
    if (!keyword) {
      return items;
    }
    return items.filter((job) => {
      const haystack = [job.label, job.filename, job.message, job.error ?? '', jobStatusLabel(job.status)]
        .join(' ')
        .toLowerCase();
      return haystack.includes(keyword);
    });
  });

  protected readonly progressLevels = computed((): ProgressLevel[] => {
    return this.selected()?.progress?.levels ?? [];
  });

  /** 已结束或进行中的任务均只读，不可在详情里“改任务” */
  protected readonly selectedJobReadonly = computed(() => {
    const job = this.selected();
    return !!job;
  });

  protected readonly selectedJobReadonlyHint = computed(() => {
    const job = this.selected();
    if (!job) {
      return '';
    }
    if (job.status === 'Succeeded' || job.status === 'Failed') {
      return '任务已结束，仅可查看记录；如需再次导入请点击左侧 +';
    }
    return '任务进行中，仅可查看进度；如需导入其他文件请点击左侧 +';
  });

  protected readonly jobStatusLabel = jobStatusLabel;
  protected readonly progressEntityLabel = progressEntityLabel;

  private refreshSpinTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private readonly jobsGuard = createRequestGuard();
  private readonly jobDetailGuard = createRequestGuard();

  constructor() {
    this.loadJobs();
    this.destroyRef.onDestroy(() => this.stopPolling());
  }

  protected onQueryChange(value: string): void {
    this.query.set(value);
  }

  protected refresh(): void {
    this.refreshing.set(true);
    if (this.refreshSpinTimer) {
      clearTimeout(this.refreshSpinTimer);
    }
    this.refreshSpinTimer = setTimeout(() => this.refreshing.set(false), 700);
    this.loadJobs();
    const selected = this.selected();
    if (selected) {
      this.loadJobDetail(selected.job_id, { quiet: true });
    }
  }

  protected showNewUpload(): void {
    this.detailPanel.set('upload');
    this.selected.set(null);
    this.uploadFile.set(null);
    this.uploadDragOver.set(false);
  }

  protected selectJob(job: JobSnapshot): void {
    this.detailPanel.set('job');
    this.selected.set(job);
    this.loadJobDetail(job.job_id);
  }

  protected onStageChange(value: ImportStage): void {
    this.uploadStage.set(value);
  }

  protected onFileInputChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) {
      this.setUploadFile(file);
    }
    input.value = '';
  }

  protected onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.uploadDragOver.set(true);
  }

  protected onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.uploadDragOver.set(false);
  }

  protected onDrop(event: DragEvent): void {
    event.preventDefault();
    this.uploadDragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      this.setUploadFile(file);
    }
  }

  protected clearUploadFile(): void {
    this.uploadFile.set(null);
  }

  protected submitUpload(): void {
    if (this.uploading()) {
      return;
    }
    const file = this.uploadFile();
    if (!file) {
      this.snack.info('请先选择 Excel 文件');
      return;
    }
    if (!this.isAcceptedFile(file)) {
      this.snack.info('仅支持 .xlsx / .xls 文件');
      return;
    }

    this.uploading.set(true);
    this.api
      .createImportJob(this.uploadStage(), file)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ job_id }) => {
          this.uploading.set(false);
          this.uploadFile.set(null);
          this.snack.success('导入任务已创建');
          this.detailPanel.set('job');
          this.detailLoading.set(true);
          this.api
            .getImportJob(job_id)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
              next: (detail) => {
                this.detailLoading.set(false);
                if (detail) {
                  this.selected.set(detail);
                }
                this.loadJobs(job_id);
              },
              error: () => {
                this.detailLoading.set(false);
                this.loadJobs(job_id);
              },
            });
        },
        error: (err) => {
          this.uploading.set(false);
          this.handleError('创建导入任务失败', err);
        },
      });
  }

  protected statusClass(status: JobSnapshot['status']): string {
    switch (status) {
      case 'Running':
        return 'ingest-status--running';
      case 'Succeeded':
        return 'ingest-status--succeeded';
      case 'Failed':
        return 'ingest-status--failed';
      default:
        return 'ingest-status--pending';
    }
  }

  protected formatFileSize(bytes: number): string {
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  private setUploadFile(file: File): void {
    if (!this.isAcceptedFile(file)) {
      this.snack.info('仅支持 .xlsx / .xls 文件');
      return;
    }
    this.uploadFile.set(file);
  }

  private isAcceptedFile(file: File): boolean {
    const name = file.name.toLowerCase();
    return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
  }

  private loadJobs(selectJobId?: string): void {
    const requestId = this.jobsGuard.next();
    if (this.jobs().length === 0) {
      this.loading.set(true);
    }
    this.api
      .listImportJobs()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (items) => {
          if (!this.jobsGuard.isLatest(requestId)) {
            return;
          }
          this.jobs.set(items);
          this.loading.set(false);
          this.syncSelectionAfterLoad(selectJobId);
          this.syncPolling();
        },
        error: (err) => {
          if (!this.jobsGuard.isLatest(requestId)) {
            return;
          }
          this.loading.set(false);
          this.handleError('加载导入任务失败', err);
        },
      });
  }

  private syncSelectionAfterLoad(selectJobId?: string): void {
    if (selectJobId) {
      const job = this.jobs().find((item) => item.job_id === selectJobId);
      if (job) {
        this.selected.set(job);
        this.loadJobDetail(job.job_id, { quiet: true });
        return;
      }
    }

    const current = this.selected();
    if (!current) {
      return;
    }
    const refreshed = this.jobs().find((item) => item.job_id === current.job_id);
    if (refreshed) {
      this.selected.set(refreshed);
    } else {
      this.selected.set(null);
      this.detailPanel.set('upload');
    }
  }

  private loadJobDetail(jobId: string, options: { quiet?: boolean } = {}): void {
    const requestId = this.jobDetailGuard.next();
    if (!options.quiet) {
      this.detailLoading.set(true);
    }
    this.api
      .getImportJob(jobId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (detail) => {
          if (!this.jobDetailGuard.isLatest(requestId) || this.selected()?.job_id !== jobId) {
            return;
          }
          this.detailLoading.set(false);
          this.selected.set(detail);
          this.jobs.update((items) =>
            items.map((item) => (item.job_id === detail.job_id ? detail : item)),
          );
          this.syncPolling();
        },
        error: (err) => {
          if (!this.jobDetailGuard.isLatest(requestId)) {
            return;
          }
          this.detailLoading.set(false);
          if (!options.quiet) {
            this.handleError('加载任务详情失败', err);
          }
        },
      });
  }

  private syncPolling(): void {
    const hasActive = this.jobs().some(
      (job) => job.status === 'Pending' || job.status === 'Running',
    );
    if (hasActive) {
      this.startPolling();
    } else {
      this.stopPolling();
    }
  }

  private startPolling(): void {
    if (this.pollTimer) {
      return;
    }
    this.pollTimer = setInterval(() => {
      this.loadJobs();
      const selected = this.selected();
      if (
        selected &&
        (selected.status === 'Pending' || selected.status === 'Running')
      ) {
        this.loadJobDetail(selected.job_id, { quiet: true });
      }
    }, POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private handleError(message: string, err: unknown): void {
    this.snack.error(`${message}: ${formatApiError(err)}`);
  }
}
