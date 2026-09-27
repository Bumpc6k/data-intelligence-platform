/**
 * 视图注册表与四个视图的单元测试（M4-01 / Issue #17）。
 *
 * 跑法（Node 自带测试运行器，不引任何依赖）：
 *     node --test apps/portal-web/views/
 *
 * 为什么能在 Node 里跑：视图模块只在**调用 render 时**才碰 DOM，
 * import 阶段只做 `registerView(...)`。所以这里给一个极小的 DOM 桩就能测渲染，
 * 不需要 jsdom，也不需要浏览器。
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

// 先 import 注册表入口（它按顺序 import 四个视图，也就是真实装配顺序）
import { getView, registerView, registeredRenderers, renderView } from './index.js';
import { edgesOf, layerOf, layoutGraph } from './graph.js';
import { diffRows } from './diff.js';
import { highlight } from './sql.js';
import { tableRowCount } from './table.js';

/* ---------------------------------------------------------------- 极小的 DOM 桩 */

class Stub {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.className = '';
    this.children = [];
    this.attributes = {};
    this.textContent = '';
    this.innerHTML = '';
    this.title = '';
  }

  append(...nodes) {
    nodes.forEach((n) => this.children.push(n));
    return this;
  }

  appendChild(n) { return this.append(n); }

  setAttribute(k, v) { this.attributes[k] = v; }
}

globalThis.document = {
  createElement: (tag) => new Stub(tag),
  createElementNS: (_ns, tag) => new Stub(tag),
};

/** 摊平一棵桩节点树里的全部文本与类名，便于断言。 */
function flatten(node, out = { text: [], classes: [] }) {
  if (!node || typeof node !== 'object') return out;
  if (node.className) out.classes.push(node.className);
  if (node.textContent) out.text.push(String(node.textContent));
  if (node.innerHTML) out.text.push(String(node.innerHTML).replace(/<[^>]+>/g, ''));
  (node.children || []).forEach((child) => flatten(child, out));
  return out;
}

function mount() { return new Stub('div'); }

/* ---------------------------------------------------------------- 注册表 */

test('四个视图都注册上了，且名字就是契约层那四个', () => {
  assert.deepEqual(registeredRenderers(), ['table', 'graph', 'sql', 'diff']);
  for (const name of ['table', 'graph', 'sql', 'diff']) {
    assert.ok(getView(name), `${name} 应该有实现`);
  }
});

test('重复注册同一个名字直接报错（谁生效不能靠 import 顺序）', () => {
  assert.throws(() => registerView({ renderer: 'table', render: () => {} }), /已注册过/);
});

test('注册的视图必须实现 render', () => {
  assert.throws(() => registerView({ renderer: '没实现' }), /必须实现 render/);
});

test('renderView 按名字分派并返回用到的视图名', () => {
  const box = mount();
  const used = renderView({ renderer: 'table', title: 't', data: { rows: [{ name: 'x' }] } }, box);
  assert.equal(used, 'table');
  assert.ok(flatten(box).text.includes('x'));
});

test('认不出的视图名不白屏：显式报警并把原始数据摊开', () => {
  const box = mount();
  const used = renderView({ renderer: 'pie', data: { slices: [1, 2, 3] } }, box);
  assert.equal(used, '未注册');
  const flat = flatten(box);
  assert.ok(flat.classes.some((c) => c.includes('unregistered')));
  assert.ok(flat.text.some((t) => t.includes('未注册的视图：pie')));
  assert.ok(flat.text.some((t) => t.includes('slices')));
});

test('新增视图只注册就生效——宿主一个字不用改', () => {
  // 这里不 import 宿主，也不需要改注册表入口：直接注册一个新视图就能用
  registerView({
    renderer: 'pie',
    label: '饼图',
    render(block, host) {
      const div = document.createElement('div');
      div.className = 'pie';
      div.textContent = '切了 ' + (block.data.slices || []).length + ' 块';
      host.append(div);
    },
  });
  const box = mount();
  const used = renderView({ renderer: 'pie', data: { slices: [1, 2, 3] } }, box);
  assert.equal(used, 'pie');
  assert.ok(flatten(box).text.includes('切了 3 块'));
  assert.ok(registeredRenderers().includes('pie'));
});

/* ---------------------------------------------------------------- 表格 */

test('表格视图：rows 画成表，非 rows 结构摊平成键值对', () => {
  const rows = mount();
  renderView({ renderer: 'table', data: { rows: [{ name: 'chanliang_qty', formula: '产量 = 打码量' }] } }, rows);
  assert.ok(flatten(rows).text.includes('chanliang_qty'));

  const kv = mount();
  renderView({ renderer: 'table', data: { start_table: 'ads.ads_产销存月报', upstream_count: 14 } }, kv);
  const flat = flatten(kv);
  assert.ok(flat.text.includes('ads.ads_产销存月报'));
  assert.ok(flat.text.includes('14'));

  assert.equal(tableRowCount({ rows: [1, 2] }), 2);
  assert.equal(tableRowCount({ a: 1, b: 2 }), 2);
});

