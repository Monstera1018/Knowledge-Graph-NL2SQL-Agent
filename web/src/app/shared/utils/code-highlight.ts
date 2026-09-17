import { Prism } from './prism-global';

export function highlightCode(text: string, language: string): string {
  const lang = language.toLowerCase();
  const grammar = Prism.languages[lang];
  if (!grammar || !text.trim()) {
    return escapeHtml(text);
  }
  return Prism.highlight(text, grammar, lang);
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
