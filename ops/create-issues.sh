#!/usr/bin/env bash
# 一键建 Issue：把《v2 项目规划 v1》§3 的 19 个工作项建成 GitHub Issues（含标签与里程碑分组）
#
# 用法（在有 gh 且已登录的机器上）：
#     gh auth login                 # 只需一次（浏览器里输码）
#     bash ops/create-issues.sh     # 建标签 + 19 个 issue（已存在则跳过）
#     bash ops/create-issues.sh --dry-run   # 只打印，不提交
#
# 没有 gh 也能用：设置 GITHUB_TOKEN（有 repo 权限）后，脚本走 REST API。
set -uo pipefail
cd "$(dirname "$0")/.."

REPO=${REPO:-Bumpc6k/data-intelligence-platform}
DRY=${1:-}
USE_GH=1
command -v gh >/dev/null 2>&1 || USE_GH=0
[ -n "${GITHUB_TOKEN:-}" ] && USE_GH=0
if [ "$USE_GH" = 0 ] && [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "!! 需要其一：① 安装并登录 gh（gh auth login）② 或设置 GITHUB_TOKEN"; exit 1
fi

api_post() {  # $1=path $2=json
  curl -sS -X POST -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}$1" -d "$2" -o /dev/null -w "%{http_code}\n"
}

echo "== 1) 标签 =="
# 名字:颜色:说明
LABELS=(
  "M1-垂直切片:0E8A16:里程碑 M1"
  "M2-安全三件:1D76DB:里程碑 M2"
  "M3-知识双通道:5319E7:里程碑 M3"
  "M4-前端与演示:D93F0B:里程碑 M4"
  "角色-前端Node:FBCA04:熟 TypeScript / Node"
  "角色-后端Python:BFD4F2:熟 Python 服务"
  "角色-数据知识:C2E0C6:业务口径视角"
  "角色-测试验收:F9D0C4:爱找反例"
  "类型-功能:BFDADC:新能力"
  "类型-安全:D73A4A:安全相关，验收从严"
  "关键路径:E11D48:卡住后面所有任务，优先派"
  "size-S:EDEDED:半天内"
  "size-M:FEF2C0:1~3 天"
)
for row in "${LABELS[@]}"; do
  IFS=':' read -r name color desc <<<"$row"
  if [ "$DRY" = "--dry-run" ]; then echo "  [dry] label $name"; continue; fi
  if [ "$USE_GH" = 1 ]; then gh label create "$name" --color "$color" --description "$desc" --repo "$REPO" 2>/dev/null || true
  else api_post "/labels" "{\"name\":\"$name\",\"color\":\"$color\",\"description\":\"$desc\"}" >/dev/null; fi
done
echo "  完成（已存在会自动跳过）"

echo "== 2) Issues =="
create_issue() {  # $1=标题 $2=标签(逗号分隔) $3=正文
  local title="$1" labels="$2" body="$3"
  if [ "$DRY" = "--dry-run" ]; then echo "  [dry] $title  [$labels]"; return; fi
  if [ "$USE_GH" = 1 ]; then
    gh issue create --repo "$REPO" --title "$title" --body "$body" --label "$labels" >/dev/null && echo "  ✓ $title"
  else
    local json
    json=$(python3 -c 'import json,sys; print(json.dumps({"title":sys.argv[1],"body":sys.argv[2],"labels":sys.argv[3].split(",")}, ensure_ascii=False))' "$title" "$body" "$labels")
    code=$(api_post "/issues" "$json")
    echo "  $([ "$code" = "201" ] && echo "✓" || echo "✗($code)") $title"
  fi
}

# ---- 正文模板：交付物 / 边界 / 验收（验收全部可执行） ----
mk() { printf '%s\n\n**交付物**\n%s\n\n**边界（不要做）**\n%s\n\n**验收（可执行）**\n%s\n\n> 规则：一任务一 PR；`bash ops/gate.sh` 必须 ALL_PASS 才提；PR 里写"怎么验证"；冻结资产只按接口调用。\n> 依据：《v2 项目规划 v1》§3 与《协作者验证包 v1》。' "$1" "$2" "$3" "$4"; }

