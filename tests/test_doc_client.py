"""文档通道客户端的解析测试（M3-04 / #15）—— 全离线，不发任何网络请求。

要钉的是**解析的诚实性**：

- 字段名容错（不同版本/不同工具返回的键不一样），但**来源缺了就缺了**，
  绝不用 id 冒充文档名 —— 那会让"引用标注"变成假的，后面 `background_of()` 检不出来；
- MCP 返回体的三种形态（结构化内容 / 文本里的 JSON / 纯文本）都要认得；
- 配置没配齐就明确不启用（返回 None / 抛 `DocChannelNotConfigured`），不静默给个空通道。
"""

from __future__ import annotations

import json

import pytest
from dip_core import load_settings
from dip_docs import (
    DocChannelNotConfigured,
    WeknoraMcpChannel,
    channel_from_settings,
    parse_documents_payload,
    parse_search_payload,
    passage_to_hit,
    passages_of,
    payload_of_tool_result,
)


def _settings(**env: str):  # noqa: ANN202
    base = {"DOCS_ENABLED": "true", "WEKNORA_MCP_URL": "http://127.0.0.1:18300/mcp/e1",
            "WEKNORA_MCP_TOKEN": "tok-123", "WEKNORA_KB_IDS": "kb-1, kb-2", "WEKNORA_TOP_K": "3"}
    base.update(env)
    return load_settings(base)


# ---------------------------------------------------------------- 解析


def test_认结构化内容():
    payload = {"results": [{"knowledge_id": "d1", "knowledge_title": "产销存月报口径说明.md",
                            "chunk_id": "c1", "excerpt": "库存增量 = 产量 − 销量", "score": 0.8}]}
    hits = parse_search_payload(payload)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.citation.document_name == "产销存月报口径说明.md"
    assert hit.citation.chunk_id == "c1"
    assert hit.text == "库存增量 = 产量 − 销量"
    assert hit.score == pytest.approx(0.8)


def test_认扁平列表与单条():
    flat = [{"document_id": "d2", "title": "产量口径.md", "chunk": "c2", "content": "产量含打码/跳码/重码"}]
    assert parse_search_payload(flat)[0].citation.document_name == "产量口径.md"
    single = {"chunk_id": "c3", "name": "税利口径.md", "text": "单箱税利 = 税利 / 产量"}
    assert parse_search_payload(single)[0].citation.chunk_id == "c3"


def test_只有id没有标题时不许拿id冒充文档名():
    hits = parse_search_payload({"results": [{"knowledge_id": "d9", "chunk_id": "c9", "excerpt": "……"}]})
    assert hits[0].citation.document_name == ""       # 留空 → 契约层会拒收
    assert hits[0].citation.knowledge_id == "d9"      # id 仍带出来，便于人追


def test_用文档清单补标题():
    payload = {"results": [{"knowledge_id": "d9", "chunk_id": "c9", "excerpt": "……"}]}
    titles = {"d9": "税利汇总口径.md"}
    hits = parse_search_payload(payload, titles)
    assert hits[0].citation.document_name == "税利汇总口径.md"


def test_文档清单解析():
    payload = {"documents": [{"knowledge_id": "d1", "title": "A.md"}, {"id": "d2", "file_name": "B.md"},
                             {"knowledge_id": "d3"}]}
    assert parse_documents_payload(payload) == {"d1": "A.md", "d2": "B.md"}


def test_位置与链接有就带():
    hit = passage_to_hit({"chunk_id": "c1", "title": "A.md", "page": 3,
                          "url": "http://127.0.0.1:18380/doc/d1"})
    assert hit.citation.position == "3"
    assert hit.citation.url == "http://127.0.0.1:18380/doc/d1"


def test_认不出的返回体不装样子():
    assert passages_of("这不是 JSON") == []
    assert passages_of({"unexpected": 1}) == []
    assert parse_search_payload(None) == []


# ---------------------------------------------------------------- WeKnora 的 XML 形态（真跑抄回来的）

# 这段是从真跑里原样抄回来的（脱敏：id 保留形状，正文截断），
# 免得以后 WeKnora 换了输出格式我们还以为解析器是对的。
SEARCH_XML = """<search_results count="2" mode="hybrid">
<query>库存增量怎么算</query>
<chunk rank="1" chunk_id="65db8f5e-90d7-4f02-8d7e-dda82a9a7329" chunk_index="2" \
knowledge_id="3f4c5279-7736-4d85-bc05-5adcf5ff26f2" \
knowledge_base_id="edb993b3-9c28-4012-acb6-7085bc5965a5" \
knowledge_title="产销存月报口径说明.md" score="0.016">
<match_snippet>产销存月报的业务背景说明，解释产量、销量、库存增量等指标含义。</match_snippet>
<content>&gt; 举例说明（**只是举例，不是结论**）：某厂 2025 年库存增量约占产量的 37%。</content>
</chunk>
<chunk rank="2" chunk_id="02c2d7d4-6d99-4028-b2b1-7c8c7f30fc19" chunk_index="1" \
knowledge_id="3f4c5279-7736-4d85-bc05-5adcf5ff26f2" \
knowledge_base_id="edb993b3-9c28-4012-acb6-7085bc5965a5" \
knowledge_title="产销存月报口径说明.md" score="0.0157">
<content>产量：打码量 + 跳码量 − 重码量。</content>
</chunk>
</search_results>"""