/* ---------------------------------------------------------------- 血缘图（纯函数） */

test('层次判定与 portal-api 同一套规则', () => {
  assert.equal(layerOf('cdw.dws_产销存汇总'), 'dws');
  assert.equal(layerOf('ads.ads_产销存月报'), 'ads');
  assert.equal(layerOf('dim.dim_brand'), 'dim');
  assert.equal(layerOf('unknowntable'), 'other');
});

test('paths 上相邻两跳就是一条边，且去重', () => {
  const edges = edgesOf([
    ['a', 'b', 'c'],
    ['a', 'b', 'd'],
  ]);
  assert.deepEqual(edges, [['a', 'b'], ['b', 'c'], ['b', 'd']]);
});

test('布局是纯函数：列按层次、起点高亮、边有坐标', () => {
  const layout = layoutGraph({
    start_table: 'ads.ads_产销存月报',
    tables: ['cdw.dws_产销存汇总'],
    paths: [['ads.ads_产销存月报', 'cdw.dws_产销存汇总']],
    direction: 'upstream',
  });
  assert.deepEqual(layout.columns, ['dws', 'ads']);           // 上游在左、起点在右
  assert.equal(layout.nodes.length, 2);
  assert.equal(layout.nodes.find((n) => n.name === 'ads.ads_产销存月报').highlight, true);
  assert.equal(layout.edges.length, 1);
  assert.ok(layout.edges[0].x1 < layout.edges[0].x2);
  assert.ok(layout.width > 0 && layout.height > 0);

  const downstream = layoutGraph({ start_table: 'x.dws_a', tables: ['y.ads_b'], direction: 'downstream' });
  assert.deepEqual(downstream.columns, ['ads', 'dws']);       // 下游图反过来
});

test('没有数据时布局返回空（视图会显式说"没数据"而不是画空图）', () => {
  const layout = layoutGraph({});
  assert.equal(layout.nodes.length, 0);
  assert.equal(layout.edges.length, 0);
});

/* ---------------------------------------------------------------- SQL */

test('SQL 高亮：关键词包 span，且先转义（不许把 SQL 当 HTML）', () => {
  const html = highlight('select sum(a) from t where b < 3 and c > 1');
  assert.ok(html.includes('<span class="kw">sum</span>'));
  assert.ok(html.includes('&lt; 3'));
  assert.ok(html.includes('&gt; 1'));
  assert.ok(!html.includes('< 3'));
});

test('SQL 视图画出口径表达式与来源', () => {
  const box = mount();
  renderView({
    renderer: 'sql',
    title: '口径 SQL',
    data: { statements: [{ sql: 'SUM(b.dama_qty) AS chanliang_qty', source_file: 'x.sql', source_stmt: 1, metric_name: 'chanliang_qty' }] },
  }, box);
  const flat = flatten(box);
  assert.ok(flat.text.some((t) => t.includes('dama_qty')));
  assert.ok(flat.text.some((t) => t.includes('x.sql')));
});

/* ---------------------------------------------------------------- diff */

test('版本对照：变的标出来，没变的照列', () => {
  const rows = diffRows({
    from: { version: 1, formula: '产量 = 打码量', status: 'superseded' },
    to: { version: 2, formula: '产量 = 打码量 + 跳码量', status: 'active' },
    changed: [{ field: 'formula', before: '产量 = 打码量', after: '产量 = 打码量 + 跳码量' }],
  });
  assert.equal(rows.find((r) => r.key === 'formula').changed, true);
  assert.equal(rows.find((r) => r.key === 'chinese_name').changed, false);
});

test('两版一致时不假装有差异', () => {
  const box = mount();
  renderView({
    renderer: 'diff',
    data: {
      subject: 'cdw.dwd_卷烟产量码段明细.chanliang_qty',
      from: { version: 37, formula: '产量 = 打码量', status: 'superseded' },
      to: { version: 38, formula: '产量 = 打码量', status: 'active' },
      changed: [],
      version_count: 38,
    },
  }, box);
  const flat = flatten(box);
  assert.ok(flat.text.some((t) => t.includes('两版内容一致')));
  assert.ok(flat.text.some((t) => t.includes('共 38 版')));
});

test('差异视图画成四列：字段 / 旧 / 新 / 是否变了', () => {
  const box = mount();
  renderView({
    renderer: 'diff',
    data: {
      from: { version: 1, formula: '旧公式' },
      to: { version: 2, formula: '新公式' },
      changed: [{ field: 'formula', before: '旧公式', after: '新公式' }],
    },
  }, box);
  const flat = flatten(box);
  assert.ok(flat.text.includes('旧公式') && flat.text.includes('新公式'));
  assert.ok(flat.text.includes('变了'));
  assert.ok(flat.classes.includes('chg'));
});
