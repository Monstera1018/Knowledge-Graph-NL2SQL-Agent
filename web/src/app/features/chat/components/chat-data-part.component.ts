import { JsonPipe } from '@angular/common';
import { Component, input } from '@angular/core';

import type { DataPart } from '../../../core/models/chat.models';

type SelectedTableDetail = {
  name: string;
  comment?: string;
  description?: string;
};

@Component({
  selector: 'app-chat-data-part',
  imports: [JsonPipe],
  templateUrl: './chat-data-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatDataPartComponent {
  readonly part = input.required<DataPart>();

  protected sqlResultSets(): Array<{ columns: string[]; rows: Array<Record<string, string>> }> {
    const sets = this.part().payload['result_sets'];
    if (!Array.isArray(sets)) {
      return [];
    }
    return sets
      .map((item) => {
        if (!item || typeof item !== 'object') {
          return null;
        }
        const columnsRaw = (item as Record<string, unknown>)['columns'];
        const rowsRaw = (item as Record<string, unknown>)['rows'];
        if (!Array.isArray(columnsRaw) || !Array.isArray(rowsRaw)) {
          return null;
        }
        const columns = columnsRaw.filter((c): c is string => typeof c === 'string');
        const rows = rowsRaw.filter((r): r is Record<string, string> => !!r && typeof r === 'object');
        return { columns, rows };
      })
      .filter((item): item is { columns: string[]; rows: Array<Record<string, string>> } => !!item);
  }

  protected sqlResultMessage(): string {
    const value = this.part().payload['message'];
    return typeof value === 'string' ? value : '';
  }

  protected sqlResultRowCount(): number {
    return this.sqlResultSets().reduce((total, result) => total + result.rows.length, 0);
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
}
