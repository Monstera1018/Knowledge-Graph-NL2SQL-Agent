export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface BaseNode {
  uuid: string;
  workspace_id: string;
  type: string;
}

export interface TableNode extends BaseNode {
  name: string;
  comment: string;
  description: string;
}

export interface TableJoinRelation {
  from_table_uuid: string;
  from_table_name: string;
  to_table_uuid: string;
  to_table_name: string;
  condition: string;
}

export interface ColumnNode extends BaseNode {
  name: string;
  comment: string;
  dtype: string;
}

export interface EnumNode extends BaseNode {
  value: string;
}

export interface EnumListItem extends EnumNode {
  table_uuid: string;
  table_name: string;
  table_comment: string;
  column_uuid: string;
  column_name: string;
  column_comment: string;
  column_dtype: string;
}

export interface KnowledgeNode extends BaseNode {
  name: string;
  description: string;
}

export interface KnowledgeRelation {
  table_name: string;
  column_names: string[];
}

export interface KnowledgeDetail extends KnowledgeNode {
  relations: KnowledgeRelation[];
}

export interface SQLNode extends BaseNode {
  name: string;
  logic: string;
  content: string;
  dialect: string;
  source?: 'import' | 'llm' | string;
  enabled?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface SQLDetail extends SQLNode {
  relations: KnowledgeRelation[];
}

export type {
  GraphEdgeType,
  GraphEdgeView,
  GraphNodeType,
  GraphNodeView,
  GraphSnapshot,
} from './graph.models';

export type JobStatus = 'Pending' | 'Running' | 'Succeeded' | 'Failed';

export interface ProgressLevel {
  entity: string;
  name: string;
  current: number;
  total: number;
}

export interface ImportProgressEvent {
  stage: string;
  scope: string;
  levels: ProgressLevel[];
  overall_current: number;
  overall_total: number;
  percent: number;
  message: string;
}

export interface JobSnapshot {
  job_id: string;
  workspace_id: string;
  type: string;
  label: string;
  filename: string;
  status: JobStatus;
  percent: number;
  message: string;
  progress?: ImportProgressEvent | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
}

export interface JobCreatedResponse {
  job_id: string;
}
