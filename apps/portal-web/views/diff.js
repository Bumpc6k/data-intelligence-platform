/**
 * 版本对照视图（renderer: diff）。
 *
 * 画的是**两版口径的逐字段对照**（来自平台口径库 M3-02 的版本表）：
 * 变了的地方标红，没变的照原样列出来 —— 关键是让"哪一版改了什么"一眼可见。
 *
 * 一条诚实规定：`changed` 为空时**明写"两版内容一致"**，不许装作有差异
 * （版本号会因回滚/重复入库而增加，公式却可能一模一样，这在 #13 是正常现象）。
 */

import { el, header, registerView } from './registry.js';

const FIELDS = [
  ['formula', '口径公式'],
  ['chinese_name', '中文名'],
  ['source_script', '来源脚本'],
  ['source_line', '来源行'],
];

/** 纯函数：把 from/to 摊成"字段 / 旧 / 新 / 是否变了"四列（可单测）。 */
export function diffRows(data, fields = FIELDS) {
  const from = (data && data.from) || {};
  const to = (data && data.to) || {};
  const changedFields = new Set((data && data.changed ? data.changed : []).map((c) => c.field));
  return fields.map(([key, label]) => ({
    key,
    label,
    before: from[key],
    after: to[key],
    changed: changedFields.has(key) || (from[key] !== undefined && to[key] !== undefined && from[key] !== to[key]),
  }));
}

function show(value) {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

registerView({
  renderer: 'diff',
  label: '版本对照',
  render(block, mount) {
    const data = block.data || {};
    const from = data.from || {};
    const to = data.to || {};
    mount.append(header(
      block.title || `版本对照：${data.subject || ''}`,
      [`v${show(from.version)} → v${show(to.version)}`,
        data.version_count ? `共 ${data.version_count} 版` : null,
        block.source].filter(Boolean).join(' · '),
    ));

    const rows = diffRows(data);
    const table = el('table', 'vdiff');
    const tbody = el('tbody');
    rows.forEach((row) => {
      const tr = el('tr', row.changed ? 'chg' : null);
      tr.append(el('th', null, row.label));
      tr.append(el('td', null, show(row.before)));
      tr.append(el('td', null, show(row.after)));
      tr.append(el('td', 'flag', row.changed ? '变了' : '＝'));
      tbody.append(tr);
    });
    table.append(tbody);
    mount.append(table);

    const status = [`旧：${show(from.status)}`, `新：${show(to.status)}`,
      from.approved_by || to.approved_by ? `人：${show(to.approved_by || from.approved_by)}` : null]
      .filter(Boolean).join(' · ');
    mount.append(el('div', 'meta', status));
    mount.append(el('div', 'meta',
      rows.some((r) => r.changed)
        ? '标记「变了」的行就是两版的差异（其余一致）。'
        : '两版内容一致 —— 只有版本号/时间不同（回滚或重复入库都会这样，见 #13）。'));
  },
});
