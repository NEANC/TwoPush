#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""验证 OnePush Server酱 通道 + 钉钉内部通道的数据传递能力

Server酱 是一个多通道消息中转服务。用户在 Server酱 后台配置「钉钉群机器人」
通道后，通过 Server酱 API 发送的消息会由 Server酱 服务端转发到钉钉 Webhook。

本测试验证：
1. OnePush ServerChan 提供者的参数能力
2. Server酱 API 的 channel 参数与钉钉通道映射
3. 数据经过 Server酱 → 钉钉 中转后的能力保留与丢失

本文件的自动化测试验证 OnePush 本地请求数据结构；Server酱 → 钉钉的真实投递结论来自独立的人工验证记录。
"""

import json
import os
import sys
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from onepush import get_notifier
from onepush.providers.serverchan import ServerChan

TEST_IMAGE_URL = "https://example.com/test.png"


class TestServerChanProviderParams:
    """测试 OnePush ServerChan 提供者的参数定义"""

    def test_provider_name(self):
        """提供者名称应为 serverchan"""
        notifier = get_notifier("serverchan")
        assert notifier.name == "serverchan"

    def test_required_params(self):
        """必填参数应包含 sckey 和 title"""
        notifier = get_notifier("serverchan")
        assert "sckey" in notifier.params["required"]
        assert "title" in notifier.params["required"]

    def test_optional_params(self):
        """可选参数应包含 content"""
        notifier = get_notifier("serverchan")
        assert "content" in notifier.params["optional"]

    def test_no_channel_param(self):
        """OnePush 不支持 channel 参数，无法动态指定钉钉通道"""
        notifier = get_notifier("serverchan")
        optional = notifier.params["optional"]
        assert "channel" not in optional, (
            "OnePush 不支持 Server酱 channel 参数，"
            "需通过 Server酱 后台固定配置钉钉通道"
        )

    def test_no_at_param(self):
        """OnePush 不支持 at/mention 相关参数"""
        notifier = get_notifier("serverchan")
        optional = notifier.params["optional"]
        at_related = [p for p in optional
                      if any(kw in p.lower() for kw in ("at", "mention", "mobile"))]
        assert len(at_related) == 0, (
            "Server酱 API 本身不支持 @ 功能参数"
        )

    def test_no_markdown_toggle(self):
        """OnePush 不支持 markdown 切换参数（Server酱 desp 默认支持 Markdown）"""
        notifier = get_notifier("serverchan")
        optional = notifier.params["optional"]
        assert "markdown" not in optional, (
            "Server酱 desp 字段默认支持 Markdown，无需显式开关"
        )


class TestServerChanDataStructure:
    """测试 OnePush ServerChan 数据构造"""

    def test_basic_data_mapping(self):
        """title → text, content → desp 映射"""
        sc = ServerChan()
        sc._prepare_data(title="测试标题", content="测试内容")
        assert sc.data == {"text": "测试标题", "desp": "测试内容"}

    def test_content_only(self):
        """仅 content 无 title 时 text 保持为 None，不做拼接"""
        sc = ServerChan()
        sc._prepare_data(title=None, content="内容")
        assert sc.data == {"text": None, "desp": "内容"}

    def test_url_construction(self):
        """URL 使用旧版 API 端点 sc.ftqq.com"""
        sc = ServerChan()
        url = sc._prepare_url(sckey="SCT123456")
        assert "sc.ftqq.com" in url
        assert "SCT123456.send" in url

    def test_url_uses_old_api_not_new(self):
        """OnePush 使用旧版 sc.ftqq.com，不是新版 sctapi.ftqq.com"""
        sc = ServerChan()
        url = sc._prepare_url(sckey="SCT123456")
        assert "sctapi.ftqq.com" not in url, (
            "OnePush 当前使用旧版 API (sc.ftqq.com)，"
            "新版 API 端点 (sctapi.ftqq.com) 未支持"
        )
        assert "sc.ftqq.com" in url

    def test_no_channel_in_data(self):
        """构造的数据中不包含 channel 字段"""
        sc = ServerChan()
        sc._prepare_data(title="标题", content="内容")
        assert "channel" not in sc.data, (
            "OnePush 不构造 channel 参数，钉钉通道需在 Server酱 后台配置"
        )


class TestServerChanMarkdownCapabilities:
    """测试 OnePush 保留 Server酱 desp 字段中的 Markdown 内容"""

    def test_markdown_headings(self):
        """OnePush 不改写 desp 中的 Markdown 标题"""
        sc = ServerChan()
        sc._prepare_data(
            title="标题测试",
            content="# 一级标题\n\n## 二级标题\n\n### 三级标题",
        )
        assert "# 一级标题" in sc.data["desp"]
        assert "## 二级标题" in sc.data["desp"]

    def test_markdown_bold_italic(self):
        """OnePush 不改写 desp 中的加粗和斜体标记"""
        sc = ServerChan()
        sc._prepare_data(
            title="文字测试",
            content="**加粗** *斜体* **加粗 *嵌套* 斜体**",
        )
        assert "**加粗**" in sc.data["desp"]
        assert "*斜体*" in sc.data["desp"]

    def test_markdown_quote(self):
        """OnePush 不改写 desp 中的引用标记"""
        sc = ServerChan()
        sc._prepare_data(
            title="引用测试",
            content="> 这是一段引用文字。\n> 多行引用。",
        )
        assert "> 这是一段引用文字。" in sc.data["desp"]

    def test_markdown_link(self):
        """OnePush 不改写 desp 中的链接标记"""
        sc = ServerChan()
        sc._prepare_data(
            title="链接测试",
            content="[Server酱](https://sct.ftqq.com/) | [钉钉](https://open.dingtalk.com/)",
        )
        assert "[Server酱](https://sct.ftqq.com/)" in sc.data["desp"]

    def test_markdown_image(self):
        """OnePush 不改写 desp 中的 Markdown 图片语法

        真实图片渲染效果需要通过网络集成测试或人工发送确认。
        """
        sc = ServerChan()
        sc._prepare_data(
            title="图片测试",
            content=f"![图片]({TEST_IMAGE_URL})",
        )
        assert f"![图片]({TEST_IMAGE_URL})" in sc.data["desp"]

    def test_markdown_unordered_list(self):
        """OnePush 不改写 desp 中的无序列表标记"""
        sc = ServerChan()
        sc._prepare_data(
            title="列表测试",
            content="- 项一\n- 项二\n- 项三",
        )
        assert "- 项一" in sc.data["desp"]

    def test_markdown_ordered_list(self):
        """OnePush 不改写 desp 中的有序列表标记"""
        sc = ServerChan()
        sc._prepare_data(
            title="列表测试",
            content="1. 第一步\n2. 第二步\n3. 第三步",
        )
        assert "1. 第一步" in sc.data["desp"]

    def test_full_markdown_content(self):
        """OnePush 可将完整 Markdown 原样放入 desp 字段"""
        content = (
            "# OnePush → Server酱 → 钉钉\n\n"
            "## 能力测试\n\n"
            "**加粗** | *斜体*\n\n"
            "> 引用文字\n\n"
            "[链接](https://example.com)\n\n"
            f"![图片]({TEST_IMAGE_URL})\n\n"
            "- 无序列表\n\n"
            "1. 有序列表"
        )
        sc = ServerChan()
        sc._prepare_data(title="全量测试", content=content)
        assert "# OnePush → Server酱 → 钉钉" in sc.data["desp"]
        assert f"![图片]({TEST_IMAGE_URL})" in sc.data["desp"]
        assert "**加粗**" in sc.data["desp"]


class TestServerChanAtCapabilities:
    """测试 ServerChan 本地数据中不包含钉钉 @ 字段

    真实 @ 效果来自人工集成验证；本类仅验证 OnePush 本地数据结构。
    """

    def test_no_at_field_in_data(self):
        """Server酱 API 不支持 at 字段，无法传递 @ 信息到钉钉"""
        sc = ServerChan()
        sc._prepare_data(
            title="测试",
            content="@所有人 测试消息 @138xxxx1234 请查收",
        )
        assert "at" not in sc.data
        assert "atMobiles" not in sc.data
        assert "atUserIds" not in sc.data
        # @ 文本仅作为普通文本出现在 desp 中
        assert "@所有人" in sc.data["desp"]
        assert "@138xxxx1234" in sc.data["desp"]

    def test_at_text_only_rendered_not_triggered(self):
        """desp 中的 @ 文本仅渲染显示，不会触发钉钉 @ 通知

        原因：Server酱 API 没有 at 字段，而钉钉 @ 需要同时满足
        (1) content/text 中有 @文本 且 (2) at 字段正确设置。
        Server酱 代理无法构造缺失的 at 字段。
        """
        sc = ServerChan()
        sc._prepare_data(
            title="通知",
            content="## 紧急通知\n\n@所有人 请立即查看！",
        )
        assert "@所有人" in sc.data["desp"]
        # 但 Server酱 不会为钉钉构造 at 字段
        assert "at" not in sc.data


class TestServerChanNotifyFlow:
    """测试完整的 notify 流程（mock 请求）"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.sc = ServerChan()
        self.mock_response = unittest.mock.MagicMock()
        self.mock_response.text = '{"code": 0, "message": "", "data": {"pushid": "123"}}'

    def test_notify_basic_mocked(self, monkeypatch):
        """通过 mock 验证 OnePush 基础请求流程"""
        captured_data = {}

        def mock_request(self_or_method, method_or_url, *args, **kwargs):
            """兼容静态方法替换后的实例方法调用"""
            captured_data["url"] = method_or_url
            captured_data["data"] = kwargs.get("data", {})
            return self.mock_response

        monkeypatch.setattr(self.sc, "request", mock_request)

        self.sc._prepare_url(sckey="SCT123456")
        self.sc._prepare_data(title="测试", content="内容")
        response = self.sc._send_message()

        assert "sc.ftqq.com" in captured_data["url"]
        assert captured_data["data"]["text"] == "测试"
        assert captured_data["data"]["desp"] == "内容"
        assert response is not None

    def test_notify_markdown_image_mocked(self, monkeypatch):
        """通过 mock 验证 Markdown 图片数据进入请求流程"""
        captured_data = {}

        def mock_request(self_or_method, method_or_url, *args, **kwargs):
            """兼容静态方法替换后的实例方法调用"""
            captured_data["data"] = kwargs.get("data", {})
            return self.mock_response

        monkeypatch.setattr(self.sc, "request", mock_request)

        self.sc._prepare_url(sckey="SCT123456")
        self.sc._prepare_data(
            title="图片测试",
            content=f"![图]({TEST_IMAGE_URL})",
        )
        self.sc._send_message()

        assert TEST_IMAGE_URL in captured_data["data"]["desp"]

    def test_one_push_high_level_notify_mocked(self, monkeypatch):
        """通过高层 notify API 验证（mock）"""
        mock_response = unittest.mock.MagicMock()
        mock_response.text = '{"code": 0}'

        def mock_request(self_or_method, method_or_url, *args, **kwargs):
            return mock_response

        monkeypatch.setattr(ServerChan, "request", mock_request)

        from onepush import notify

        response = notify(
            "serverchan",
            sckey="SCT123456",
            title="测试",
            content=f"![图片]({TEST_IMAGE_URL})",
        )
        assert response is not None
        assert response.text == '{"code": 0}'


