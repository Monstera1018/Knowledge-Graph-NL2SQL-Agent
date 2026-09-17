import { describe, expect, it } from 'vitest';

import './prism-global';
import { highlightCode } from './code-highlight';

describe('highlightCode', () => {
  it('highlights SQL keywords and comments', () => {
    const sql = "-- comment\nSELECT COUNT(*) FROM t WHERE id = 1;";
    const html = highlightCode(sql, 'sql');
    expect(html).toContain('token keyword');
    expect(html).toContain('token comment');
    expect(html).toContain('SELECT');
  });
});
