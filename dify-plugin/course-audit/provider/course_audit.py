"""工具提供方：本插件不需要任何凭据（全部计算在本地完成）。"""

from __future__ import annotations

from typing import Any

from dify_plugin import ToolProvider


class CourseAuditProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # 无外部依赖、无密钥：配置阶段总是通过
        return None