"""命令行入口：`python -m dip_refine --project <目录> --out <目录>`（工作项 M3-05 / Issue #16）。

只做三件事：起适配器（内核客户端 + 模型网关客户端）→ 跑流水线 → 写报告。

**这里没有任何写操作**：不提交候选、不写知识库（Issue 边界：只出报告不落库）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from dip_core import load_settings
from lineage_client import LineageClient

from .parse import SqlParser
from .pipeline import run
from .report import write_report
from .screen import GatewayLlm


def build_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m dip_refine", description="知识提炼流水线（只出报告，不落库）")
    parser.add_argument("--project", required=True, help="要提炼的项目目录（会递归找 *.sql）")
    parser.add_argument("--project-root", default=None,
                        help="来源路径相对谁记录（默认同 --project；一般填仓库根，便于与内核 source_file 对齐）")
    parser.add_argument("--out", default="docs/evidence/refine", help="报告输出目录")
    parser.add_argument("--dialect", default="hive")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个脚本（先小步试跑用）")
    parser.add_argument("--sample", type=int, default=10, help="人工抽检建议的条数")
    parser.add_argument("--gateway", default="http://127.0.0.1:18200", help="平台模型网关地址")
    parser.add_argument("--model", default=None, help="模型名（默认取 LLM_MODEL）")
    parser.add_argument("--stem", default="refine-report", help="报告文件名前缀")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = build_args(argv)
    settings = load_settings()
    model = args.model or settings.llm_model
    if not model:
        print("缺少模型名：设置 LLM_MODEL 或用 --model 指定（这一步必须有真实模型，不做规则凑数）",
              file=sys.stderr)
        return 2

    project = pathlib.Path(args.project)
    out_dir = pathlib.Path(args.out)
    root = pathlib.Path(args.project_root) if args.project_root else project

    # 内核客户端满足提炼需要的窄协议（只用到 parse）；模型只经平台网关
    parser: SqlParser = LineageClient(settings.kernel_base_url, retries=settings.kernel_retries)
    screener = GatewayLlm(args.gateway, model=model, api_key=settings.llm_api_key,
                          timeout=max(60.0, settings.llm_timeout), max_attempts=settings.llm_max_attempts)

    print(f"提炼：{project}（来源相对 {root}）｜模型 {model}｜网关 {args.gateway}")
    try:
        report = run(project, parser=parser, screener=screener, project_root=root,
                     dialect=args.dialect, limit=args.limit, sample_size=args.sample)
    except Exception as exc:  # noqa: BLE001 - 失败就明说，不产出半成品报告
        print(f"提炼失败，未产出报告：{exc}", file=sys.stderr)
        return 1

    md_path, json_path = write_report(report, out_dir, stem=args.stem)
    print(f"脚本 {len(report.parsed_ok)}/{len(report.scripts)} 个解析成功，"
          f"LLM 调用 {report.llm_calls} 次，候选 {len(report.candidates)} 条"
          f"（通过校验 {len(report.ok_candidates)} / 有问题 {len(report.needs_human)}）")
    if report.incomplete:
        print(f"注意：本报告不完整（{len(report.errors)} 处失败），别据此入库")
    print(f"报告：{md_path}")
    print(f"      {json_path}")
    print("下一步：人工抽检（模板 templates/人工抽检记录.md），合格后才提交候选 —— 本流水线不落库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
