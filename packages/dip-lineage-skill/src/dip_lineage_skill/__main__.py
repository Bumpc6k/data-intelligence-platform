"""进程入口：把真实内核客户端接上，按 MCP 协议提供服务（工作项 M1-03 / M4-02）。

两种传输方式：

    # 1) stdio（默认；外壳自己起这个进程，M1 就是这么用的）
    LINEAGE_BASE=http://127.0.0.1:18080 .venv/bin/python -m dip_lineage_skill

    # 2) Streamable HTTP（M4-02 / #18 加：dsh 跑在 Windows、skill 跑在 WSL，
    #    跨窗口起进程太脆，直接让它监听一个口更省事）
    LINEAGE_BASE=http://127.0.0.1:18080 .venv/bin/python -m dip_lineage_skill --transport http --port 18360

外壳（dsh 等）按 MCP 约定连上来即可。也可以全用环境变量给（`DIP_SKILL_TRANSPORT=
stdio|http`、`DIP_SKILL_HOST`、`DIP_SKILL_PORT`），命令行优先。

**日志一律走 stderr**：stdio 模式下 stdout 是 MCP 的协议通道，往里多写一个字符就会破坏协议，
外壳会直接握手失败。所以这里 `logging.basicConfig(stream=sys.stderr)`，且库代码里不用 print。
（HTTP 模式没有这个限制，但保持一致，省得两种模式行为不一样。）
"""

from __future__ import annotations

import logging
import os
import sys

from lineage_client import DEFAULT_BASE_URL, LineageClient

from .mcp_server import SERVER_NAME, build_server

logger = logging.getLogger(SERVER_NAME)

#: HTTP 模式默认监听地址。**默认只绑 loopback**：这是本机演示用的口子，不该暴露到局域网。
DEFAULT_HOST = "127.0.0.1"
#: 端口沿用本项目 183xx 的约定（18300 WeKnora / 18360 本 skill）
DEFAULT_PORT = 18360
#: HTTP 模式的 MCP 路径（SDK 默认就是 /mcp，显式写出来让连接方一眼能看到）
MCP_PATH = "/mcp"

USAGE = f"""用法：
  python -m dip_lineage_skill                          # stdio（默认，外壳起进程）
  python -m dip_lineage_skill --transport http [--host H] [--port P]

环境变量：
  LINEAGE_BASE         内核地址，默认 {DEFAULT_BASE_URL}
  DIP_SKILL_TRANSPORT  stdio | http，默认 stdio（命令行 --transport 优先）
  DIP_SKILL_HOST       HTTP 模式监听地址，默认 {DEFAULT_HOST}
  DIP_SKILL_PORT       HTTP 模式监听端口，默认 {DEFAULT_PORT}
  DIP_LOG_LEVEL        日志级别，默认 INFO

HTTP 模式的 MCP 端点是 http://<host>:<port>{MCP_PATH}
"""


def parse_options(argv: list[str] | None = None) -> dict[str, object]:
    """把命令行与环境变量合成一份配置（纯函数，方便测）。

    规则：**命令行 > 环境变量 > 默认值**；认不出的参数直接报错，不静默忽略
    （静默忽略会让人以为"我明明传了"，结果跑的是另一套配置）。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    env = os.environ

    transport = str(env.get("DIP_SKILL_TRANSPORT", "stdio")).strip().lower()
    host = str(env.get("DIP_SKILL_HOST", DEFAULT_HOST))
    port = int(env.get("DIP_SKILL_PORT", DEFAULT_PORT))

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            return {"help": True}
        if arg == "--transport":
            i += 1
            if i >= len(args):
                raise ValueError("--transport 需要给值：stdio 或 http")
            transport = args[i].strip().lower()
        elif arg.startswith("--transport="):
            transport = arg.split("=", 1)[1].strip().lower()
        elif arg == "--host":
            i += 1
            if i >= len(args):
                raise ValueError("--host 需要给值")
            host = args[i]
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg == "--port":
            i += 1
            if i >= len(args):
                raise ValueError("--port 需要给值")
            port = int(args[i])
        elif arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
        else:
            raise ValueError(f"认不出的参数：{arg}（-h 看用法）")
        i += 1

    if transport not in {"stdio", "http"}:
        raise ValueError(f"--transport 只认 stdio 或 http，收到：{transport}")

    return {"help": False, "transport": transport, "host": host, "port": port}


def main(argv: list[str] | None = None) -> int:
    try:
        options = parse_options(argv)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    if options["help"]:
        print(USAGE)
        return 0

    transport = str(options["transport"])
    base_url = os.environ.get("LINEAGE_BASE", DEFAULT_BASE_URL)
    logging.basicConfig(level=os.environ.get("DIP_LOG_LEVEL", "INFO"), stream=sys.stderr)
    logger.info("启动 %s（transport=%s），内核地址 %s", SERVER_NAME, transport, base_url)

    with LineageClient(base_url) as client:
        server = build_server(client)
        if transport == "http":
            host = str(options["host"])
            port = int(str(options["port"]))
            logger.info("MCP Streamable HTTP 端点：http://%s:%d%s", host, port, MCP_PATH)
            server.run(transport="streamable-http", host=host, port=port)
        else:
            server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
