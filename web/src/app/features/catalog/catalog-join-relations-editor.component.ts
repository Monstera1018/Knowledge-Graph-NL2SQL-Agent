import { Component, HostListener, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

import { TableNode } from '../../core/models/metadata.models';
import { AppTruncateTooltipDirective } from '../../shared/directives/truncate-tooltip.directive';
import { TableJoinDraft } from './catalog.models';
import { tableCommentForName, tableOptionLabel } from './catalog.utils';

@Component({
  selector: 'app-catalog-join-relations-editor',
  imports: [FormsModule, MatIconModule, MatTooltipModule, AppTruncateTooltipDirective],
  templateUrl: './catalog-join-relations-editor.component.html',
  styleUrl: './catalog-join-relations-editor.component.css',
})
export class CatalogJoinRelationsEditorComponent {
  readonly joinRelations = input.required<TableJoinDraft[]>();
  readonly tables = input.required<TableNode[]>();
  readonly draftTableName = input('');
  readonly selectedTableName = input('');

  readonly addRelation = output<void>();
  readonly removeRelation = output<string>();
  readonly patchRelation = output<{ id: string; patch: Partial<TableJoinDraft> }>();

  protected readonly openPeerPickerId = signal<string | null>(null);
  protected readonly peerPickerQuery = signal('');

  protected readonly peerTables = computed(() => {
    const exclude = new Set(
      [this.draftTableName(), this.selectedTableName()]
        .map((name) => name.trim().toLowerCase())
        .filter((name) => name.length > 0),
    );
    return this.tables().filter((table) => !exclude.has(table.name.trim().toLowerCase()));
  });

  protected readonly filteredPeerTables = computed(() => {
    const keyword = this.peerPickerQuery().trim().toLowerCase();
    const tables = this.peerTables();
    if (!keyword) {
      return tables;
    }
    return tables.filter((table) => {
      const comment = tableCommentForName(this.tables(), table.name).toLowerCase();
      return table.name.toLowerCase().includes(keyword) || comment.includes(keyword);
    });
  });

  @HostListener('document:click')
  protected onDocumentClick(): void {
    this.closePeerPicker();
  }

  protected optionLabel(table: TableNode): string {
    return tableOptionLabel(table);
  }

  protected optionLabelByName(tableName: string): string {
    const table = this.tables().find(
      (item) => item.name.trim().toLowerCase() === tableName.trim().toLowerCase(),
    );
    return table ? tableOptionLabel(table) : tableName;
  }

  protected commentForName(tableName: string): string {
    return tableCommentForName(this.tables(), tableName);
  }

  protected togglePeerPicker(event: MouseEvent, relationId: string): void {
    event.stopPropagation();
    if (this.openPeerPickerId() === relationId) {
      this.closePeerPicker();
      return;
    }
    this.peerPickerQuery.set('');
    this.openPeerPickerId.set(relationId);
  }

  protected selectPeerTable(relationId: string, tableName: string): void {
    this.patchRelation.emit({ id: relationId, patch: { peer_table_name: tableName } });
    this.closePeerPicker();
  }

  protected closePeerPicker(): void {
    this.openPeerPickerId.set(null);
    this.peerPickerQuery.set('');
  }

  protected onAddRelation(): void {
    this.closePeerPicker();
    this.addRelation.emit();
  }

  protected onPatch(relationId: string, patch: Partial<TableJoinDraft>): void {
    this.patchRelation.emit({ id: relationId, patch });
  }
}
