/**
 * SQL 视图（renderer: sql）。
 *
 * 画的是内核给的口径表达式与来源脚本**原文**（`expression_raw` / `formula` / `source_file#source_stmt`），
 * 不做格式化、不改写 —— 只做两件事：把关键词点亮（纯展示），以及把转义处理好（避免把 SQL 当 HTML 注入）。
 */

import { el, header, registerView } from './registry.js';

const KEYWORDS = ['SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'JOIN', 'LEFT JOIN',
  'INNER JOIN', 'ON', 'AS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'AND', 'OR', 'NOT', 'NULL',
  'SUM', 'COUNT', 'MAX', 'MIN', 'AVG', 'ROUND', 'COALESCE', 'NULLIF', 'CAST', 'OVER', 'PARTITION BY'];

/** 关键词高亮：**先转义再包 span**，别把 SQL 里的 `<`/`&` 变成 HTML。 */
export function highlight(sql, keywords = KEYWORDS) {
  let out = String(sql || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  keywords.forEach((kw) => {
    const pattern = new RegExp(`\\b${kw.replace(/ /g, '\\s+')}\\b`, 'gi');
    out = out.replace(pattern, (m) => `<span class="kw">${m}</span>`);
  });
  return out;
}

registerView({
  renderer: 'sql',
  label: 'SQL',
  render(block, mount) {
    const statements = (block.data && block.data.statements) || [];
    mount.append(header(block.title || 'SQL', [block.source, `${statements.length} 段`].filter(Boolean).join(' · ')));
    if (!statements.length) {
      mount.append(el('div', 'meta', '这次没有口径 SQL（内核没返回 expression_raw）。'));
      return;
    }
    statements.forEach((item, i) => {
      const wrap = el('div', 'vsql');
      const where = [item.source_file, item.source_stmt ? '语句 ' + item.source_stmt : null].filter(Boolean).join(' # ');
      if (item.metric_name || where) {
        wrap.append(el('div', 'meta', [item.metric_name, where].filter(Boolean).join(' · ')));
      }
      const pre = el('pre', 'code');
      pre.innerHTML = highlight(item.sql);   // highlight 里已转义
      wrap.append(pre);
      if (item.formula) wrap.append(el('div', 'meta', '人话口径：' + item.formula));
      if (item.notes) wrap.append(el('div', 'meta', item.notes));
      if (i < statements.length - 1) wrap.append(el('hr', 'sep'));
      mount.append(wrap);
    });
  },
});