create_issue "M1-02 skill 契约声明与校验器" "M1-垂直切片,角色-后端Python,关键路径,size-M" "$(mk \
  '实现 skill 契约的声明规范与校验器（9 类字段），缺字段/越界即报错；附模板与样例。' \
  '- `dip_skills/spec.py`：YAML 声明解析为强类型对象\n- 校验器：字段完整性、取值合法性（如 side_effect 枚举）、版本兼容\n- `templates/skill.yaml` 模板 + 1 个样例\n- 测试：缺字段/错枚举/超长描述等反例' \
  '- 不做 skill 运行时、不做 MCP 服务（那是 M1-03）\n- 不引入重量级依赖（契约层必须保持纯净）' \
  '- 缺字段的声明被拒（有测试）\n- 模板复制即用，`gate` 绿\n- 契约字段与《架构讨论》§3 的 9 类完全一致')"

create_issue "M1-01 dsh 起壳与版本锁定" "M1-垂直切片,角色-前端Node,size-S" "$(mk \
  '把 DeepSeek Harness（dsh）在我们的环境跑起来，并写清从零到能对话的完整步骤。' \
  '- `docs/collaboration/notes/dsh-setup.md`：Node/pnpm 版本、镜像源、token URL、已知坑\n- 版本锁定写法（不追 latest）\n- 能对话的截图或录屏' \
  '- 不接业务 skill（那是 M1-03）、不做鉴权与多用户\n- 不改 dsh 源码' \
  '- 新人照文档一次跑通\n- 文档写明 3 个已知坑：必须走 registry.npmmirror.com、裸访问 / 返回 401 需带 token 的 URL、安装会跳过原生模块 install script\n- 落点：`docs/collaboration/notes/`（仓库暂无 docs/notes/）')"

create_issue "M1-03 血缘 skill（MCP 服务）" "M1-垂直切片,角色-后端Python,关键路径,size-M" "$(mk \
  '把现成的血缘解析包成一个 MCP skill，能被 dsh 这类外壳无差别调用，并带回执。' \
  '- MCP skill 服务（包装内核 `/analyze` + `/upstream`，只读）\n- 契约声明（按 M1-02 的规范）\n- 回执：来源端点 / 耗时 / 证据条数\n- `docs/collaboration/notes/mcp-skill.md`：注册与调用方式' \
  '- 只做 1 个 skill；不碰写操作类能力\n- 不改内核与 lineage-client 的接口' \
  '- 给一句「ads.ads_产销存月报 的产量怎么来的？」返回结构化结果 + 回执\n- 可用 `tests/fixtures/kernel-probe-2026-09-23.json` 做无内核测试\n- `gate` 绿')"

create_issue "M1-04 模型网关（薄层）" "M1-垂直切片,角色-后端Python,size-M" "$(mk \
  '统一 OpenAI 兼容入口，接 DeepSeek；换模型只改配置。' \
  '- 网关服务：统一 chat 接口 + 配置化 base_url/model\n- 超时/重试/限流占位 + 调用记账字段（token 数）\n- `.env.example` 更新' \
  '- 不做提示词工程、不做多轮对话编排\n- 不把 key 写进仓库' \
  '- 换模型只需改配置（给一段配置 diff 证明）\n- 网关不可达时**明确报错**，不静默失败（本项目不保留无模型降级）')"

create_issue "M1-05 出口事实校验器（含中文用例）" "M1-垂直切片,角色-测试验收,类型-安全,size-M" "$(mk \
  '让"模型不许编"成为机制：只允许用回执里出现过的表名/字段名/数字组答案。' \
  '- `packages/dip-contracts/src/dip_contracts/guards.py`：入参（模型文本 + 回执白名单）→ 越界即拦\n- 测试：3 正例 + 3 反例（编造表名 / 编造字段 / 编造数字）\n- 中文标识符用例（表名含中文，如 `ads.ads_产销存月报`）' \
  '- 不依赖具体模型实现（纯函数最好测）\n- 不重复实现 status 推导（复用 `dip_contracts/status.py`）' \
  '- 反例全部被拦（有测试）\n- **中文标识符必须拦得住**：用 ASCII 正则会漏检全部中文标识符，测试会假绿\n- `gate` 绿')"

