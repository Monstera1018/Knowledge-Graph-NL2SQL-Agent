import { KnowledgeRelationDraft } from './knowledge.models';

export interface KnowledgeRelationDialogData {
  mode: 'add' | 'edit';
  initial?: KnowledgeRelationDraft;
  /** 已占用表名（小写），编辑时排除当前表 */
  blockedTableKeys: string[];
}

export type KnowledgeRelationDialogResult = KnowledgeRelationDraft | undefined;
