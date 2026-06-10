export type GraphNodeType = 'TABLE' | 'COLUMN' | 'ENUM' | 'KNOWLEDGE' | 'SQL';
export type GraphEdgeType = 'HAS' | 'DESCRIBES' | 'USES' | 'JOINS';

export interface GraphNodeView {
  uuid: string;
  type: GraphNodeType;
  label: string;
}

export interface GraphEdgeView {
  type: GraphEdgeType;
  source: string;
  target: string;
}

export interface GraphSnapshot {
  focus_uuid: string;
  nodes: GraphNodeView[];
  edges: GraphEdgeView[];
}
