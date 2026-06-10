import type { JobStatus } from './metadata.models';

export type ImportStage =
  | 'IMPORT_TABLE_SCHEMA'
  | 'IMPORT_ENUM_VALUE'
  | 'IMPORT_KNOWLEDGE'
  | 'IMPORT_SQL'
  | 'IMPORT_TABLE_RELATION';

export interface ImportStageOption {
  value: ImportStage;
  label: string;
  hint: string;
  icon: string;
}

export const IMPORT_STAGE_OPTIONS: ImportStageOption[] = [
  {
    value: 'IMPORT_TABLE_SCHEMA',
    label: '表结构',
    hint: '表名、列名、类型与描述',
    icon: 'table_chart',
  },
  {
    value: 'IMPORT_ENUM_VALUE',
    label: '枚举值',
    hint: '表、列与枚举取值',
    icon: 'list_alt',
  },
  {
    value: 'IMPORT_KNOWLEDGE',
    label: '业务知识',
    hint: '知识条目及关联表列',
    icon: 'lightbulb',
  },
  {
    value: 'IMPORT_SQL',
    label: '历史 SQL',
    hint: 'SQL 名称、逻辑、内容与方言',
    icon: 'code',
  },
  {
    value: 'IMPORT_TABLE_RELATION',
    label: '表关系',
    hint: '主从表及关联条件',
    icon: 'account_tree',
  },
];

const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  Pending: '等待中',
  Running: '进行中',
  Succeeded: '已完成',
  Failed: '失败',
};

const ENTITY_LABELS: Record<string, string> = {
  Table: '表',
  Column: '列',
  Enum: '枚举',
  Knowledge: '业务知识',
  SQL: 'SQL',
  Relation: '表关系',
};

export function jobStatusLabel(status: JobStatus): string {
  return JOB_STATUS_LABELS[status] ?? status;
}

export function progressEntityLabel(entity: string): string {
  return ENTITY_LABELS[entity] ?? entity;
}
