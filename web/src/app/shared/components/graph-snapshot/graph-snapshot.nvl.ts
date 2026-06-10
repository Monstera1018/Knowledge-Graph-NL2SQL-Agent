import type { Node, Relationship } from '@neo4j-nvl/base';

import {
  GraphEdgeView,
  GraphNodeView,
  GraphSnapshot,
} from '../../../core/models/graph.models';

/** Neo4j Browser 风格：焦点金黄、主实体粉、附属灰、边线浅灰 */
const PALETTE = {
  focus: '#FFC107',
  table: '#F06292',
  column: '#90A4AE',
  enum: '#78909C',
  knowledge: '#CE93D8',
  sql: '#80CBC4',
  edge: '#BDBDBD',
} as const;

export interface NvlGraphElements {
  nodes: Node[];
  relationships: Relationship[];
  nodeIndex: Map<string, GraphNodeView>;
  fitNodeIds: string[];
}

export function toNvlElements(snapshot: GraphSnapshot): NvlGraphElements {
  const nodeIndex = new Map<string, GraphNodeView>();
  const columnCount = snapshot.nodes.filter((node) => node.type === 'COLUMN').length;
  const enumCount = snapshot.nodes.filter((node) => node.type === 'ENUM').length;

  const nodes: Node[] = snapshot.nodes.map((node) => {
    nodeIndex.set(node.uuid, node);
    const isFocus = node.uuid === snapshot.focus_uuid;
    return {
      id: node.uuid,
      caption: captionForNode(node),
      captionAlign: 'center',
      color: nodeColor(node, isFocus),
      size: nodeSize(node.type, columnCount, enumCount, isFocus),
      selected: isFocus,
    };
  });

  const relationships: Relationship[] = snapshot.edges.map((edge) => ({
    id: relationshipId(edge),
    from: edge.source,
    to: edge.target,
    type: edge.type,
    caption: edge.type,
    color: PALETTE.edge,
    width: 1,
  }));

  return {
    nodes,
    relationships,
    nodeIndex,
    fitNodeIds: snapshot.nodes.map((node) => node.uuid),
  };
}

export function relationshipId(edge: GraphEdgeView): string {
  return `${edge.source}-${edge.target}-${edge.type}`;
}

function nodeColor(node: GraphNodeView, isFocus: boolean): string {
  if (isFocus) {
    return PALETTE.focus;
  }

  switch (node.type) {
    case 'TABLE':
      return PALETTE.table;
    case 'COLUMN':
      return PALETTE.column;
    case 'ENUM':
      return PALETTE.enum;
    case 'KNOWLEDGE':
      return PALETTE.knowledge;
    case 'SQL':
      return PALETTE.sql;
    default:
      return PALETTE.column;
  }
}

function nodeSize(
  type: GraphNodeView['type'],
  columnCount: number,
  enumCount: number,
  isFocus = false,
): number {
  if (type === 'TABLE') {
    return isFocus ? 50 : 44;
  }
  if (type === 'COLUMN') {
    const base = columnCount > 36 ? 12 : columnCount > 20 ? 14 : 18;
    return isFocus ? base + 4 : base;
  }
  if (type === 'ENUM') {
    return enumCount > 120 ? 12 : enumCount > 60 ? 14 : 16;
  }
  if (type === 'KNOWLEDGE' || type === 'SQL') {
    return isFocus ? 34 : 30;
  }
  return 16;
}

function captionForNode(node: GraphNodeView): string | undefined {
  return truncateLabel(node.label, node.type);
}

function truncateLabel(label: string, type: GraphNodeView['type']): string {
  const max =
    type === 'TABLE' ? 28 : type === 'COLUMN' ? 18 : type === 'ENUM' ? 10 : 14;
  if (label.length <= max) {
    return label;
  }
  return `${label.slice(0, max - 1)}…`;
}

export { PALETTE as graphPalette };
