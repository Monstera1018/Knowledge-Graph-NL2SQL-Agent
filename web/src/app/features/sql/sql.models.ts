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

export const sqlSourceLabel = (item: Pick<SQLNode, 'source'> | null | undefined): string => {
  return item?.source === 'llm' ? 'LLM生成' : '人工导入';
};

export const sqlIsEnabled = (item: Pick<SQLNode, 'enabled'> | null | undefined): boolean => {
  return item?.enabled !== false;
};

export const sqlTimeLabel = (
  item: Pick<SQLNode, 'updated_at' | 'created_at'> | null | undefined,
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

export const sqlToDraft = (item: SQLNode): SqlFormDraft => ({
  name: item.name,
  logic: item.logic ?? '',
  content: item.content ?? '',
  dialect: item.dialect ?? '',
});
