/**
 * 表格视图（renderer: table）。
 *
 * 数据来源两类，都画成表：
 * - 检索命中（`/kb/search` 的 groups 摊平后的 rows）—— 列固定，行里带 kind/score；
 * - **任意键值对象**：认不出 rows 时把 `data` 摊平成"字段 / 值"两列。
 *   这条兜底不是偷懒：视图名可以被 skill 声明换掉（把血缘声明改成 table），
 *   那时进来的就是血缘的 data —— 表格视图得照样画得出来，而不是白屏。
 */

import { el, header, registerView } from './registry.js';

const COLUMNS = [
  ['kind', '类型'],
  ['name', '名字'],
  ['chinese_name', '中文名'],
  ['table', '表'],
  ['layer', '层次'],
  ['formula', '口径'],
  ['score', '相关度'],
];

function cell(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return String(value);
}

function rowsTable(rows) {
  const box = el('div', 'vtbl');
  const table = el('table');
  const thead = el('thead');
  const hr = el('tr');
  COLUMNS.forEach(([, label]) => hr.append(el('th', null, label)));
  thead.append(hr);
  table.append(thead);
  const tbody = el('tbody');
  rows.forEach((row) => {
    const tr = el('tr');
    COLUMNS.forEach(([key]) => {
      const td = el('td', key === 'score' ? 'num' : null, cell(row[key]));
      if (key === 'formula' && row[key]) td.title = row[key];
      tr.append(td);
    });
    tbody.append(tr);
  });
  table.append(tbody);
  box.append(table);
  return box;
}

function kvTable(data) {
  const box = el('div', 'vtbl');
  const table = el('table');
  const tbody = el('tbody');
  Object.entries(data || {}).forEach(([key, value]) => {
    const tr = el('tr');
    tr.append(el('th', null, key));
    const text = value !== null && typeof value === 'object' ? JSON.stringify(value) : cell(value);
    const td = el('td', null, text);
    td.title = text;
    tr.append(td);
    tbody.append(tr);
  });
  table.append(tbody);
  box.append(table);
  return box;
}

/** rows 为空就摊平 data —— 表格视图对任何结构都得给出可读的东西。 */
export function tableRowCount(data) {
  const rows = (data && data.rows) || null;
  return rows ? rows.length : Object.keys(data || {}).length;
}

registerView({
  renderer: 'table',
  label: '表格',
  render(block, mount) {
    const data = block.data || {};
    const rows = Array.isArray(data.rows) ? data.rows : null;
    mount.append(header(block.title || '表格', block.source || ''));
    mount.append(rows ? rowsTable(rows) : kvTable(data));
    const bits = [];
    if (data.total !== undefined) bits.push(`命中总数 ${data.total}`);
    if (rows) bits.push(`本表 ${rows.length} 行`);
    if (data.truncated) bits.push('（已截断）');
    if (block.note) bits.push(block.note);
    if (bits.length) mount.append(el('div', 'meta', bits.join(' · ')));
  },
});
