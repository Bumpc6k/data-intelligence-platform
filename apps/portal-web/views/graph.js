/**
 * 血缘图视图（renderer: graph）。
 *
 * 输入是内核 `/upstream`、`/impact` 的**原始形状**（tables / paths / levels），
 * 这里做两件事，都只做重组、不做推断：
 *
 * 1. `paths` 上相邻两跳 → 边（与 `portal-api` 的 `/api/lineage/upstream` 同一套口径）；
 * 2. 表名 → 层次（`cdw.dws_产销存汇总` → `dws`；表名里看不出来就看 schema，再不行算 other）。
 *
 * 布局是**纯函数**（`layoutGraph`），所以能在 Node 里单测，不依赖浏览器：
 * 层次为列，列内按输入顺序排列，边画成 SVG 直线。
 */

import { el, header, registerView } from './registry.js';

export const LAYERS = ['ods', 'dwd', 'dws', 'ads', 'dim', 'other'];
const NODE_W = 168;
const NODE_H = 30;
const COL_GAP = 26;
const ROW_GAP = 12;
const PAD = 12;

/** 表名 → 层次。与 portal-api 的 `layer_of` 同一套规则（两处都要用，不改一处忘一处）。 */
export function layerOf(table) {
  const leaf = String(table || '').split('.').pop().toLowerCase();
  const head = leaf.split('_')[0];
  if (LAYERS.includes(head)) return head;
  const schema = String(table || '').split('.')[0].toLowerCase();
  return LAYERS.includes(schema) && schema !== 'other' ? schema : 'other';
}

/** `paths` 上相邻两跳就是一条边（去重，保持出现顺序）。 */
export function edgesOf(paths) {
  const edges = [];
  const seen = new Set();
  (paths || []).forEach((path) => {
    (path || []).forEach((node, i) => {
      if (i === 0) return;
      const pair = [path[i - 1], node];
      const key = pair.join('\u0000');
      if (!seen.has(key)) { seen.add(key); edges.push(pair); }
    });
  });
  return edges;
}

/** 纯布局：给节点与边算坐标（不碰 DOM，可单测）。 */
export function layoutGraph(data) {
  const nodes = [];
  const seen = new Set();
  const push = (name) => {
    if (!name || seen.has(name)) return;
    seen.add(name);
    nodes.push({ name, layer: layerOf(name) });
  };
  (data && data.paths ? data.paths : []).forEach((path) => (path || []).forEach(push));
  (data && data.tables ? data.tables : []).forEach(push);
  push(data && data.start_table);

  const columns = LAYERS.filter((layer) => nodes.some((n) => n.layer === layer));
  const start = data && data.start_table;
  // 起点放最右（上游图）或最左（下游图）：读图方向和"往哪追"一致
  if (data && data.direction === 'downstream') columns.reverse();

  const pos = new Map();
  const placed = [];
  columns.forEach((layer, ci) => {
    nodes.filter((n) => n.layer === layer).forEach((node, ri) => {
      const box = {
        ...node,
        x: PAD + ci * (NODE_W + COL_GAP),
        y: PAD + ri * (NODE_H + ROW_GAP),
        w: NODE_W,
        h: NODE_H,
        highlight: node.name === start,
      };
      pos.set(node.name, box);
      placed.push(box);
    });
  });

  const edges = edgesOf(data && data.paths)
    .map(([from, to]) => {
      const a = pos.get(from);
      const b = pos.get(to);
      if (!a || !b) return null;
      // 从左列的右边连到右列的左边（谁在左由布局决定）
      const [left, right] = a.x <= b.x ? [a, b] : [b, a];
      return {
        from, to,
        x1: left.x + left.w, y1: left.y + left.h / 2,
        x2: right.x, y2: right.y + right.h / 2,
      };
    })
    .filter(Boolean);

  const width = PAD * 2 + Math.max(columns.length, 1) * NODE_W + Math.max(columns.length - 1, 0) * COL_GAP;
  const height = PAD * 2 + Math.max(...columns.map((l) => placed.filter((n) => n.layer === l).length), 1) * (NODE_H + ROW_GAP);
  return { nodes: placed, edges, columns, width, height };
}

const SVG = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, String(v)));
  return node;
}

registerView({
  renderer: 'graph',
  label: '血缘图',
  render(block, mount) {
    const data = block.data || {};
    const { nodes, edges, columns, width, height } = layoutGraph(data);
    const count = data.upstream_count !== undefined && data.upstream_count !== null
      ? `上游 ${data.upstream_count} 张`
      : (data.downstream_count !== undefined && data.downstream_count !== null ? `下游 ${data.downstream_count} 张` : '');
    mount.append(header(block.title || '血缘', [count, `${edges.length} 条边`, block.source].filter(Boolean).join(' · ')));

    if (!nodes.length) {
      mount.append(el('div', 'meta', '这次没有血缘数据（内核没返回 tables/paths）。'));
      return;
    }

    const box = el('div', 'vgraph');
    const svg = svgEl('svg', { width, height, viewBox: `0 0 ${width} ${height}` });
    columns.forEach((layer, ci) => {
      const t = svgEl('text', { x: PAD + ci * (NODE_W + COL_GAP), y: 10, class: 'vglayer' });
      t.textContent = layer;
      svg.append(t);
    });
    edges.forEach((e) => {
      svg.append(svgEl('line', { x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2, class: 'vgedge' }));
    });
    nodes.forEach((n) => {
      svg.append(svgEl('rect', { x: n.x, y: n.y, width: n.w, height: n.h, rx: 6, class: n.highlight ? 'vgnode on' : 'vgnode' }));
      const label = svgEl('text', { x: n.x + 8, y: n.y + n.h / 2 + 4, class: 'vglabel' });
      label.textContent = n.name.split('.').pop().slice(0, 16);
      const title = svgEl('title');
      title.textContent = n.name;
      label.append(title);
      svg.append(label);
    });
    box.append(svg);
    mount.append(box);
    mount.append(el('div', 'meta', `层次列：${columns.join(' → ')}（方向：${data.direction || 'upstream'}）`));
  },
});
