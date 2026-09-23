"""内核客户端异常体系。

分层原则（对应《B2 接口设计与评审》§1.2-1）：
- **HTTP 200 也可能是失败**：内核用 `{"success": false, "error": "..."}` 表达业务失败，
  所以「HTTP 状态码正常」绝不等于「调用成功」。
- 异常只用于**程序员需要区分处理**的情形；常规失败请读 `ToolResult.ok/error`。
"""

from __future__ import annotations


class KernelError(Exception):
    """内核调用相关错误的基类。"""


class KernelTransportError(KernelError):
    """连不上、连接被重置、超时——**可重试**。"""


class KernelHTTPError(KernelError):
    """HTTP 状态码非 200（如 404/500）——通常不可重试。"""

    def __init__(self, status: int, path: str, body: str = "") -> None:
        self.status = status
        self.path = path
        self.body = body[:400]
        super().__init__(f"HTTP {status} {path}: {self.body}")


class KernelAPIError(KernelError):
    """HTTP 200 但 `success=false`（业务失败），例如参数名写错。"""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path} 业务失败: {message}")
