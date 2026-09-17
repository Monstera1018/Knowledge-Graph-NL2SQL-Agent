/** 忽略过期的 HTTP 回调（快速切换搜索/选中时） */
export function createRequestGuard() {
  let latest = 0;
  return {
    next(): number {
      latest += 1;
      return latest;
    },
    isLatest(id: number): boolean {
      return id === latest;
    },
  };
}
