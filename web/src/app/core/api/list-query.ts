import type { ListQuery } from './metadata-api.service';

/** 与后端 MAX_LIST_PAGE_SIZE 一致，工作台列表一次拉全量 */
export const LIST_ALL_PAGE_SIZE = 10_000;

export function listAllQuery(extra: Omit<ListQuery, 'page' | 'page_size'> = {}): ListQuery {
  return {
    page: 1,
    page_size: LIST_ALL_PAGE_SIZE,
    ...extra,
  };
}
