"""skill 回执：每次 skill 调用都要交代"这次凭什么"（工作项 M1-03）。

回执是**契约层的概念**，不是某个 skill 私有的 —— 将来每个 skill 都要带回执，理由有三：

1. 出口事实校验（M1-05）要按回执里的白名单判断模型是不是在编；
2. 前端「工具步骤条」要显示来源端点与耗时（不许美化）；
3. 审计要留痕：谁在什么时候调了什么、花了多久、拿到几条证据。

所以它放在契约层，并且仍然只依赖 pydantic。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field


class SkillReceipt(BaseModel):
    """一次 skill 调用的回执 —— **失败也要有回执**（失败也是结果，不抛异常）。"""

    model_config = ConfigDict(frozen=True)

    skill: str = Field(description="skill 的 name@version，如 lineage.analyze@1")
    endpoint: str = Field(description="来源端点，如 POST /upstream")
    ms: int = Field(ge=0, description="这次调用的实际耗时（毫秒）")
    evidence_count: int = Field(ge=0, description="本次可引用的证据条数；0 表示没有证据可引")
    ok: bool = Field(default=True, description="这次调用本身是否成功（不代表结论可信）")
    attempts: int = Field(default=1, ge=1, description="实际尝试次数（含重试）")
    http_status: int | None = Field(default=None, description="内核返回的 HTTP 状态码")
    error: str | None = Field(default=None, description="内核原文错误，便于定位是平台还是内核的问题")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable(self) -> bool:
        """能不能拿它去支撑结论：**成功且至少有一条证据**。

        对应铁律 2「无凭证不出结论」——回执存在不等于结论成立，`evidence_count == 0`
        的回执只能支撑"没查到"，不能支撑任何数字或公式。

        用 `computed_field` 而不是普通 property：它必须**出现在序列化结果里**，
        否则外壳拿到的 JSON 里没有这个判断，就只能自己猜规则。
        """
        return self.ok and self.evidence_count > 0