def test_认真实XML返回():
    hits = parse_search_payload(SEARCH_XML)
    assert len(hits) == 2
    first, second = hits
    assert first.citation.document_name == "产销存月报口径说明.md"
    assert first.citation.chunk_id == "65db8f5e-90d7-4f02-8d7e-dda82a9a7329"
    assert first.citation.knowledge_id == "3f4c5279-7736-4d85-bc05-5adcf5ff26f2"
    assert first.citation.knowledge_base_id == "edb993b3-9c28-4012-acb6-7085bc5965a5"
    assert first.citation.position == "2"          # chunk_index
    assert first.score == pytest.approx(0.016)
    assert second.score == pytest.approx(0.0157)
    # 正文用 <content>（完整切片），不是 <match_snippet>
    assert "37%" in second.text or "37%" in first.text
    assert first.text.startswith("> 举例说明")      # &gt; 要还原成 >
    assert "某厂 2025 年库存增量约占产量的 37%" in first.text


def test_XML单条也能被契约层接受():
    from dip_contracts.doc_channel import background_of

    background = background_of(parse_search_payload(SEARCH_XML))
    assert [hit.citation.label for hit in background.hits] == [
        "产销存月报口径说明.md#65db8f5e-90d7-4f02-8d7e-dda82a9a7329",
        "产销存月报口径说明.md#02c2d7d4-6d99-4028-b2b1-7c8c7f30fc19",
    ]


def test_XML自闭合标签也认():
    payload = '<documents count="1"><document id="d1" title="A.md" file_name="A.md"/></documents>'
    assert parse_documents_payload(payload) == {"d1": "A.md"}


def test_服务端的错误文本不会装成一条材料():
    """`list_documents` 没给 knowledge_base_id 时服务端回一句说明 —— 那是"补不到名字"，不是材料。"""
    assert passages_of("knowledge_base_id is required") == []
    assert parse_documents_payload("knowledge_base_id is required") == {}


def test_正文里的XML特殊字符不至于把整段解崩():
    payload = ('<chunk chunk_id="c1" knowledge_title="A.md">'
               '<content>条件是 a &lt; b 且 c &gt; d，还有裸的 & 符号</content></chunk>')
    hit = parse_search_payload(payload)[0]
    assert hit.text == "条件是 a < b 且 c > d，还有裸的 & 符号"


# ---------------------------------------------------------------- MCP 返回体


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, *, structured=None, content=None) -> None:
        self.structuredContent = structured
        self.content = content or []


def test_优先结构化内容():
    structured = {"results": [{"chunk_id": "c1", "title": "A.md", "excerpt": "x"}]}
    payload = payload_of_tool_result(_Result(structured=structured, content=[_Block("忽略我")]))
    assert payload == structured


def test_文本里的JSON也认_含代码围栏():
    body = {"results": [{"chunk_id": "c1", "title": "A.md", "excerpt": "x"}]}
    fenced = "```json\n" + json.dumps(body, ensure_ascii=False) + "\n```"
    assert payload_of_tool_result(_Result(content=[_Block(fenced)])) == body


def test_纯文本退回原样():
    assert payload_of_tool_result(_Result(content=[_Block("没有 JSON 的一段话")])) == "没有 JSON 的一段话"


# ---------------------------------------------------------------- 配置


def test_配置没开就不造通道():
    assert channel_from_settings(load_settings({})) is None
    assert channel_from_settings(_settings(DOCS_ENABLED="false")) is None
    assert channel_from_settings(_settings(WEKNORA_MCP_TOKEN="")) is None


def test_配置齐了就按配置造():
    channel = channel_from_settings(_settings())
    assert isinstance(channel, WeknoraMcpChannel)
    assert channel.url.endswith("/mcp/e1")
    assert channel.kb_ids == ("kb-1", "kb-2")
    assert channel.limit == 3
    described = channel.describe()
    assert described["tool_group"] == "retrieve"
    assert "tok-123" not in json.dumps(described)      # 令牌不许出现在任何可打印的地方


def test_直接构造时缺参就明确报错():
    with pytest.raises(DocChannelNotConfigured):
        WeknoraMcpChannel("", "tok")
    with pytest.raises(DocChannelNotConfigured):
        WeknoraMcpChannel("http://127.0.0.1:18300/mcp/e1", "")
