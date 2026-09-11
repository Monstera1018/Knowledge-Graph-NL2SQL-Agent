import type { SqlResultSet } from '../chat-sql-result';

export type ChatSqlResultDialogData = SqlResultSet & {
  setIndex?: number;
  setCount?: number;
};
