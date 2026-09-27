"""进程入口：把真实内核客户端接上，按 MCP stdio 协议提供服务（工作项 M1-03）。

用法：

    LINEAGE_BASE=http://127.0.0.1:18080 .venv/bin/python -m dip_lineage_skill

外壳（dsh 等）按 MCP 约定用 stdio 启动这个进程即可，不需要额外参数。

**日志一律走 stderr**：stdout 是 MCP 的协议通道，往里多写一个字符就会破坏协议，
外壳会直接握手失败。所以这里 `logging.basicConfig(stream=sys.stderr)`，且库代码里不用 print。
"""

from __future__ import annotations

import logging
import os
import sys

from lineage_client import DEFAULT_BASE_URL, LineageClient

from .mcp_server import SERVER_NAME, build_server

logger = logging.getLogger(SERVER_NAME)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print("用法：python -m dip_lineage_skill    # 按 MCP stdio 协议提供服务（外壳来起它）")
        print(f"环境变量 LINEAGE_BASE 指定内核地址，默认 {DEFAULT_BASE_URL}")
        return 0

    base_url = os.environ.get("LINEAGE_BASE", DEFAULT_BASE_URL)
    logging.basicConfig(level=os.environ.get("DIP_LOG_LEVEL", "INFO"), stream=sys.stderr)
    logger.info("启动 %s，内核地址 %s", SERVER_NAME, base_url)

    with LineageClient(base_url) as client:
        build_server(client).run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
