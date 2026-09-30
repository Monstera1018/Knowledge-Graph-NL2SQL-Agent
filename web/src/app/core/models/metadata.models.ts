export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface BatchActionResult {
  ok: number;
  skipped: number;
  failed: number;
}

export type BatchAction = 'approve' | 'enable' | 'disable' | 'delete';

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
  source?: 'import' | 'llm' | string;
  enabled?: boolean;
  review_status?: 'pending' | 'approved' | string;
  source_sql_uuid?: string;
  created_at?: string;
  updated_at?: string;
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
  review_status?: 'pending' | 'approved' | string;
  knowledge_extracted?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface SQLDetail extends SQLNode {
  relations: KnowledgeRelation[];
}

export interface SQLPrecipitateResult {
  action: 'created' | 'updated' | 'merged' | 'skipped' | 'empty' | string;
  uuid: string;
  name: string;
  source: string;
  review_status: string;
  enabled: boolean;
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
