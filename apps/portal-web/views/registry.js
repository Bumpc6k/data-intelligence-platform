/**
 * 视图注册表（M4-01 / Issue #17）。
 *
 * 规矩只有一条：**宿主不认识任何具体视图**。宿主拿到一个 `ViewBlock`（契约层给的结构），
 * 调 `renderView(block, mount)`；用哪个视图渲染由 `block.renderer` 在这里查表决定。
 * 于是"加一个视图"= 加一个 `registerView(...)` 文件，宿主一个字都不用改（验收②）。
 *
 * 三条刻意的设计：
 *
 * 1. **重复注册直接报错**，不覆盖。两个视图抢同一个名字，"用哪个"就变成看 import 顺序的运气，
 *    这种不确定性不该藏在渲染层。
 * 2. **认不出的视图名不静默吞掉**：渲染一块"未注册"提示 + 把原始数据摊开，
 *    人能看到"这儿本来有东西只是没视图"，而不是白屏（与铁律 1 同一条纪律）。
 * 3. **注册表不含任何前端框架**：本仓前端是零构建的静态页，用最朴素的 DOM，别为了插件机制引框架。
 */

const registry = new Map();

/** 注册一个视图。`view = {renderer, label, render(block, mount)}`。 */
export function registerView(view) {
  if (!view || typeof view !== 'object') throw new Error('registerView 需要一个对象');
  const name = view.renderer;
  if (!name || typeof name !== 'string') throw new Error('视图必须声明 renderer 名字');
  if (typeof view.render !== 'function') throw new Error(`视图 ${name} 必须实现 render(block, mount)`);
  if (registry.has(name)) throw new Error(`视图 ${name} 已注册过（不覆盖：谁生效不能靠 import 顺序）`);
  registry.set(name, view);
  return view;
}

/** 已注册的视图名（顺序 = 注册顺序）。 */
export function registeredRenderers() {
  return [...registry.keys()];
}

export function getView(name) {
  return registry.get(name) || null;
}

/**
 * 按 `block.renderer` 分派渲染。返回实际使用的视图名（'未注册' 表示走了兜底）。
 *
 * `mount` 是宿主给的容器元素；视图只往里面画，不碰别的地方。
 */
export function renderView(block, mount) {
  if (!mount) throw new Error('renderView 需要一个容器元素');
  const name = block && block.renderer;
  const view = getView(name);
  if (!view) {
    renderUnregistered(block, mount);
    return '未注册';
  }
  view.render(block, mount);
  return name;
}

/** 兜底：把"有块但没视图"这件事显式摆出来，并给出原始数据（不白屏、不假装没这回事）。 */
function renderUnregistered(block, mount) {
  const box = document.createElement('div');
  box.className = 'unregistered';
  box.append(header(
    '未注册的视图：' + (block && block.renderer ? block.renderer : '(缺 renderer)'),
    '宿主编排不认识它 —— 已注册：' + (registeredRenderers().join(' / ') || '（一个都没有）'),
  ));
  const pre = document.createElement('pre');
  pre.className = 'raw';
  try {
    pre.textContent = JSON.stringify(block && block.data ? block.data : {}, null, 1);
  } catch (e) {
    pre.textContent = '（数据无法序列化：' + e + '）';
  }
  box.append(pre);
  mount.append(box);
}

/** 卡片头（各视图共用）：标题 + 右侧小字（来源/备注）。 */
export function header(title, meta) {
  const box = document.createElement('div');
  box.className = 'vh';
  const h = document.createElement('div');
  h.className = 'vt';
  h.textContent = title || '';
  box.append(h);
  const m = document.createElement('div');
  m.className = 'vm';
  m.textContent = meta || '';
  box.append(m);
  return box;
}

/** 视图共用的元素小工具（零依赖，别引框架）。 */
export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** 一键创建元素并 setAttribute。 */
export function attrs(node, map) {
  Object.entries(map || {}).forEach(([k, v]) => { if (v !== undefined && v !== null) node.setAttribute(k, String(v)); });
  return node;
}
