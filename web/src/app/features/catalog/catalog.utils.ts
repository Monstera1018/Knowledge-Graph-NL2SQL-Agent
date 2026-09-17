import type { TableJoinRelation, TableNode } from '../../core/models/metadata.models';

export function tableCommentForName(tables: TableNode[], tableName: string): string {
  const table = tables.find(
    (item) => item.name.trim().toLowerCase() === tableName.trim().toLowerCase(),
  );
  return (table?.comment || table?.description || '').trim();
}

export function tableOptionLabel(table: Pick<TableNode, 'name' | 'comment' | 'description'>): string {
  const comment = (table.comment || table.description || '').trim();
  return comment ? `${table.name} — ${comment}` : table.name;
}

export function joinRelationPeerTooltip(peer: string, peerComment: string): string {
  const comment = peerComment.trim();
  return comment ? `${peer} — ${comment}` : peer;
}

export function joinRelationViewMeta(
  tableName: string,
  relation: TableJoinRelation,
  resolveComment: (name: string) => string,
): { peer: string; peerComment: string; role: string; arrow: string } {
  const current = tableName.trim().toLowerCase();
  const from = relation.from_table_name.trim().toLowerCase();
  if (from === current) {
    const peer = relation.to_table_name;
    return {
      peer,
      peerComment: resolveComment(peer),
      role: '当前表为主表',
      arrow: '→',
    };
  }
  const peer = relation.from_table_name;
  return {
    peer,
    peerComment: resolveComment(peer),
    role: '当前表为从表',
    arrow: '←',
  };
}
