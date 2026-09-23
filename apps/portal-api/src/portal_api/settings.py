"""后端配置入口（复用 dip-core 的 Settings，避免两处配置真相）。"""

from __future__ import annotations

from dip_core import load_settings

settings = load_settings()
