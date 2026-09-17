import { JsonPipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';

import { ChatApiService } from '../../../core/api/chat-api.service';
import type { DataPart } from '../../../core/models/chat.models';
import {
  parseSqlResultSets,
  previewSqlResultRows,
  sqlResultHasMore,
  type SqlResultSet,
} from '../chat-sql-result';
import { ChatChartComponent } from './chat-chart.component';
import { ChatSqlResultDialogComponent } from './chat-sql-result-dialog.component';
import { parseChartSpec, parseChartView, type ChatChartView } from '../chat-chart';

type SelectedTableDetail = {
  name: string;
  comment?: string;
  description?: string;
};

@Component({
  selector: 'app-chat-data-part',
  imports: [JsonPipe, MatIconModule, ChatChartComponent],
  templateUrl: './chat-data-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatDataPartComponent {
  private readonly dialog = inject(MatDialog);
  private readonly chatApi = inject(ChatApiService);

  readonly part = input.required<DataPart>();

  protected readonly sqlResults = computed(() => parseSqlResultSets(this.part().payload));
  protected readonly chartSpec = computed(() => parseChartSpec(this.part().payload));

  protected sqlResultSets(): SqlResultSet[] {
    return this.sqlResults();
  }

  protected previewRows(result: SqlResultSet): Array<Record<string, string>> {
    return previewSqlResultRows(result.rows);
  }

  protected hasMoreRows(result: SqlResultSet): boolean {
    return sqlResultHasMore(result.rows.length);
  }

  protected openFullResult(result: SqlResultSet, index: number): void {
    const sets = this.sqlResults();
    this.dialog.open(ChatSqlResultDialogComponent, {
      data: {
        columns: result.columns,
        rows: result.rows,
        setIndex: index,
        setCount: sets.length,
      },
      panelClass: 'app-sql-result-dialog-panel',
      backdropClass: 'app-dialog-backdrop',
      autoFocus: 'first-tabbable',
      restoreFocus: true,
      width: 'min(72rem, calc(100vw - 2rem))',
      maxWidth: 'calc(100vw - 2rem)',
      maxHeight: '86vh',
    });
  }

  protected sqlResultMessage(): string {
    const value = this.part().payload['message'];
    return typeof value === 'string' ? value : '';
  }

  protected sqlResultRowCount(): number {
    return this.sqlResults().reduce((total, result) => total + result.rows.length, 0);
  }

  protected rows(): Array<{ name: string; comment?: string; description?: string }> {
    const tables = this.part().payload['tables'];
    if (!Array.isArray(tables)) {
      return [];
    }
    return tables.filter(
      (row): row is { name: string; comment?: string; description?: string } =>
        !!row && typeof row === 'object' && typeof (row as { name?: unknown }).name === 'string',
    );
  }

  protected genericKind(): string {
    const payload = this.part().payload;
    const kind = payload['kind'];
    return typeof kind === 'string' ? kind : '';
  }

  protected values(key: string): string[] {
    const value = this.part().payload[key];
    if (!Array.isArray(value)) {
      return [];
    }
    return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
  }

  /** Backend may emit ``tables`` / ``column_hints`` before stream normalization. */
  protected selectedTables(): string[] {
    const direct = this.values('selected_tables');
    if (direct.length) {
      return direct;
    }
    return this.values('tables');
  }

  protected selectedTableDetails(): SelectedTableDetail[] {
    const details = this.part().payload['selected_table_details'];
    if (Array.isArray(details)) {
      const normalized = details
        .map((item): SelectedTableDetail | null => {
          if (!item || typeof item !== 'object') {
            return null;
          }
          const record = item as Record<string, unknown>;
          const name = record['name'];
          if (typeof name !== 'string' || !name.trim()) {
            return null;
          }
          const comment = record['comment'];
          const description = record['description'];
          return {
            name,
            comment: typeof comment === 'string' ? comment : '',
            description: typeof description === 'string' ? description : '',
          };
        })
        .filter((item): item is SelectedTableDetail => !!item);
      if (normalized.length) {
        return normalized;
      }
    }
    return this.selectedTables().map((name) => ({ name }));
  }

  protected selectedColumns(): string[] {
    const direct = this.values('selected_columns');
    if (direct.length) {
      return direct;
    }
    return this.values('column_hints');
  }

  protected selectedEnums(): string[] {
    const direct = this.values('selected_enums');
    if (direct.length) {
      return direct;
    }
    return this.values('enum_hints');
  }

  protected selectedKnowledges(): string[] {
    return this.values('knowledges');
  }

  protected text(key: string): string {
    const value = this.part().payload[key];
    return typeof value === 'string' ? value : '';
  }

  protected retryCount(): number {
    const value = this.part().payload['retry_count'];
    return typeof value === 'number' ? value : 0;
  }

  protected isRepairDiagnostic(): boolean {
    const action = String(this.part().payload['recovery_action'] ?? '')
      .trim()
      .toLowerCase();
    return action === 'repair';
  }

  protected numberValue(key: string): number | null {
    const value = this.part().payload[key];
    return typeof value === 'number' ? value : null;
  }

  protected isCompactPlan(): boolean {
    return this.part().schema === 'generic' && this.genericKind() === 'schema_plan';
  }

  protected fallbackTitle(): string {
    if (this.part().title) {
      return this.part().title;
    }
    const kind = this.genericKind();
    const labels: Record<string, string> = {
      retrieval_context: '检索上下文',
      schema_context: '表结构上下文',
      schema_plan: '选表结果',
      sql_diagnostic: 'SQL 校验说明',
      sql_result: '查询结果',
      chart_spec: '分析图表',
      analysis_report: '分析报告',
    };
    return labels[kind] || '结构化信息';
  }

  protected fallbackSummary(): string {
    const kind = this.genericKind();
    if (kind === 'retrieval_context') {
      return '系统已找到与问题相关的业务资料，后续会用这些资料辅助选表和生成 SQL。';
    }
    if (kind === 'schema_context') {
      return '系统已整理本次查询可使用的表、字段和关联关系。';
    }
    if (this.part().schema === 'column_list') {
      return '系统返回了相关字段清单，可用于确认查询口径和输出列。';
    }
    if (this.part().schema === 'join_relations') {
      return '系统返回了表之间的关联关系，可用于多表查询时确定连接方式。';
    }
    if (this.part().schema === 'graph_snapshot') {
      return '系统返回了知识图谱片段，可用于理解表、字段和值之间的业务关系。';
    }
    return '系统返回了一段结构化信息，默认展示为业务摘要；需要排查时可展开技术详情查看原始数据。';
  }

  protected fallbackItems(): string[] {
    const payload = this.part().payload;
    const items: string[] = [];
    const tables = this.values('tables').length || this.values('selected_tables').length;
    const columns = this.values('columns').length || this.values('column_hints').length;
    const enums = this.values('enums').length || this.values('enum_hints').length;
    const knowledges = this.values('knowledges').length;

    if (tables) {
      items.push(`相关数据表：${tables} 个`);
    }
    if (columns) {
      items.push(`相关字段：${columns} 个`);
    }
    if (enums) {
      items.push(`相关枚举值：${enums} 个`);
    }
    if (knowledges) {
      items.push(`相关业务知识：${knowledges} 条`);
    }

    const keys = Object.keys(payload).filter((key) => key !== 'kind');
    if (!items.length && keys.length) {
      items.push(`包含信息项：${keys.slice(0, 6).join('、')}`);
    }
    return items;
  }

  protected reportTitle(): string {
    return this.text('title') || '数据分析报告';
  }

  protected reportSummary(): string {
    return this.text('summary');
  }

  protected reportOverview(): string {
    return this.text('overview');
  }

  protected reportConclusions(): string {
    return this.text('conclusions');
  }

  protected reportFeasible(): boolean {
    return this.part().payload['feasible'] === true;
  }

  protected reportDownloadable(): boolean {
    return this.reportFeasible() && Boolean(this.text('file_id'));
  }

  protected reportAnalyses(): Array<{ heading: string; narrative: string; chart: ChatChartView | null }> {
    const raw = this.part().payload['analyses'];
    if (!Array.isArray(raw)) {
      return [];
    }
    return raw
      .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
      .map((item) => ({
        heading: typeof item['heading'] === 'string' ? item['heading'] : '分析维度',
        narrative: typeof item['narrative'] === 'string' ? item['narrative'] : '',
        chart: parseChartView(item['chart']),
      }));
  }

  protected downloadReport(): void {
    const fileId = this.text('file_id');
    if (!fileId) {
      return;
    }
    const filename = this.text('filename') || '分析报告.docx';
    this.chatApi.downloadReport(fileId, filename).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
      },
    });
  }
}
