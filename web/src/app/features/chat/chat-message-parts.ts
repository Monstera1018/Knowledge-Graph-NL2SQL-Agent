import type { ArtifactPart, DataPart, MessagePart, TextPart } from '../../core/models/chat.models';

const PART_ORDER = {
  workflow: 0,
  tool: 10,
  data_plan: 18,
  text: 20,
  data_table: 40,
  data_diagnostic: 50,
  artifact_thinking: 55,
  artifact_sql_rationale: 58,
  artifact_sql: 60,
  text_query: 70,
  artifact: 80,
  error: 90,
} as const;

const QUERY_RESULT_TEXT_RE = /(?:##|###)\s*[✅🔎📊]?\s*(?:SQL 合法性检查|查询结果)/;

export function isQueryResultText(part: TextPart): boolean {
  return QUERY_RESULT_TEXT_RE.test(part.text);
}

export function partOrderKey(part: MessagePart): number {
  if (part.kind === 'text') {
    return isQueryResultText(part) ? PART_ORDER.text_query : PART_ORDER.text;
  }
  if (part.kind === 'workflow') {
    return PART_ORDER.workflow;
  }
  if (part.kind === 'tool') {
    return PART_ORDER.tool;
  }
  if (part.kind === 'error') {
    return PART_ORDER.error;
  }
  if (part.kind === 'data') {
    const dataPart = part as DataPart;
    const kind = dataPart.payload['kind'];
    if (kind === 'schema_plan') {
      return PART_ORDER.data_plan;
    }
    if (kind === 'sql_diagnostic') {
      return PART_ORDER.data_diagnostic;
    }
    return PART_ORDER.data_table;
  }
  if (part.kind === 'artifact') {
    const artifact = part as ArtifactPart;
    const language = artifact.language.trim().toLowerCase();
    if (artifact.id.includes('sql-rationale') || artifact.title === '查询说明') {
      return PART_ORDER.artifact_sql_rationale;
    }
    if (language === 'sql') {
      return PART_ORDER.artifact_sql;
    }
    if (language === 'thinking') {
      return PART_ORDER.artifact_thinking;
    }
    return PART_ORDER.artifact;
  }
  return 100;
}

export function shouldShowDataPart(part: DataPart, streaming = false): boolean {
  const kind = part.payload['kind'];
  if (kind !== 'sql_diagnostic') {
    return true;
  }
  return part.payload['resolved'] !== true;
}

export function shouldShowArtifactPart(part: ArtifactPart): boolean {
  const language = part.language.trim().toLowerCase();
  if (language === 'thinking') {
    return Boolean(part.content.trim());
  }
  return true;
}

export function shouldShowTextPart(part: TextPart, streaming = false): boolean {
  const text = part.text.trim();
  if (!text) {
    return false;
  }
  if (
    streaming &&
    /##\s*❌\s*SQL 合法性检查/.test(text) &&
    !/##\s*✅\s*SQL 合法性检查/.test(text)
  ) {
    return false;
  }
  return true;
}

export function sortVisibleParts(parts: readonly MessagePart[]): MessagePart[] {
  return parts.slice().sort((left, right) => partOrderKey(left) - partOrderKey(right));
}

function isHorizontalRuleChunk(chunk: string): boolean {
  return /^-{3,}$/.test(chunk.trim());
}

/** Validation block before query results within the same text part. */
export function reorderQueryMarkdown(text: string): string {
  const chunks = text.split(/\n\n---\n\n/);
  if (chunks.length < 2) {
    return text;
  }

  const validation: string[] = [];
  const results: string[] = [];
  const others: string[] = [];

  for (const chunk of chunks) {
    const trimmed = chunk.trim();
    if (!trimmed || isHorizontalRuleChunk(trimmed)) {
      continue;
    }
    if (/SQL 合法性检查/.test(trimmed)) {
      validation.push(trimmed);
    } else if (/查询结果/.test(trimmed)) {
      results.push(trimmed);
    } else {
      others.push(trimmed);
    }
  }

  if (!validation.length && !results.length) {
    return text;
  }

  const preferredValidation =
    validation.find((chunk) => /##\s*✅\s*SQL 合法性检查/.test(chunk)) ??
    validation[validation.length - 1];

  return [...others, preferredValidation, ...results].filter(Boolean).join('\n\n');
}
