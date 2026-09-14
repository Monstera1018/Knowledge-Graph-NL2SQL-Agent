/** Mirrors server/chat/protocol.py (v1). */

export type StreamEventType =
  | 'turn_start'
  | 'turn_end'
  | 'part_start'
  | 'part_delta'
  | 'part_end'
  | 'error';

export type PartKind = 'text' | 'workflow' | 'tool' | 'data' | 'artifact' | 'error';

export type WorkflowStatus = 'pending' | 'running' | 'done' | 'error';

export type ToolPhase = 'call' | 'result';

export type DataSchema =
  | 'table_list'
  | 'table_detail'
  | 'column_list'
  | 'join_relations'
  | 'graph_snapshot'
  | 'generic';

export interface AgentDescriptor {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
}

export interface AgentListResponse {
  agents: AgentDescriptor[];
}

export interface SendMessageRequest {
  agent_id: string;
  content: string;
}

export interface TextPart {
  kind: 'text';
  id: string;
  text: string;
}

export interface WorkflowPart {
  kind: 'workflow';
  id: string;
  title: string;
  status: WorkflowStatus;
  detail: string;
}

export interface ToolPart {
  kind: 'tool';
  id: string;
  name: string;
  phase: ToolPhase;
  title: string;
  input?: Record<string, unknown> | null;
  output?: unknown;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
}

export interface DataPart {
  kind: 'data';
  id: string;
  schema: DataSchema;
  title: string;
  payload: Record<string, unknown>;
  truncated: boolean;
}

export interface ArtifactPart {
  kind: 'artifact';
  id: string;
  title: string;
  language: string;
  content: string;
}

export interface ErrorPart {
  kind: 'error';
  id: string;
  message: string;
  code: string;
  recoverable: boolean;
}

export type MessagePart =
  | TextPart
  | WorkflowPart
  | ToolPart
  | DataPart
  | ArtifactPart
  | ErrorPart;

export interface PartPatch {
  op: 'text_append' | 'field_set' | 'payload_merge';
  text?: string;
  path?: string;
  value?: unknown;
  payload?: Record<string, unknown>;
}

export interface StreamEvent {
  v: number;
  type: StreamEventType;
  session_id: string;
  turn_id: string;
  ts?: string;
  agent_id?: string;
  user_message?: string;
  status?: 'ok' | 'error' | 'cancelled';
  part?: MessagePart;
  part_id?: string;
  patch?: PartPatch;
  message?: string;
  code?: string;
  recoverable?: boolean;
}

export type ChatMessageRole = 'user' | 'assistant';

export type ChatMessageStatus = 'streaming' | 'done' | 'error';

export interface ChatUserMessage {
  id: string;
  role: 'user';
  content: string;
  createdAt: string;
}

export interface ChatAssistantMessage {
  id: string;
  role: 'assistant';
  parts: MessagePart[];
  status: ChatMessageStatus;
  error?: string;
  createdAt: string;
}

export type ChatMessage = ChatUserMessage | ChatAssistantMessage;

export interface ChatSession {
  id: string;
  title: string;
  agentId: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
}

export function isAssistantMessage(msg: ChatMessage): msg is ChatAssistantMessage {
  return msg.role === 'assistant';
}