class TestServerChanDingTalkChannel:
    """验证 Server酱 钉钉通道的数据传递能力矩阵

    Server酱 作为中转层，通过 channel 参数指定下游通道。当 channel=2（钉钉群机器人）
    时，消息流程为：

    OnePush/API → Server酱 API → Server酱 服务端 → 钉钉 Webhook

    此测试类不实际发送请求，而是基于 API 文档和实测结论，对比「直接调用钉钉」
    与「通过 Server酱 中转」的能力差异。
    """

    def test_serverchan_adds_convenience_but_loses_advanced_features(self):
        """Server酱 提供多通道统一入口，但牺牲了各通道的高级特性

        优点：
        - 一个 SendKey 可同时推送到多个通道（微信 + 钉钉 + 飞书等）
        - 免去各平台单独接入的复杂度
        - 统一管理多个通道的 SendKey

        缺点：
        - 无法使用各平台的 @ 功能
        - 无法使用 link/feedCard/actionCard 等高级消息类型
        - 消息内容受 Server酱 API 字段限制（title + desp）
        """  # noqa: D401
        sc = ServerChan()
        assert sc._params["required"] == ["sckey", "title"]
        assert sc._params["optional"] == ["content"]
        # 参数极简，无法传递高级功能参数


class TestOnePushHighLevelServerChan:
    """测试 OnePush 高层 API 的 ServerChan 调用"""

    def test_get_notifier_returns_serverchan_instance(self):
        """get_notifier('serverchan') 应返回 ServerChan 实例"""
        notifier = get_notifier("serverchan")
        assert isinstance(notifier, ServerChan)

    def test_all_providers_includes_serverchan(self):
        """all_providers() 应包含 serverchan"""
        from onepush import all_providers
        providers = all_providers()
        assert "serverchan" in providers
