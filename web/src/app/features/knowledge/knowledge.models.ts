import { GraphSnapshot } from '../../core/models/metadata.models';

export type KnowledgeFormMode = 'view' | 'create' | 'edit';

export interface KnowledgeRelationDraft {
  table_name: string;
  column_names: string[];
}

export interface KnowledgeFormDraft {
  name: string;
  description: string;
  relations: KnowledgeRelationDraft[];
}

export const emptyKnowledgeForm = (): KnowledgeFormDraft => ({
  name: '',
  description: '',
  relations: [],
});

export const knowledgeSourceLabel = (
  item: { source?: string } | null | undefined,
): string => {
  return item?.source === 'llm' ? 'LLM抽取' : '人工导入';
};

export const knowledgeIsEnabled = (item: { enabled?: boolean } | null | undefined): boolean => {
  return item?.enabled !== false;
};

export const knowledgeIsPending = (
  item: { review_status?: string; source?: string; enabled?: boolean } | null | undefined,
): boolean => {
  if (!item) {
    return false;
  }
  if (item.review_status === 'pending') {
    return true;
  }
  if (item.review_status === 'approved') {
    return false;
  }
  return item.source === 'llm' && item.enabled === false;
};

export const knowledgeReviewLabel = (
  item: { review_status?: string; source?: string; enabled?: boolean } | null | undefined,
): string => {
  return knowledgeIsPending(item) ? '待审核' : '已审核';
};

export const knowledgeTimeLabel = (
  item: { updated_at?: string; created_at?: string } | null | undefined,
): string => {
  const raw = item?.updated_at || item?.created_at || '';
  if (!raw) {
    return '';
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export const cloneRelations = (relations: KnowledgeRelationDraft[] | undefined | null): KnowledgeRelationDraft[] =>
  (relations ?? []).map((relation) => ({
    table_name: relation.table_name,
    column_names: [...relation.column_names],
  }));

export const relationsFromGraph = (snapshot: GraphSnapshot | null): KnowledgeRelationDraft[] => {
  if (!snapshot) {
    return [];
  }

  const focusUuid = snapshot.focus_uuid;
  const nodeByUuid = new Map(snapshot.nodes.map((node) => [node.uuid, node]));
  const tableColumns = new Map<string, Set<string>>();

  for (const edge of snapshot.edges) {
    if (edge.type !== 'DESCRIBES' || edge.source !== focusUuid) {
      continue;
    }
    const target = nodeByUuid.get(edge.target);
    if (!target) {
      continue;
    }
    if (target.type === 'TABLE') {
      if (!tableColumns.has(target.label)) {
        tableColumns.set(target.label, new Set());
      }
      continue;
    }
    if (target.type === 'COLUMN') {
      const parentEdge = snapshot.edges.find(
        (item) => item.type === 'HAS' && item.target === target.uuid,
      );
      const table = parentEdge ? nodeByUuid.get(parentEdge.source) : undefined;
      if (table?.type !== 'TABLE') {
        continue;
      }
      if (!tableColumns.has(table.label)) {
        tableColumns.set(table.label, new Set());
      }
      tableColumns.get(table.label)!.add(target.label);
    }
  }

  return [...tableColumns.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([table_name, columnNames]) => ({
      table_name,
      column_names: [...columnNames].sort(),
    }));
};

export const relationStatsFromRelations = (relations: KnowledgeRelationDraft[]) => ({
  tables: relations.length,
  columns: relations.reduce((sum, relation) => sum + relation.column_names.length, 0),
});