create_issue "M1-06 端到端串起来（一条命令）" "M1-垂直切片,角色-测试验收,size-S" "$(mk \
  '一条命令起 dsh + skill + 网关，走完 M1 的验收问题，输出留证。' \
  '- `ops/demo-m1.sh`\n- 输出存 `docs/evidence/m1-*.txt`\n- 失败时打印每一步的实际返回，便于定位' \
  '- 不做全栈一键（那是 M4-03）' \
  '- 新机器上一条命令跑通 M1 验收问题\n- 证据文件里能看到：答案 + 3 条凭证 + 回执耗时')"

create_issue "M2-01 防火墙服务（独立进程）" "M2-安全三件,角色-后端Python,类型-安全,size-M" "$(mk \
  '把风险判定做成独立服务：三档判定 + 令牌校验，策略表可配置。' \
  '- HTTP 服务：`judge`（身份/权限/档位/处置）与 `verify`（令牌校验）\n- 策略表（YAML）：动作 → 档位，含 `default_tier`\n- `docs/collaboration/notes/firewall.md`：三档定义与策略怎么改' \
  '- 不做审批流转引擎与多人会签（P1 只保留"上一级确认一次"）\n- 不接真实调度平台写操作' \
  '- 三档各 1 例：只读自动通过 / 生产写需审批 / `truncate`·`drop` 拒绝\n- **拿不准按上一档处理**（fail-closed）')"

create_issue "M2-02 审批令牌（一次性 + 绑指纹 + 时效）" "M2-安全三件,角色-后端Python,类型-安全,size-S" "$(mk \
  '审批通过的令牌必须不可复用、绑具体动作、限时。' \
  '- 令牌签发与校验：绑定动作指纹（如 表名 + SQL hash）、5 分钟时效、一次性\n- 测试：二次使用被拒' \
  '- 不做令牌刷新与续期' \
  '- **同一令牌第二次使用被拒**（有测试）\n- 令牌跨动作不可用（改一个字符即失效）')"

create_issue "M2-03 防火墙判定落库（审计）" "M2-安全三件,角色-后端Python,类型-安全,size-S" "$(mk \
  '每次判定都留痕：谁、什么动作、依据、结果、审批人。' \
  '- 复用 `dip-pg`：新增判定记录（动作 / 档位 / 依据 / 结果 / 审批人 / 时间）\n- `/api/audit` 增加判定视图' \
  '- 不改动既有会话与问答审计表结构（只新增）' \
  '- 四次判定（自动/审批/拒绝/令牌）都能在接口里查到\n- DB 不可用时判定**不受影响**，但要显式提示"未落库"')"

create_issue "M2-04 中文动作名策略用例" "M2-安全三件,角色-测试验收,类型-安全,size-S" "$(mk \
  '修掉一个已被实测到的危险失效模式：中文动作名匹配不上策略，静默落到默认档。' \
  '- 至少 1 条中文动作策略用例（如"上线工作流到生产"）\n- 正则改为 Unicode 感知/结构化匹配，不依赖 `\\b` 词边界' \
  '- 不为这一条写特例分支' \
  '- 中文动作**必须命中策略**，不得落到默认档\n- 附带一个反例测试：策略未命中时必须显式记录"未命中"（不能静默）')"

create_issue "M2-05 未命中默认档显式化" "M2-安全三件,角色-后端Python,类型-安全,size-S" "$(mk \
  '策略没命中时的行为必须显式声明、显式留痕。' \
  '- 策略表 `default_tier` 字段 + 加载时校验（缺失即报错）\n- 判定结果里标注 `matched=false` 与原因' \
  '- 不做策略热更新与灰度' \
  '- 未命中时按声明档处理，且日志/审计写明"策略未命中，按默认档 X 处理"')"

create_issue "M3-01 候选池与审核流" "M3-知识双通道,角色-数据知识,size-M" "$(mk \
  '知识回写必须过审：候选 → 责任人审核 → 入库。' \
  '- `knowledge_candidates` 表 + 提交/审核/入库接口\n- 价值判断入口（"值得留下"由用户确认）\n- 测试：无来源脚本的候选拒绝入库' \
  '- 不做审核台 UI' \
  '- **没有来源脚本的口径一律不许入库**（有测试）\n- 候选被拒时说明原因（缺来源/格式非法）')"

