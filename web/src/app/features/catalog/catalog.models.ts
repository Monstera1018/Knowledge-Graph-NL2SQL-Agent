import type { TableJoinRelation } from '../../core/models/metadata.models';

export type CatalogFormMode = 'view' | 'create' | 'edit';

export type TableJoinDirection = 'outgoing' | 'incoming';

export interface TableJoinDraft {
  id: string;
  peer_table_name: string;
  direction: TableJoinDirection;
  condition: string;
}

export const createJoinRelationDraft = (): TableJoinDraft => ({
  id: globalThis.crypto?.randomUUID?.() ?? `join-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
  peer_table_name: '',
  direction: 'outgoing',
  condition: '',
});

export interface TableFormDraft {
  name: string;
  comment: string;
  description: string;
  joinRelations: TableJoinDraft[];
}

export interface ColumnFormDraft {
  name: string;
  comment: string;
  dtype: string;
}

export const emptyTableForm = (): TableFormDraft => ({
  name: '',
  comment: '',
  description: '',
  joinRelations: [],
});

export const emptyColumnForm = (): ColumnFormDraft => ({
  name: '',
  comment: '',
  dtype: '',
});

export const joinRelationToDraft = (
  currentTableName: string,
  relation: TableJoinRelation,
): TableJoinDraft => {
  const current = currentTableName.trim().toLowerCase();
  const from = relation.from_table_name.trim().toLowerCase();
  if (from === current) {
    return {
      ...createJoinRelationDraft(),
      peer_table_name: relation.to_table_name,
      direction: 'outgoing',
      condition: relation.condition ?? '',
    };
  }
  return {
    ...createJoinRelationDraft(),
    peer_table_name: relation.from_table_name,
    direction: 'incoming',
    condition: relation.condition ?? '',
  };
};

export const joinDraftToApi = (
  currentTableName: string,
  draft: TableJoinDraft,
): Pick<TableJoinRelation, 'from_table_name' | 'to_table_name' | 'condition'> => {
  const current = currentTableName.trim().toLowerCase();
  const peer = draft.peer_table_name.trim().toLowerCase();
  if (draft.direction === 'outgoing') {
    return {
      from_table_name: current,
      to_table_name: peer,
      condition: draft.condition.trim(),
    };
  }
  return {
    from_table_name: peer,
    to_table_name: current,
    condition: draft.condition.trim(),
  };
};

export const serializeJoinRelations = (
  currentTableName: string,
  drafts: TableJoinDraft[],
): Pick<TableJoinRelation, 'from_table_name' | 'to_table_name' | 'condition'>[] =>
  drafts
    .filter((draft) => draft.peer_table_name.trim())
    .map((draft) => joinDraftToApi(currentTableName, draft));

export const countRelatedTables = (
  currentTableName: string,
  relations: TableJoinRelation[],
): number => {
  const current = currentTableName.trim().toLowerCase();
  const peers = new Set<string>();
  for (const relation of relations) {
    const from = relation.from_table_name.trim().toLowerCase();
    const to = relation.to_table_name.trim().toLowerCase();
    if (from === current) {
      peers.add(to);
    } else if (to === current) {
      peers.add(from);
    }
  }
  return peers.size;
};
