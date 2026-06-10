import { SQLNode } from '../../core/models/metadata.models';

export type SqlFormMode = 'view' | 'create' | 'edit';

export interface SqlFormDraft {
  name: string;
  logic: string;
  content: string;
  dialect: string;
}

export const emptySqlForm = (): SqlFormDraft => ({
  name: '',
  logic: '',
  content: '',
  dialect: '',
});

export const sqlToDraft = (item: SQLNode): SqlFormDraft => ({
  name: item.name,
  logic: item.logic ?? '',
  content: item.content ?? '',
  dialect: item.dialect ?? '',
});