create_issue "M3-02 口径版本与回滚" "M3-知识双通道,角色-数据知识,size-M" "$(mk \
  '入库的口径要能追溯历史并回滚。' \
  '- 版本号规则 + 历史表 + 回滚接口\n- 与 `/kb/metric` 的联合验证' \
  '- 不做全量知识库迁移' \
  '- 回滚后 `/kb/metric` 返回上一版本\n- 每条口径能指到来源脚本（行号缺失时标注"文件级"）')"

create_issue "M3-03 冲突仲裁" "M3-知识双通道,角色-数据知识,size-S" "$(mk \
  '同字段两条口径公式必须择一，不允许并存。' \
  '- 冲突检测 + 仲裁入口 + 冲突说明\n- 测试：提交冲突候选时的行为' \
  '- 不做自动仲裁（必须人决定）' \
  '- 冲突候选被拒并列出冲突项\n- 仲裁后只保留一条生效版本，历史保留')"

create_issue "M3-04 文档通道（WeKnora）与「仅背景」约束" "M3-知识双通道,角色-数据知识,size-M" "$(mk \
  '部署 WeKnora 承接文档型知识，并硬约束它不得进入结论链路。' \
  '- WeKnora 部署（Docker）+ 官方 MCP server 接入\n- 调用约束：文档通道结果只能作为"背景说明"，不得产出结论数字\n- 测试：问业务口径时结构化通道优先' \
  '- 不让 WeKnora 写正式口径、不让它替代结构化通道' \
  '- 结构化通道与文档通道同时命中时，结论只来自结构化通道（有测试断言）\n- 文档通道的引用必须标注来源')"

create_issue "M3-05 知识提炼流水线（先一个项目、只出报告）" "M3-知识双通道,角色-数据知识,size-M" "$(mk \
  '从脚本/任务流提炼候选口径：解析 → LLM 初筛 → 规则校验 → 候选池。' \
  '- 流水线脚本 + 报告输出（含每个候选的来源与置信度）\n- 人工抽检记录模板' \
  '- 不做全量初始化（先 1 个项目、**只出报告不落库**）' \
  '- 一个项目跑通并出报告\n- 人工抽检记录留档，合格后才走候选池入库')"

create_issue "M4-01 renderer 插件机制" "M4-前端与演示,角色-前端Node,size-M" "$(mk \
  '前端按 skill 声明的 renderer 分派视图，可插拔。' \
  '- 视图注册表 + 四种视图（table / graph / sql / diff）\n- 复用现有对话前端（静态迭代版）作为宿主' \
  '- 不重做视觉设计（沿用《设计说明书》的令牌与组件层）' \
  '- 四种视图各有一次真实渲染\n- 新增视图不改宿主逻辑（只注册）')"

create_issue "M4-02 对话接入 dsh 壳" "M4-前端与演示,角色-前端Node,size-M" "$(mk \
  '在 dsh 的 Web 壳里问业务问题，走通我们的 skill 与渲染。' \
  '- 壳体接入 + 一段演示录屏\n- 与 M1-06 的端到端脚本对齐' \
  '- 不做多用户与权限' \
  '- 截图/录屏可复现\n- 失败路径有明确提示（模型不可用 / skill 报错）')"

create_issue "M4-03 一键起全栈 + 一键验收" "M4-前端与演示,角色-测试验收,size-M" "$(mk \
  '新机器一条命令起全栈并跑完验收路径。' \
  '- `ops/demo.sh`（PG + 内核 + 平台 + dsh）\n- `ops/gate.sh --demo`\n- 演示路径文档' \
  '- 不追求生产级部署（不做编排/K8s）' \
  '- 新机器上一条命令跑完《v2 项目规划》§5 的 7 步验收路径\n- 每步留证到 `docs/evidence/`')"

create_issue "M4-04 演示资产与存证" "M4-前端与演示,角色-测试验收,size-S" "$(mk \
  '把可演示的东西固化成资产：点击路径、截图、录屏、证据目录。' \
  '- 点击路径文档（逐步写清）+ 截图/录屏\n- `docs/evidence/` 结构约定' \
  '- 不做宣传物料' \
  '- 换一台机器按文档能复现\n- 反例证据（被拦的动作、被拦的编造）必须保留')"

echo
echo "全部完成。查看：https://github.com/${REPO}/issues"
