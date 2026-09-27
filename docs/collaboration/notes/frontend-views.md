# 前端视图插件机制（M4-01 / Issue #17）

一句话：**宿主只负责"把块交给注册表"，视图自己负责画**。
所以"加一个视图"= 加一个注册文件，`index.html` 一个字不用改（验收②）。

```
answer.views = [ {renderer:"table", data:{...}}, {renderer:"sql", ...}, {renderer:"graph", ...}, {renderer:"diff", ...} ]
      │
      ▼  宿主（index.html 的 renderViews）
   blocks.forEach(b => window.renderView(b, box))        ← 就这两行，不认识任何具体视图
      │
      ▼  注册表（apps/portal-web/views/registry.js）
   registry.get(block.renderer).render(block, mount)     ← 认不出就渲染"未注册 + 原始数据"，不白屏
```

## 1. 三层分工（谁管什么）

| 层 | 文件 | 管什么 |
| --- | --- | --- |
| 契约 | `packages/dip-contracts/src/dip_contracts/models.py` | `ViewBlock`（renderer/title/data/source/note）+ `RENDERERS` 词汇表；未知视图名在**契约层就被拦** |
| 后端 | `packages/dip-agent/src/dip_agent/agent/views.py` | 内核结果 → 视图块：血缘→graph、检索→table、口径 SQL→附加 sql 块；不画空图（没数据就不给块） |
| 后端 | `apps/portal-api/src/portal_api/view_support.py` | **平台侧**视图：口径版本对照（diff）来自口径库，接在内核视图之后 |
| 前端 | `apps/portal-web/views/registry.js` | 注册表与分派；重复注册报错；认不出不白屏 |
| 前端 | `apps/portal-web/views/{table,graph,sql,diff}.js` | 四个视图各自的画法（零依赖，纯 DOM/SVG） |
| 前端 | `apps/portal-web/views/index.js` | **唯一装配点**：加视图只改这里（一行 import） |
| 前端 | `apps/portal-web/index.html` | 宿主：遍历 `answer.views` → `renderView`；一个新组件样式都不放这里 |

## 2. 词汇表只有一份

`dip_skills.Renderer`（skill 契约的第 8 类字段）↔ `dip_contracts.RENDERERS` ↔ 前端注册名。
三处一致由测试钉住（`tests/test_views.py::test_视图词汇表与_skill_契约一致`）。
**前端不自己发明视图名**：注册表里没有的名字，后端也发不出来。

## 3. skill 声明怎么影响渲染（这条是 Issue 的原话）

`ops/start-portal.sh` 起的 portal-api 会读 `packages/*/skill.yaml`：

```
lineage.analyze  evidence_kind=lineage  renderer=graph
        │
        ▼
deps.skill_renderer_by_kind() → {"lineage": "graph"}
        │
        ▼
Agent(renderer_by_kind=...) → views.view_map(..., renderer_by_kind=...)
```

把声明里的 `renderer: graph` 改成 `table`，血缘结果就会交给**表格视图**渲染（表格视图认不出 rows 时会摊平成键值对，不会白屏）——
`tests/test_views.py::test_skill_声明能把血缘的视图换成表格` 就是这个行为的钉子。

## 4. 想加东西时的三步

**加一个视图**（例如饼图）：

1. `apps/portal-web/views/pie.js`：`registerView({renderer:'pie', label:'饼图', render(block, mount){...}})`
2. `apps/portal-web/views/index.js` 加一行 `import './pie.js';`
3. 完。宿主、注册表、契约层都不用动 —— 但**后端要能发出 `renderer:"pie"` 的块**，
   而 `RENDERERS` 里没有 `pie`，所以想真用起来得先在契约词汇表里加它（这是故意的：视图名是契约，不是前端私事）。

**加一类数据块**：在 `dip_agent/views.py` 的 `blocks_for()` 里加规则（哪种工具、什么数据、给哪个视图），
或者在 `portal-api/view_support.py` 里补平台侧块（像 diff 那样）。原数据一律原样放进 `data`，不做二次改写。

## 5. 真跑踩到的坑（都记下来）

| 坑 | 现象 | 正解 |
| --- | --- | --- |
| 运行时 PYTHONPATH 是**手工清单** | 新包（`dip-skills` / `dip-docs`）没登记 → 服务起来后 `/api/agent/ask` **500**：`ModuleNotFoundError` | `ops/start-portal.sh` 的 `EXPORT_PATH` 里登记；pyproject 的 pythonpath 只管测试，管不到运行的服务 |
| tmux 不继承调用方环境 | 重启后库连不上（`数据库不可用（未落库）`） | 脚本里显式 `env VAR=...` 传进去；`start-portal.sh` 现在还会读仓里的 `.env` |
| dockerd 重启后**容器端口代理失效** | `127.0.0.1:15432` 连上就断（容器内 psql 却好好的） | `docker restart dip-pg` 让代理重新绑定（不是数据库的问题） |
| 浏览器工具（headless CDP）截图偶发超时 | `Page.captureScreenshot timed out after 60s` | 重试一次基本就好；四次截图里出现过 1 次超时 |
| 浏览器工具的代码要走 stdin | 代码里带中文 → `UnicodeDecodeError` | 交给浏览器工具的代码**只写 ASCII** |

## 6. 当前不做的事

- **不引前端框架、不加构建步骤**：这个前端是零构建静态页（后端直接托管），插件机制用最朴素的 DOM/SVG，够用。
- **不做前端路由 / 主题重做**（Issue 边界：不重做视觉设计）：只补了视图区那几个类名的样式。
- **不做"记住用户上次选的视图"**（没这个需求，别提前做）。
- **不做视图级的权限**（视图只画后端给的数据，权限在后端）。
