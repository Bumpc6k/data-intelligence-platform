/**
 * 视图总入口（M4-01 / Issue #17）。
 *
 * **这个文件就是"注册表"的全部装配**：谁要加视图，就在下面加一行 import
 * —— 宿主（`index.html`）一个字都不用改，它只会调 `renderView(block, mount)`（验收②）。
 *
 * 顺序 = 注册顺序，也等于"同名视图冲突时报错的顺序"，没有其它含义。
 */

import './table.js';
import './graph.js';
import './sql.js';
import './diff.js';

export { renderView, registeredRenderers, getView, registerView } from './registry.js';
