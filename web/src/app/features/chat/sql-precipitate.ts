export interface SqlPrecipitateDraft {
  name: string;
  logic: string;
  content: string;
}

/** 从助手正文里取出 SQL，并用用户问题或说明首句作为默认样例名称。 */
export function buildSqlPrecipitateDraft(
  markdown: string,
  sql: string,
  question: string,
): SqlPrecipitateDraft {
  const content = (sql || '').trim();
  const logic = (markdown || '')
    .replace(/```[\w-]*\s*[\s\S]*?```/g, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*/g, '')
    .trim();
  const questionName = question.trim().replace(/\s+/g, ' ');
  const fallback = logic
    .split(/[。\n]/)
    .map((item) => item.trim())
    .find(Boolean) ?? '';
  const name = (questionName || fallback || '问数 SQL').slice(0, 120);
  return { name, logic, content };
}
