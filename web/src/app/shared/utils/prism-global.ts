import Prism from 'prismjs';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-sql';

/** ngx-markdown 依赖全局 Prism.highlightAllUnder，需在 bootstrap 前加载 */
Prism.manual = true;
globalThis.Prism = Prism;

export { Prism };
