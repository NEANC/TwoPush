#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""验证 OnePush 能否实现对钉钉推送图片的测试

测试范围：
1. OnePush DingTalk 提供者的参数与消息类型能力
2. Markdown 模式下内嵌图片（变通方案）
3. 钉钉 Webhook 方式不支持 image 消息类型的说明
4. @ 功能（@所有人、@指定人）的数据结构验证
5. 钉钉 Markdown 全语法支持验证
6. 请求数据结构验证
"""

import json
import os
import sys
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from onepush import get_notifier
from onepush.providers.dingtalk import DingTalk

# 测试用的图片 URL（公开可访问的示例图片）
TEST_IMAGE_URL = "https://img.alicdn.com/tfs/TB1NwmBEL9TBuNjy1zbXXXpepXa-2400-1218.png"


class TestDingTalkProviderParams:
    """测试 DingTalk 提供者的参数定义"""

    def test_provider_name(self):
        """提供者名称应为 dingtalk"""
        notifier = get_notifier("dingtalk")
        assert notifier.name == "dingtalk"

    def test_required_params(self):
        """必填参数应包含 token"""
        notifier = get_notifier("dingtalk")
        assert "token" in notifier.params["required"]

    def test_optional_params(self):
        """可选参数应包含 title、content、secret、markdown"""
        notifier = get_notifier("dingtalk")
        optional = notifier.params["optional"]
        assert "title" in optional
        assert "content" in optional
        assert "secret" in optional
        assert "markdown" in optional

    def test_no_image_param(self):
        """可选参数中不应包含 image 或 pic_url，因为 Webhook 方式不支持"""
        notifier = get_notifier("dingtalk")
        optional = notifier.params["optional"]
        # Webhook 方式不支持 image 类型的消息，因此也没有 image 相关参数
        image_related = [p for p in optional if "image" in p.lower() or "pic" in p.lower()]
        assert len(image_related) == 0, (
            f"Webhook 方式不支持 image 消息类型，但发现相关参数: {image_related}"
        )


class TestDingTalkTextMessageData:
    """测试 text 消息类型的数据结构"""

    def test_text_message_data_structure(self):
        """text 消息应生成正确的数据结构"""
        dingtalk = DingTalk()
        dingtalk._prepare_data(
            title="测试标题",
            content="测试内容",
            markdown=False,
        )
        assert dingtalk.data["msgtype"] == "text"
        assert "text" in dingtalk.data
        assert "content" in dingtalk.data["text"]
        assert "测试标题" in dingtalk.data["text"]["content"]
        assert "测试内容" in dingtalk.data["text"]["content"]

    def test_text_message_has_no_image_field(self):
        """text 消息数据结构中不应包含 image 字段"""
        dingtalk = DingTalk()
        dingtalk._prepare_data(
            title="测试",
            content="测试内容",
            markdown=False,
        )
        assert "image" not in dingtalk.data
        assert "picUrl" not in dingtalk.data


class TestDingTalkMarkdownMessageData:
    """测试 markdown 消息类型的数据结构"""

    def test_markdown_message_data_structure(self):
        """markdown 消息应生成正确的数据结构"""
        dingtalk = DingTalk()
        dingtalk._prepare_data(
            title="Markdown 标题",
            content="# Hello\n\n这是一段 Markdown 内容",
            markdown=True,
        )
        assert dingtalk.data["msgtype"] == "markdown"
        assert "markdown" in dingtalk.data
        assert dingtalk.data["markdown"]["title"] == "Markdown 标题"
        assert "# Hello" in dingtalk.data["markdown"]["text"]

    def test_markdown_with_image_syntax(self):
        """Markdown 消息中可嵌入图片语法（变通方案）"""
        dingtalk = DingTalk()
        markdown_content = (
            f"## 图片推送测试\n\n"
            f"![示例图片]({TEST_IMAGE_URL})\n\n"
            f"这是一张通过 Markdown 语法嵌入的图片。"
        )
        dingtalk._prepare_data(
            title="图片推送",
            content=markdown_content,
            markdown=True,
        )
        assert dingtalk.data["msgtype"] == "markdown"
        assert f"![示例图片]({TEST_IMAGE_URL})" in dingtalk.data["markdown"]["text"]
        assert "![" in dingtalk.data["markdown"]["text"]
        assert "](" in dingtalk.data["markdown"]["text"]

    def test_markdown_message_has_no_image_field(self):
        """markdown 消息数据结构中也不应有独立的 image 字段"""
        dingtalk = DingTalk()
        dingtalk._prepare_data(
            title="测试",
            content="![img](http://example.com/pic.jpg)",
            markdown=True,
        )
        assert "image" not in dingtalk.data
        assert "picUrl" not in dingtalk.data


class TestDingTalkUrlConstruction:
    """测试 URL 构造逻辑"""

    def test_url_with_token_only(self):
        """仅提供 token 时，URL 应使用 base_url 模板"""
        dingtalk = DingTalk()
        url = dingtalk._prepare_url(token="test_token_123")
        assert "oapi.dingtalk.com/robot/send" in url
        assert "access_token=test_token_123" in url

    def test_url_with_full_webhook_url(self):
        """提供完整 Webhook URL 时，应直接使用该 URL"""
        dingtalk = DingTalk()
        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = dingtalk._prepare_url(token=full_url)
        assert url == full_url

    def test_url_with_secret_adds_sign_params(self):
        """提供 secret 时，URL 应附加 timestamp 和 sign 参数"""
        dingtalk = DingTalk()
        url = dingtalk._prepare_url(token="test_token", secret="SECtest123")
        assert "&timestamp=" in url
        assert "&sign=" in url


class TestDingTalkEncrypt:
    """测试加签逻辑"""

    def test_encrypt_returns_timestamp_and_sign(self):
        """encrypt 方法应返回 timestamp 和 sign"""
        timestamp, sign = DingTalk.encrypt("test_secret")
        assert isinstance(timestamp, str)
        assert isinstance(sign, str)
        assert len(timestamp) > 0
        assert len(sign) > 0
        # timestamp 应为毫秒级时间戳（13 位数字）
        assert timestamp.isdigit()
        assert len(timestamp) == 13


class TestDingTalkNotifyFlow:
    """测试完整的 notify 流程（mock 请求）"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试用例前的初始化"""
        self.dingtalk = DingTalk()
        self.mock_response = unittest.mock.MagicMock()
        self.mock_response.text = '{"errcode": 0, "errmsg": "ok"}'

    def test_notify_with_markdown_image_mocked(self, monkeypatch):
        """通过 mock 请求验证 Markdown 图片推送的完整流程"""
        captured_data = {}

        def mock_request(method, url, **kwargs):
            captured_data["url"] = url
            if "json" in kwargs:
                captured_data["json"] = kwargs["json"]
            return self.mock_response

        monkeypatch.setattr(self.dingtalk, "request", mock_request)

        markdown_content = (
            f"## 推送测试\n\n"
            f"![图片]({TEST_IMAGE_URL})\n\n"
            f"推送成功。"
        )
        self.dingtalk._prepare_url(token="fake_token_123")
        self.dingtalk._prepare_data(
            title="图片推送测试",
            content=markdown_content,
            markdown=True,
        )
        response = self.dingtalk._send_message()

        # 验证请求的 JSON 数据
        assert captured_data["json"]["msgtype"] == "markdown"
        assert TEST_IMAGE_URL in captured_data["json"]["markdown"]["text"]
        assert response is not None

    def test_notify_with_text_only_no_image(self, monkeypatch):
        """纯文本推送不应包含图片相关字段"""
        captured_data = {}

        def mock_request(method, url, **kwargs):
            captured_data["json"] = kwargs.get("json", {})
            return self.mock_response

        monkeypatch.setattr(self.dingtalk, "request", mock_request)

        self.dingtalk._prepare_url(token="fake_token_123")
        self.dingtalk._prepare_data(
            title="纯文本推送",
            content="这是一条纯文本消息",
            markdown=False,
        )
        self.dingtalk._send_message()

        # 纯文本消息不应包含 markdown 或 image 字段
        assert captured_data["json"]["msgtype"] == "text"
        assert "image" not in captured_data["json"]
        assert "markdown" not in captured_data["json"]


class TestWebhookImageMessageLimitation:
    """验证钉钉 Webhook 方式不支持 image 消息类型的说明"""

    def test_webhook_does_not_support_image_msgtype(self):
        """钉钉 Webhook 方式不支持 msgtype: 'image' 的消息类型

        这是钉钉 API 自身的限制，不是 OnePush 的问题。
        参考文档: https://open.dingtalk.com/document/development/robot-message-type

        消息类型对比:
        - text:    Webhook ✅ | 接口 ✅
        - markdown: Webhook ✅ | 接口 ✅
        - image:   Webhook ❌ | 接口 ✅
        - link:    Webhook ✅ | 接口 ✅
        - feedCard: Webhook ✅ | 接口 ❌
        """
        dingtalk = DingTalk()
        # 确认 OnePush 的 DingTalk 提供者仅支持 text 和 markdown
        dingtalk._prepare_data(title="测试", content="测试", markdown=False)
        assert dingtalk.data["msgtype"] == "text"

        dingtalk._prepare_data(title="测试", content="测试", markdown=True)
        assert dingtalk.data["msgtype"] == "markdown"

        # 尝试构造 image 类型的消息体（OnePush 不会自动生成此类型）
        assert dingtalk.data["msgtype"] != "image"

    def test_workaround_markdown_image_syntax(self):
        """变通方案：使用 Markdown 语法嵌入图片

        在 markdown=True 模式下，content 中可以使用 Markdown 图片语法:
        ![图片描述](图片URL)

        钉钉 Markdown 支持的语法参见文档：
        https://open.dingtalk.com/document/isvapp/the-internal-robot-of-the-enterprise-realizes-group-chat-and
        """
        dingtalk = DingTalk()
        image_markdown = f"![钉钉Logo]({TEST_IMAGE_URL})"
        dingtalk._prepare_data(
            title="图片推送",
            content=image_markdown,
            markdown=True,
        )
        data = dingtalk.data
        assert data["msgtype"] == "markdown"
        assert "![" in data["markdown"]["text"]
        assert f"]({TEST_IMAGE_URL})" in data["markdown"]["text"]

    def test_workaround_multiple_images(self):
        """变通方案：Markdown 中可嵌入多张图片"""
        dingtalk = DingTalk()
        image1 = "https://example.com/pic1.jpg"
        image2 = "https://example.com/pic2.jpg"
        markdown_content = (
            f"## 多图推送\n\n"
            f"![图1]({image1})\n\n"
            f"![图2]({image2})"
        )
        dingtalk._prepare_data(
            title="多图推送",
            content=markdown_content,
            markdown=True,
        )
        assert "![图1]" in dingtalk.data["markdown"]["text"]
        assert "![图2]" in dingtalk.data["markdown"]["text"]


class TestAtMentionDataStructure:
    """测试 @ 功能的数据结构

    注意：
    1. OnePush 的 DingTalk 提供者 _prepare_data 方法不支持构造 at 字段，
       因此 @ 功能需要通过直接构造请求体来实现。
    2. text 和 markdown 两种消息类型都支持 @指定人（atMobiles）。
    3. 同一机器人短时间内多条消息只触发一次 @ 通知。
    4. atUserIds 必须使用企业通讯录中的真实数字 userId，昵称无效。
    """

    def test_one_push_no_at_field_in_text(self):
        """OnePush text 消息不包含 at 字段（已知限制）"""
        dingtalk = DingTalk()
        dingtalk._prepare_data(
            title="测试",
            content="@所有人 测试消息",
            markdown=False,
        )
        assert "at" not in dingtalk.data, (
            "OnePush 当前不支持 at 字段，@ 功能需直接构造请求"
        )

    def test_one_push_no_at_field_in_markdown(self):
        """OnePush markdown 消息不包含 at 字段（已知限制）"""
        dingtalk = DingTalk()
        dingtalk._prepare_data(
            title="测试",
            content="@所有人 测试消息",
            markdown=True,
        )
        assert "at" not in dingtalk.data, (
            "OnePush 当前不支持 at 字段，@ 功能需直接构造请求"
        )


class TestDingTalkMarkdownFullSyntax:
    """测试钉钉 Markdown 支持的全部语法

    钉钉 Markdown 支持以下语法（参考官方文档）：
    - 标题（# ~ ######）
    - 引用（>）
    - 加粗（**text**）、斜体（*text*）
    - 链接（[text](url)）
    - 图片（![alt](url)）
    - 无序列表（- 或 *）
    - 有序列表（1. 2. 3.）

    不支持：表格、代码块、删除线、任务列表等。
    """

    IMG = "https://example.com/test.png"

    def test_all_headings(self):
        """所有六级标题语法"""
        dingtalk = DingTalk()
        content = (
            "# 一级标题\n\n"
            "## 二级标题\n\n"
            "### 三级标题\n\n"
            "#### 四级标题\n\n"
            "##### 五级标题\n\n"
            "###### 六级标题"
        )
        dingtalk._prepare_data(title="标题测试", content=content, markdown=True)
        data = dingtalk.data
        assert "# 一级标题" in data["markdown"]["text"]
        assert "## 二级标题" in data["markdown"]["text"]
        assert "###### 六级标题" in data["markdown"]["text"]

    def test_bold_and_italic(self):
        """加粗和斜体语法"""
        dingtalk = DingTalk()
        content = "**加粗文字** 和 *斜体文字* 以及 **加粗 *嵌套斜体* 文字**"
        dingtalk._prepare_data(title="文字效果", content=content, markdown=True)
        data = dingtalk.data
        assert "**加粗文字**" in data["markdown"]["text"]
        assert "*斜体文字*" in data["markdown"]["text"]
        assert "**加粗 *嵌套斜体* 文字**" in data["markdown"]["text"]

    def test_quote(self):
        """引用语法"""
        dingtalk = DingTalk()
        content = "> 这是一段引用文字。\n> 引用可以有多行。"
        dingtalk._prepare_data(title="引用测试", content=content, markdown=True)
        data = dingtalk.data
        assert "> 这是一段引用文字。" in data["markdown"]["text"]
        assert "> 引用可以有多行。" in data["markdown"]["text"]

    def test_link(self):
        """链接语法"""
        dingtalk = DingTalk()
        content = (
            "请访问 [OnePush](https://github.com/y1ndan/onepush) 了解更多。\n"
            "也可查看 [钉钉文档](https://open.dingtalk.com/)。"
        )
        dingtalk._prepare_data(title="链接测试", content=content, markdown=True)
        data = dingtalk.data
        assert "[OnePush](https://github.com/y1ndan/onepush)" in data["markdown"]["text"]
        assert "[钉钉文档](https://open.dingtalk.com/)" in data["markdown"]["text"]

    def test_image(self):
        """图片语法"""
        dingtalk = DingTalk()
        content = f"![示例图片]({self.IMG})"
        dingtalk._prepare_data(title="图片测试", content=content, markdown=True)
        data = dingtalk.data
        assert f"![示例图片]({self.IMG})" in data["markdown"]["text"]

    def test_unordered_list(self):
        """无序列表语法（- 和 *）"""
        dingtalk = DingTalk()
        content = (
            "- 第一项\n"
            "- 第二项\n"
            "- 第三项\n\n"
            "* 星号列表项1\n"
            "* 星号列表项2"
        )
        dingtalk._prepare_data(title="无序列表", content=content, markdown=True)
        data = dingtalk.data
        assert "- 第一项" in data["markdown"]["text"]
        assert "- 第三项" in data["markdown"]["text"]
        assert "* 星号列表项1" in data["markdown"]["text"]

    def test_ordered_list(self):
        """有序列表语法"""
        dingtalk = DingTalk()
        content = (
            "1. 第一步：安装依赖\n"
            "2. 第二步：配置参数\n"
            "3. 第三步：发送消息\n"
            "4. 第四步：验证结果"
        )
        dingtalk._prepare_data(title="有序列表", content=content, markdown=True)
        data = dingtalk.data
        assert "1. 第一步" in data["markdown"]["text"]
        assert "4. 第四步" in data["markdown"]["text"]

    def test_full_syntax_combined(self):
        """全语法组合测试：标题 + 引用 + 加粗斜体 + 链接 + 图片 + 列表 + @"""
        dingtalk = DingTalk()
        content = (
            "# OnePush 推送能力测试\n\n"
            "## @ 功能测试\n\n"
            "@所有人 请查看以下内容：\n\n"
            "## 文字效果\n\n"
            "**加粗** | *斜体* | **加粗 *嵌套* 斜体**\n\n"
            "## 引用\n\n"
            "> OnePush 支持多种消息平台。\n"
            "> 钉钉 Webhook 方式不支持 image 消息类型。\n\n"
            "## 链接\n\n"
            "[OnePush](https://github.com/y1ndan/onepush) | "
            "[钉钉开放平台](https://open.dingtalk.com/)\n\n"
            "## 图片\n\n"
            f"![测试图片]({self.IMG})\n\n"
            "## 无序列表\n\n"
            "- 文本消息\n"
            "- Markdown 消息\n"
            "- 图片（Markdown 内嵌）\n\n"
            "## 有序列表\n\n"
            "1. 获取 Webhook 地址\n"
            "2. 配置加签密钥\n"
            "3. 调用 API 发送\n"
            "4. 查看推送结果\n\n"
            "###### 测试完成"
        )
        dingtalk._prepare_data(title="全语法测试", content=content, markdown=True)
        data = dingtalk.data
        assert data["msgtype"] == "markdown"
        assert "# OnePush" in data["markdown"]["text"]
        assert "@所有人" in data["markdown"]["text"]
        assert "**加粗**" in data["markdown"]["text"]
        assert "*斜体*" in data["markdown"]["text"]
        assert "> OnePush" in data["markdown"]["text"]
        assert "[OnePush](https://github.com/y1ndan/onepush)" in data["markdown"]["text"]
        assert f"![测试图片]({self.IMG})" in data["markdown"]["text"]
        assert "- 文本消息" in data["markdown"]["text"]
        assert "1. 获取 Webhook" in data["markdown"]["text"]
        assert "###### 测试完成" in data["markdown"]["text"]

    def test_unsupported_syntax_table(self):
        """钉钉 Markdown 不支持表格语法（已知限制）"""
        dingtalk = DingTalk()
        content = "| 列1 | 列2 |\n| --- | --- |\n| A | B |"
        dingtalk._prepare_data(title="不支持语法", content=content, markdown=True)
        data = dingtalk.data
        # 表格语法会原样发送，但钉钉不会渲染为表格
        assert "|" in data["markdown"]["text"]

    def test_unsupported_syntax_code_block(self):
        """钉钉 Markdown 不支持代码块语法（已知限制）"""
        dingtalk = DingTalk()
        content = "```python\nprint('hello')\n```"
        dingtalk._prepare_data(title="不支持语法", content=content, markdown=True)
        data = dingtalk.data
        # 代码块语法会原样发送，但钉钉不会渲染为代码块
        assert "```" in data["markdown"]["text"]


class TestOnePushHighLevelAPI:
    """测试 OnePush 高层 API 的 DingTalk 调用"""

    def test_get_notifier_returns_dingtalk_instance(self):
        """get_notifier('dingtalk') 应返回 DingTalk 实例"""
        notifier = get_notifier("dingtalk")
        assert isinstance(notifier, DingTalk)

    def test_all_providers_includes_dingtalk(self):
        """all_providers() 应包含 dingtalk"""
        from onepush import all_providers
        providers = all_providers()
        assert "dingtalk" in providers

    def test_notify_markdown_image_workaround(self, monkeypatch):
        """通过高层 notify API 验证 Markdown 图片推送（mock）

        注意：request 是 @staticmethod，用 monkeypatch 替换后 self.request
        会绑定实例为第一个参数，因此 mock 需要接收 self。
        """
        mock_response = unittest.mock.MagicMock()
        mock_response.text = '{"errcode": 0, "errmsg": "ok"}'

        def mock_request(self_or_method, method_or_url, *args, **kwargs):
            """兼容静态方法替换后的实例方法调用"""
            return mock_response

        monkeypatch.setattr(DingTalk, "request", mock_request)

        from onepush import notify

        response = notify(
            "dingtalk",
            token="fake_token",
            title="图片推送",
            content=f"![图片]({TEST_IMAGE_URL})",
            markdown=True,
        )
        assert response is not None
        assert response.text == '{"errcode": 0, "errmsg": "ok"}'


class TestTwoPushDingTalkRouting:
    """测试 TwoPush 钉钉增强路径与 OnePush 原路径互斥。"""

    def test_dingtalk_without_enhanced_params_uses_onepush(self, monkeypatch):
        """未携带增强参数时应继续调用 OnePush。"""
        from modules import notification

        calls = {"onepush": 0, "direct": 0}

        class FakeNotifier:
            def request(self, *args, **kwargs):
                """占位请求函数，满足安全注入契约。"""
                response = unittest.mock.MagicMock()
                response.status_code = 200
                response.text = '{"errcode": 0, "errmsg": "ok"}'
                response.json.return_value = {"errcode": 0, "errmsg": "ok"}
                return response

            def notify(self, **kwargs):
                calls["onepush"] += 1
                response = unittest.mock.MagicMock()
                response.status_code = 200
                response.text = '{"errcode": 0, "errmsg": "ok"}'
                response.json.return_value = {"errcode": 0, "errmsg": "ok"}
                return response

        def fake_get_notifier(provider):
            assert provider == "dingtalk"
            return FakeNotifier()

        def fake_direct(*args, **kwargs):
            calls["direct"] += 1
            return unittest.mock.MagicMock()

        monkeypatch.setattr(notification, "get_notifier", fake_get_notifier)
        monkeypatch.setattr(notification, "_send_dingtalk_webhook", fake_direct)

        result = notification._notify_single_channel(
            {"provider": "dingtalk", "token": "token-only"},
            "标题",
            "内容",
            0,
            1,
            unittest.mock.MagicMock(),
        )

        assert result is True
        assert calls == {"onepush": 1, "direct": 0}

    def test_dingtalk_with_enhanced_params_uses_direct_once(self, monkeypatch):
        """携带增强参数时应只调用 TwoPush 直发路径一次。"""
        from modules import notification

        calls = {"onepush": 0, "direct": 0}

        def fake_get_notifier(provider):
            calls["onepush"] += 1
            raise AssertionError("增强钉钉通道不应调用 OnePush")

        def fake_direct(channel, title, content, validated_url=None):
            """记录增强路由收到的预构造 URL。"""
            assert validated_url == (
                "https://oapi.dingtalk.com/robot/send?"
                "access_token=token-only"
            )
            calls["direct"] += 1
            response = unittest.mock.MagicMock()
            response.status_code = 200
            response.text = '{"errcode": 0, "errmsg": "ok"}'
            response.json.return_value = {"errcode": 0, "errmsg": "ok"}
            return response

        monkeypatch.setattr(notification, "get_notifier", fake_get_notifier)
        monkeypatch.setattr(notification, "_send_dingtalk_webhook", fake_direct)

        result = notification._notify_single_channel(
            {
                "provider": "dingtalk",
                "token": "token-only",
                "msgtype": "markdown",
                "at": ["13800138000"],
            },
            "标题",
            "内容",
            0,
            1,
            unittest.mock.MagicMock(),
        )

        assert result is True
        assert calls == {"onepush": 0, "direct": 1}


class TestIsEnhancedDingtalkChannel:
    """测试 _is_enhanced_dingtalk_channel 的增强路径判定。"""

    def test_at_null_does_not_trigger_enhanced_path(self):
        """显式 at 为 None 时不应触发钉钉直发增强路径。"""
        from modules.notification import _is_enhanced_dingtalk_channel

        assert _is_enhanced_dingtalk_channel("dingtalk", {"at": None}) is False

    def test_none_value_keys_do_not_trigger_enhanced_path(self):
        """多个增强键值均为 None 时不应触发钉钉直发增强路径。"""
        from modules.notification import _is_enhanced_dingtalk_channel

        assert _is_enhanced_dingtalk_channel(
            "dingtalk", {"at": None, "isAtAll": None}
        ) is False

    def test_non_none_enhanced_values_still_trigger(self):
        """增强键存在且值非 None 时应触发钉钉直发增强路径。"""
        from modules.notification import _is_enhanced_dingtalk_channel

        assert _is_enhanced_dingtalk_channel(
            "dingtalk", {"at": ["13800138000"]}
        ) is True
        assert _is_enhanced_dingtalk_channel(
            "dingtalk", {"msgtype": "markdown"}
        ) is True

    def test_non_dingtalk_provider_never_enhanced(self):
        """非钉钉渠道即使携带增强键也不应走钉钉直发增强路径。"""
        from modules.notification import _is_enhanced_dingtalk_channel

        assert _is_enhanced_dingtalk_channel(
            "serverchan", {"at": ["13800138000"]}
        ) is False


class TestTwoPushDingTalkUrlBuilder:
    """测试 TwoPush 钉钉 Webhook URL 构造。"""

    def test_token_only_builds_webhook_url(self):
        """仅传 access token 时应拼接钉钉 Webhook URL。"""
        from modules.notification import _build_dingtalk_webhook_url

        url = _build_dingtalk_webhook_url("abc123")

        assert url == "https://oapi.dingtalk.com/robot/send?access_token=abc123"

    def test_full_webhook_url_is_reused(self):
        """完整 Webhook URL 不应重复追加 access_token。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(full_url)

        assert url == full_url
        assert url.count("access_token=") == 1

    def test_token_with_secret_adds_sign(self):
        """token + secret 应生成 timestamp 与 sign。"""
        from modules.notification import _build_dingtalk_webhook_url

        url = _build_dingtalk_webhook_url("abc123", secret="SECtest")

        assert "access_token=abc123" in url
        assert "timestamp=" in url
        assert "sign=" in url

    def test_full_url_with_secret_does_not_duplicate_access_token(self):
        """完整 URL 加签时不应重复追加 access_token。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(full_url, secret="SECtest")

        assert url.count("access_token=") == 1
        assert "timestamp=" in url
        assert "sign=" in url

    def test_existing_sign_params_are_replaced(self):
        """已有 timestamp/sign 时应以本次生成值覆盖，避免重复参数。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?"
            "access_token=abc123&timestamp=old&sign=old"
        )
        url = _build_dingtalk_webhook_url(full_url, secret="SECtest")

        assert url.count("timestamp=") == 1
        assert url.count("sign=") == 1
        assert "timestamp=old" not in url
        assert "sign=old" not in url

    @pytest.mark.parametrize(
        "full_url",
        [
            "http://oapi.dingtalk.com/robot/send?access_token=abc123",
            "HTTP://oapi.dingtalk.com/robot/send?access_token=abc123",
            "http://oapi.dingtalk.com:80/robot/send?access_token=abc123",
        ],
    )
    def test_http_full_webhook_url_raises_value_error(self, full_url):
        """HTTP 完整 Webhook URL 应因未使用 HTTPS 而被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="HTTPS"):
            _build_dingtalk_webhook_url(full_url)

    def test_non_http_scheme_is_treated_as_token(self):
        """非 http(s) scheme 的地址不应被静默当作裸 token，应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        token = "ftp://example.com/robot/send?access_token=abc123"

        with pytest.raises(ValueError, match="URL"):
            _build_dingtalk_webhook_url(token)

    def test_schemeless_query_string_raises_value_error(self):
        """无 scheme 但含 query（=）特征时应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="URL"):
            _build_dingtalk_webhook_url(
                "oapi.dingtalk.com/robot/send?access_token=abc123"
            )

    def test_plain_token_still_works(self):
        """纯裸 token 应正常拼接（守护既有行为）。"""
        from modules.notification import _build_dingtalk_webhook_url

        url = _build_dingtalk_webhook_url("abc123")

        assert url == "https://oapi.dingtalk.com/robot/send?access_token=abc123"

    def test_scheme_without_netloc_is_not_full_url(self):
        """仅有 scheme 而无 netloc 时不应视为完整 URL，且因含协议特征应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url, _is_full_url

        assert _is_full_url("https://") is False
        assert _is_full_url("http://") is False

        with pytest.raises(ValueError, match="URL"):
            _build_dingtalk_webhook_url("http://")

    def test_http_scheme_case_insensitive(self):
        """大写 http(s) scheme 应同样被识别为完整 URL。"""
        from modules.notification import _is_full_url

        assert _is_full_url(
            "HTTPS://oapi.dingtalk.com/robot/send?access_token=abc123"
        ) is True
        assert _is_full_url(
            "HTTP://oapi.dingtalk.com/robot/send?access_token=abc123"
        ) is True

    @pytest.mark.parametrize("control_code", [*range(32), 127])
    def test_full_url_with_ascii_control_character_raises_value_error(
        self, control_code
    ):
        """完整 URL 包含 ASCII C0 或 DEL 时应抛出明确配置错误。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            f"ht{chr(control_code)}tps://oapi.dingtalk.com/robot/send?"
            "access_token=abc123"
        )

        with pytest.raises(ValueError, match="控制字符"):
            _build_dingtalk_webhook_url(full_url)

    @pytest.mark.parametrize("control_character", ["\n", "\t", "\r", "\x00", "\x7f"])
    @pytest.mark.parametrize("position", ["prefix", "suffix"])
    def test_full_url_with_edge_control_character_raises_value_error(
        self, control_character, position
    ):
        """原始完整 URL 首尾控制字符不得被 strip 静默移除。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        value = (
            f"{control_character}{full_url}"
            if position == "prefix"
            else f"{full_url}{control_character}"
        )

        with pytest.raises(ValueError, match="控制字符"):
            _build_dingtalk_webhook_url(value)

    @pytest.mark.parametrize("scheme", ["https", "HTTPS", "HtTpS"])
    def test_full_url_normalizes_https_scheme(self, scheme):
        """完整 URL 应接受大小写不敏感的 HTTPS 并输出小写 scheme。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            f"{scheme}://oapi.dingtalk.com/robot/send?access_token=abc123"
        )

        assert _build_dingtalk_webhook_url(full_url) == (
            "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        )

    def test_full_url_without_access_token_raises_value_error(self):
        """完整 Webhook URL 缺少 access_token 参数时应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="access_token"):
            _build_dingtalk_webhook_url("https://oapi.dingtalk.com/robot/send")

    def test_full_url_without_access_token_with_secret_raises_value_error(self):
        """完整 Webhook URL 缺少 access_token 时即使加签也应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="access_token"):
            _build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send",
                secret="SECtest",
            )

    def test_full_url_with_access_token_still_works(self):
        """含 access_token 的完整 Webhook URL 应原样返回（守护既有行为）。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(full_url)

        assert url == full_url
        assert url.count("access_token=") == 1

    def test_non_dingtalk_domain_full_url_raises_value_error(self):
        """非钉钉域名的完整 Webhook URL 应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="oapi.dingtalk.com"):
            _build_dingtalk_webhook_url(
                "https://example.com/robot/send?access_token=abc123"
            )

    def test_non_dingtalk_domain_with_secret_raises_value_error(self):
        """非钉钉域名（如内网地址）即使加签也应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="oapi.dingtalk.com"):
            _build_dingtalk_webhook_url(
                "https://internal.local/robot/send?access_token=abc123",
                secret="SECtest",
            )

    def test_dingtalk_domain_full_url_still_works(self):
        """钉钉官方域名的完整 Webhook URL 应原样返回（守护既有行为）。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(full_url)

        assert url == full_url
        assert url.count("access_token=") == 1

    @pytest.mark.parametrize("hostname", ["oapi.dingtalk.com", "OAPI.DINGTALK.COM"])
    @pytest.mark.parametrize("port", ["", ":443"])
    def test_full_url_normalizes_hostname_and_default_port(self, hostname, port):
        """官方域名应输出小写并移除显式默认端口。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            f"https://{hostname}{port}/robot/send?access_token=abc123"
        )
        url = _build_dingtalk_webhook_url(full_url)

        assert url == "https://oapi.dingtalk.com/robot/send?access_token=abc123"

    @pytest.mark.parametrize(
        "userinfo",
        [
            "user@",
            "user:@",
            "user:password@",
            "%75ser@",
            "user:%70assword@",
        ],
    )
    def test_full_url_with_userinfo_raises_value_error(self, userinfo):
        """完整 URL authority 包含任意 userinfo 时应拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="用户信息"):
            _build_dingtalk_webhook_url(
                f"https://{userinfo}oapi.dingtalk.com/robot/send?"
                "access_token=abc123"
            )

    @pytest.mark.parametrize("port", [444, 8443])
    def test_full_url_with_non_standard_numeric_port_raises_value_error(self, port):
        """完整 URL 仅允许省略端口或显式使用 443。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="443"):
            _build_dingtalk_webhook_url(
                f"https://oapi.dingtalk.com:{port}/robot/send?access_token=abc123"
            )

    @pytest.mark.parametrize("port", ["bad", "99999"])
    def test_full_url_with_invalid_port_raises_configuration_value_error(self, port):
        """非法或越界端口应转换为明确的配置 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="端口"):
            _build_dingtalk_webhook_url(
                f"https://oapi.dingtalk.com:{port}/robot/send?access_token=abc123"
            )

    @pytest.mark.parametrize("port", ["", "00443", "+443"])
    def test_full_url_rejects_noncanonical_raw_port(self, port):
        """完整 URL 应拒绝空端口和非规范的 443 原始拼写。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="端口"):
            _build_dingtalk_webhook_url(
                f"https://oapi.dingtalk.com:{port}/robot/send?access_token=abc123"
            )

    @pytest.mark.parametrize(
        "full_url",
        [
            "https://evil.example:444/robot/send?access_token=abc123",
            "https://[::1]:444/robot/send?access_token=abc123",
        ],
    )
    def test_invalid_host_error_precedes_port_error(self, full_url):
        """其他域名和 IPv6 应先按非官方域名拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError) as exc:
            _build_dingtalk_webhook_url(full_url)

        assert "域名" in str(exc.value)
        assert "端口" not in str(exc.value)

    def test_non_standard_path_full_url_raises_value_error(self):
        """完整 Webhook URL 使用非标准路径时应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="robot/send"):
            _build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/other/path?access_token=abc123"
            )

    def test_trailing_slash_path_raises_value_error(self):
        """完整 Webhook URL 路径带尾部斜杠（非标准路径）时应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="robot/send"):
            _build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send/?access_token=abc123"
            )

    def test_standard_path_full_url_still_works(self):
        """完整 Webhook URL 使用标准路径 /robot/send 时应原样返回（守护既有行为）。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(full_url)

        assert url == full_url
        assert url.count("access_token=") == 1

    def test_sign_is_encoded_exactly_once(self):
        """sign 在最终 URL 中应只被 URL 编码一次，可正确解码回原始字节。"""
        import base64
        import hashlib
        import hmac
        import time
        from urllib.parse import parse_qsl, urlsplit
        from modules.notification import _build_dingtalk_webhook_url

        url = _build_dingtalk_webhook_url("abc123", secret="SECtest")

        query = dict(parse_qsl(urlsplit(url).query))
        assert "%25" not in url, "sign 不应被双重 URL 编码"

        timestamp = query["timestamp"]
        string_to_sign = f"{timestamp}\nSECtest"
        expected = base64.b64encode(
            hmac.new(
                b"SECtest",
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        assert query["sign"] == expected

    def test_empty_access_token_value_raises_value_error(self):
        """完整 Webhook URL 的 access_token 值为空时应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="access_token"):
            _build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send?access_token="
            )

    def test_blank_access_token_value_raises_value_error(self):
        """完整 Webhook URL 的 access_token 值为空白时应抛出 ValueError。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="access_token"):
            _build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send?access_token=%20%20"
            )

    def test_non_empty_access_token_still_works(self):
        """access_token 值非空时完整 Webhook URL 应原样返回（守护既有行为）。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(full_url)

        assert url == full_url
        assert url.count("access_token=") == 1

    def test_multiple_access_tokens_one_non_empty_passes(self):
        """多个 access_token 参数中至少一个非空时应放行。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?access_token=&access_token=abc"
        )
        url = _build_dingtalk_webhook_url(full_url)

        assert url == full_url

    def test_full_url_normalizes_query_encoding_and_preserves_pairs(self):
        """完整 URL query 应规范编码并保留重复键、空值与顺序。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?"
            "x=&access%5ftoken=abc&x=1&access_token="
        )

        assert _build_dingtalk_webhook_url(full_url) == (
            "https://oapi.dingtalk.com/robot/send?"
            "x=&access_token=abc&x=1&access_token="
        )

    @pytest.mark.parametrize("extra_length", [0, 1])
    def test_full_url_length_limit(self, extra_length):
        """完整 Webhook URL 正好达到上限时接受，超过一个字符时拒绝。"""
        from modules.notification import (
            DINGTALK_WEBHOOK_MAX_URL_LENGTH,
            _build_dingtalk_webhook_url,
        )

        prefix = "https://oapi.dingtalk.com/robot/send?access_token="
        full_url = prefix + "a" * (
            DINGTALK_WEBHOOK_MAX_URL_LENGTH - len(prefix) + extra_length
        )

        if extra_length == 0:
            assert _build_dingtalk_webhook_url(full_url) == full_url
            return

        with pytest.raises(ValueError, match="长度|8192"):
            _build_dingtalk_webhook_url(full_url)

    def test_full_url_rejects_normalized_url_over_length_limit(self):
        """完整 URL 规范编码后超过长度上限时应拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?access_token="
            + "中" * 1000
        )

        with pytest.raises(ValueError, match="编码后.*长度|8192"):
            _build_dingtalk_webhook_url(full_url)

    def test_secret_signing_can_push_final_url_over_length_limit(
        self, monkeypatch
    ):
        """未加签可用的边界 URL 在追加固定签名后超限应拒绝。"""
        import modules.notification as notification

        prefix = "https://oapi.dingtalk.com/robot/send?access_token="
        full_url = prefix + "a" * (
            notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH - len(prefix)
        )
        monkeypatch.setattr(
            notification,
            "_make_dingtalk_sign",
            lambda secret: ("1", "s" * 44),
        )

        assert notification._build_dingtalk_webhook_url(full_url) == full_url
        with pytest.raises(ValueError, match="编码后.*长度|8192"):
            notification._build_dingtalk_webhook_url(
                full_url,
                secret="SECtest",
            )

    @pytest.mark.parametrize("parameter_count", [100, 101])
    def test_full_url_query_parameter_limit(self, parameter_count):
        """完整 URL query 接受 100 个参数并拒绝 101 个参数。"""
        from modules.notification import _build_dingtalk_webhook_url

        pairs = ["access_token=abc123"]
        pairs.extend(f"x={index}" for index in range(parameter_count - 1))
        full_url = (
            "https://oapi.dingtalk.com/robot/send?" + "&".join(pairs)
        )

        if parameter_count == 100:
            assert _build_dingtalk_webhook_url(full_url) == full_url
            return

        with pytest.raises(ValueError, match="参数|100"):
            _build_dingtalk_webhook_url(full_url)

    @pytest.mark.parametrize("invalid_utf8", ["%FF", "%E4%B8"])
    def test_full_url_query_rejects_invalid_utf8(self, invalid_utf8):
        """query 中非法或截断的 UTF-8 百分号字节序列应被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?access_token=abc123&x="
            f"{invalid_utf8}"
        )

        with pytest.raises(ValueError, match="合法 UTF-8") as exc_info:
            _build_dingtalk_webhook_url(full_url)

        assert "参数不得超过" not in str(exc_info.value)

    def test_full_url_query_field_limit_has_dedicated_message(self):
        """超过 100 个用户 query 参数时应返回独立数量错误。"""
        from modules.notification import _build_dingtalk_webhook_url

        pairs = ["access_token=abc123"]
        pairs.extend(f"x={index}" for index in range(100))
        full_url = (
            "https://oapi.dingtalk.com/robot/send?" + "&".join(pairs)
        )

        with pytest.raises(ValueError, match="参数不得超过 100") as exc_info:
            _build_dingtalk_webhook_url(full_url)

        assert "合法 UTF-8" not in str(exc_info.value)

    @pytest.mark.parametrize(
        ("suffix", "accepted"),
        [
            ("&" * 99, True),
            ("&" * 102, False),
            ("&x=" + "&" * 98, True),
            ("&x=" + "&" * 101, False),
        ],
    )
    def test_query_field_limit_counts_blank_and_consecutive_fields(
        self, suffix, accepted
    ):
        """空字段与连续分隔符应按 max_num_fields 的字段语义计数。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?access_token=abc123"
            f"{suffix}"
        )

        if accepted:
            assert "access_token=abc123" in _build_dingtalk_webhook_url(
                full_url
            )
            return

        with pytest.raises(ValueError, match="参数总数（含签名字段）不得超过 102"):
            _build_dingtalk_webhook_url(full_url)

    def test_query_field_limit_is_checked_before_parse_qsl(self, monkeypatch):
        """字段超限应在解析前确定，不依赖解析器异常文本。"""
        import modules.notification as notification

        parse_calls = []

        def fail_parse(*args, **kwargs):
            """记录不应发生的解析调用。"""
            parse_calls.append((args, kwargs))
            raise ValueError("本地化字段数量错误")

        monkeypatch.setattr(notification, "parse_qsl", fail_parse)
        full_url = (
            "https://oapi.dingtalk.com/robot/send?"
            + "&".join(["access_token=abc123"] + ["x="] * 102)
        )

        with pytest.raises(ValueError, match="参数总数（含签名字段）不得超过 102"):
            notification._build_dingtalk_webhook_url(full_url)

        assert parse_calls == []

    def test_empty_query_has_zero_fields_before_parse(self, monkeypatch):
        """空 query 应按零字段处理并继续交给解析器。"""
        import modules.notification as notification

        parse_calls = []

        def record_parse(query, **kwargs):
            """记录空 query 解析并返回空字段列表。"""
            parse_calls.append((query, kwargs))
            return []

        monkeypatch.setattr(notification, "parse_qsl", record_parse)

        assert notification._parse_dingtalk_query("", "测试 URL") == []
        assert len(parse_calls) == 1
        assert parse_calls[0][0] == ""

    def test_other_query_value_error_uses_generic_parse_message(
        self, monkeypatch
    ):
        """非字段数量类 ValueError 应归入通用 query 解析错误。"""
        import modules.notification as notification

        def fail_parse(*args, **kwargs):
            raise ValueError("conversion failed")

        monkeypatch.setattr(notification, "parse_qsl", fail_parse)

        with pytest.raises(ValueError, match="query 解析失败") as exc_info:
            notification._build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send?access_token=abc123"
            )

        message = str(exc_info.value)
        assert "合法 UTF-8" not in message
        assert "参数不得超过" not in message

    def test_full_url_query_accepts_valid_encoded_chinese(self):
        """query 中合法 UTF-8 中文百分号编码应正常解码并规范化。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?"
            "access_token=abc123&name=%E4%B8%AD%E6%96%87"
        )

        assert _build_dingtalk_webhook_url(full_url) == full_url

    @pytest.mark.parametrize("invalid_percent", ["%", "%2", "%ZZ", "%2Z"])
    def test_full_url_query_rejects_invalid_percent_escape(self, invalid_percent):
        """query 中所有非合法 %HH 转义均应被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?access_token=abc123&x="
            f"{invalid_percent}"
        )

        with pytest.raises(ValueError, match="百分号|percent|%HH"):
            _build_dingtalk_webhook_url(full_url)

    @pytest.mark.parametrize("suffix", ["#", "?#", "#section"])
    def test_full_url_with_fragment_delimiter_raises_value_error(self, suffix):
        """完整 URL 只要存在 fragment 分隔符就应被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="fragment|片段"):
            _build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send?"
                f"access_token=abc123{suffix}"
            )

    def test_plain_token_with_hash_remains_url_encoded(self):
        """含井号的裸 token 不应被误判为完整 URL。"""
        from modules.notification import _build_dingtalk_webhook_url

        assert _build_dingtalk_webhook_url("abc#section") == (
            "https://oapi.dingtalk.com/robot/send?access_token=abc%23section"
        )

    @pytest.mark.parametrize(
        "encoded_character",
        [
            "%00",
            "%0A",
            "%0a",
            "%09",
            "%C2%85",
            "%c2%85",
            "%E2%80%8B",
            "%e2%80%8b",
        ],
    )
    @pytest.mark.parametrize("position", ["key", "value"])
    def test_decoded_query_control_or_format_character_raises_value_error(
        self, encoded_character, position
    ):
        """query 键值解码后出现 Unicode Cc/Cf 字符均应被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        hidden_pair = (
            f"x{encoded_character}=safe"
            if position == "key"
            else f"access_token=safe{encoded_character}"
        )
        suffix = "&access_token=abc123" if position == "key" else ""
        full_url = "https://oapi.dingtalk.com/robot/send?" f"{hidden_pair}{suffix}"

        with pytest.raises(ValueError, match="控制|格式"):
            _build_dingtalk_webhook_url(full_url)

    def test_double_encoded_query_control_sequence_is_accepted(self):
        """双编码控制字符按一次 URL 解码语义应保留为字面量。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = (
            "https://oapi.dingtalk.com/robot/send?access_token=%2500&x=%250A"
        )

        assert _build_dingtalk_webhook_url(full_url) == full_url

    def test_domain_error_prioritized_over_missing_access_token(self):
        """域名错误应优先于缺失 access_token 报错。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError) as exc:
            _build_dingtalk_webhook_url("https://evil.com/robot/send")

        assert "域名" in str(exc.value)
        assert "access_token" not in str(exc.value)

    def test_path_error_prioritized_over_missing_access_token(self):
        """路径错误应优先于缺失 access_token 报错。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError) as exc:
            _build_dingtalk_webhook_url("https://oapi.dingtalk.com/other/path")

        assert "路径" in str(exc.value)
        assert "access_token" not in str(exc.value)

    def test_whitespace_padded_token_is_stripped(self):
        """带首尾空白的裸 token 应先 strip 再拼接 Webhook URL。"""
        from modules.notification import _build_dingtalk_webhook_url

        url = _build_dingtalk_webhook_url("  abc123  ")

        assert url == "https://oapi.dingtalk.com/robot/send?access_token=abc123"

    def test_whitespace_padded_full_url_is_stripped(self):
        """带首尾空白的完整 Webhook URL 应先 strip 再原样复用。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        url = _build_dingtalk_webhook_url(f"  {full_url}  ")

        assert url == full_url

    @pytest.mark.parametrize("edge_character", ["\u00a0", "\u3000", "\u2028"])
    @pytest.mark.parametrize("position", ["prefix", "suffix"])
    def test_non_ascii_edge_whitespace_raises_value_error(
        self, edge_character, position
    ):
        """原始 token 首尾非普通空格的空白字符应被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        token = "abc123"
        value = (
            f"{edge_character}{token}"
            if position == "prefix"
            else f"{token}{edge_character}"
        )

        with pytest.raises(ValueError, match="空白"):
            _build_dingtalk_webhook_url(value)

    @pytest.mark.parametrize(
        "value_template",
        [
            "abc{character}123",
            (
                "https://oapi.dingtalk.com/robot/send?"
                "x{character}=safe&access_token=abc123"
            ),
            (
                "https://oapi.dingtalk.com/robot/send?"
                "access_token=abc{character}123"
            ),
        ],
        ids=["token", "url-key", "url-value"],
    )
    @pytest.mark.parametrize(
        "hidden_character",
        ["\u0085", "\u200b", "\u2060"],
        ids=["U+0085", "U+200B", "U+2060"],
    )
    def test_unicode_control_or_format_character_raises_value_error(
        self, value_template, hidden_character
    ):
        """裸 token 与完整 URL 中的 Unicode Cc/Cf 字符均应被拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        value = value_template.format(character=hidden_character)

        with pytest.raises(ValueError, match="控制|格式"):
            _build_dingtalk_webhook_url(value)

    @pytest.mark.parametrize(
        "value_template",
        [
            "abc{character}123",
            (
                "https://oapi.dingtalk.com/robot/send?"
                "x{character}=safe&access_token=abc123"
            ),
            (
                "https://oapi.dingtalk.com/robot/send?"
                "access_token=abc{character}123"
            ),
        ],
        ids=["token", "url-key", "url-value"],
    )
    @pytest.mark.parametrize(
        "surrogate",
        ["\ud800", "\udbff", "\udc00", "\udfff"],
        ids=["U+D800", "U+DBFF", "U+DC00", "U+DFFF"],
    )
    def test_literal_unicode_surrogate_raises_plain_value_error(
        self, value_template, surrogate
    ):
        """裸 token 与完整 URL 原文中的 Unicode Cs 字符应明确拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        value = value_template.format(character=surrogate)

        with pytest.raises(ValueError, match="代理字符") as exc_info:
            _build_dingtalk_webhook_url(value)

        assert type(exc_info.value) is ValueError

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
    @pytest.mark.parametrize("position", ["key", "value"])
    def test_parsed_query_unicode_surrogate_raises_plain_value_error(
        self, monkeypatch, surrogate, position
    ):
        """parse_qsl 后 query 键值中的 Unicode Cs 字符应明确拒绝。"""
        import modules.notification as notification

        query_pairs = (
            [(f"x{surrogate}", "safe"), ("access_token", "abc123")]
            if position == "key"
            else [("access_token", f"abc{surrogate}123")]
        )
        monkeypatch.setattr(
            notification,
            "parse_qsl",
            lambda *args, **kwargs: query_pairs,
        )

        with pytest.raises(ValueError, match="代理字符") as exc_info:
            notification._build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send?access_token=abc123"
            )

        assert type(exc_info.value) is ValueError

    def test_plain_token_remains_url_encoded(self):
        """裸 token 应继续通过 urlencode 编码。"""
        from modules.notification import _build_dingtalk_webhook_url

        assert _build_dingtalk_webhook_url("abc+/?") == (
            "https://oapi.dingtalk.com/robot/send?access_token=abc%2B%2F%3F"
        )

    def test_plain_token_rejects_encoded_url_over_length_limit(self):
        """裸 token 编码后的完整 URL 超过长度上限时应拒绝。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="编码后.*长度|8192"):
            _build_dingtalk_webhook_url("中" * 1000)

    def test_non_ascii_final_url_raises_value_error(self, monkeypatch):
        """最终 URL 意外包含 Unicode 时应安全转换为 ValueError。"""
        import modules.notification as notification

        monkeypatch.setattr(
            notification,
            "urlunsplit",
            lambda parts: "https://oapi.dingtalk.com/robot/send?access_token=中文",
        )

        with pytest.raises(ValueError, match="ASCII"):
            notification._build_dingtalk_webhook_url(
                "https://oapi.dingtalk.com/robot/send?access_token=abc123"
            )

    @pytest.mark.parametrize("extra_length", [0, 1])
    def test_plain_token_length_limit(self, extra_length):
        """裸 token 正好达到上限时接受，超过一个字符时拒绝。"""
        from modules.notification import (
            DINGTALK_TOKEN_MAX_LENGTH,
            _build_dingtalk_webhook_url,
        )

        token = "a" * (DINGTALK_TOKEN_MAX_LENGTH + extra_length)

        if extra_length == 0:
            assert _build_dingtalk_webhook_url(token).endswith(token)
            return

        with pytest.raises(ValueError, match="token.*长度|4096"):
            _build_dingtalk_webhook_url(token)

    def test_blank_token_still_rejected(self):
        """strip 后为空的 token 应抛出 ValueError（守护守卫）。"""
        from modules.notification import _build_dingtalk_webhook_url

        with pytest.raises(ValueError, match="缺少 token"):
            _build_dingtalk_webhook_url("   ")


class TestTwoPushDingTalkAt:
    """测试 TwoPush 钉钉手机号 @ 处理。"""

    def test_string_at_normalizes_to_mobile_list(self):
        """字符串 at 应归一为 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": "13800138000"})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

    def test_list_at_normalizes_to_mobile_list(self):
        """数组 at 应归一为 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": ["13800138000", "13900139000"]})

        assert at == {
            "atMobiles": ["13800138000", "13900139000"],
            "isAtAll": False,
        }

    def test_country_code_prefixed_mobiles_are_normalized(self):
        """带 +86/86 前缀的手机号应归一化为纯号段进入 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": ["+8613800138000"]})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

        at = _normalize_dingtalk_at({"at": ["8613800138000", "13900139000"]})

        assert at["atMobiles"] == ["13800138000", "13900139000"]

    def test_dict_at_mobiles_is_supported(self):
        """字典 at.atMobiles 应被支持。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": {"atMobiles": ["13800138000"]}})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

    def test_at_mobiles_alias_is_supported(self):
        """at_mobiles 别名应被支持。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at_mobiles": ["13800138000"]})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

    def test_numeric_at_normalizes_to_mobile_list(self):
        """数字 at 应转字符串归一为 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": 13800138000})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

    def test_numeric_at_mobiles_alias_works(self):
        """数字 at_mobiles 别名应转字符串生效。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at_mobiles": 13800138000})

        assert at["atMobiles"] == ["13800138000"]

    def test_non_mobile_numeric_at_is_filtered(self):
        """非手机号数字 at 应被过滤，不进入 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": 12345})

        assert "atMobiles" not in at

    def test_boolean_at_is_not_converted(self):
        """布尔 at 不应转字符串进入 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": True})

        assert "atMobiles" not in at

    def test_append_missing_mobile_mentions(self):
        """正文缺少 @手机号 时应自动补齐。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions("通知内容", ["13800138000"])

        assert text == "通知内容\n\n@13800138000"

    def test_existing_mobile_mentions_are_not_duplicated(self):
        """正文已有 @手机号 时不应重复补齐。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions(
            "通知内容\n\n@13800138000",
            ["13800138000"],
        )

        assert text.count("@13800138000") == 1

    @pytest.mark.parametrize(
        "format_character",
        [
            "\u200c",
            "\u200d",
            "\u200e",
            "\u200f",
            "\u202a",
            "\u202b",
            "\u202c",
            "\u202d",
            "\u202e",
            "\u2066",
            "\u2067",
            "\u2068",
            "\u2069",
        ],
    )
    @pytest.mark.parametrize(
        ("mention", "mobiles", "is_at_all"),
        [
            ("@13800138000", ["13800138000"], False),
            ("@所有人", [], True),
        ],
    )
    def test_mention_followed_by_format_character_is_not_independent(
        self, format_character, mention, mobiles, is_at_all
    ):
        """提醒后紧跟 Unicode 格式字符时应补齐独立提醒。"""
        from modules.notification import _append_missing_dingtalk_mentions

        content = f"通知内容 {mention}{format_character}后缀"
        text = _append_missing_dingtalk_mentions(
            content, mobiles, is_at_all=is_at_all
        )

        assert text == f"{content}\n\n{mention}"

    @pytest.mark.parametrize(
        "prefix",
        ["x", "9", "_", "中", "@", "\\"],
    )
    @pytest.mark.parametrize(
        ("mention", "mobiles", "is_at_all"),
        [
            ("@13800138000", ["13800138000"], False),
            ("@所有人", [], True),
        ],
    )
    def test_mention_with_non_independent_prefix_is_appended(
        self, prefix, mention, mobiles, is_at_all
    ):
        """单词字符、额外 @ 与反斜杠前缀后的提醒不应视为独立。"""
        from modules.notification import _append_missing_dingtalk_mentions

        content = f"通知内容 {prefix}{mention}"
        text = _append_missing_dingtalk_mentions(
            content, mobiles, is_at_all=is_at_all
        )

        assert text == f"{content}\n\n{mention}"

    @pytest.mark.parametrize(
        "content",
        [
            "通知内容 @138001380001",
            "通知内容 @13800138000abc",
            "通知内容 @13800138000_",
            "通知内容 @13800138000１",
            "通知内容 @13800138000é",
            "通知内容 @13800138000中",
            "通知内容 @13800138000\u0301",
        ],
    )
    def test_mobile_mention_followed_by_unicode_word_is_not_independent(self, content):
        """手机号提醒后紧跟 Unicode 单词字符或组合符时应补齐独立提醒。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions(content, ["13800138000"])

        assert text == f"{content}\n\n@13800138000"

    @pytest.mark.parametrize(
        "content",
        [
            "通知内容 @13800138000，已发送",
            "通知内容 @13800138000 已发送",
            "通知内容\n@13800138000",
            "@13800138000 通知内容",
            "通知内容 @13800138000\r\n下一行",
            "通知内容 @13800138000😀已发送",
        ],
    )
    def test_mobile_mention_with_independent_boundary_is_not_duplicated(self, content):
        """手机号提醒位于文本边界或后接分隔字符时不应重复补齐。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions(content, ["13800138000"])

        assert text == content

    @pytest.mark.parametrize(
        "content",
        [
            "通知内容 @所有人员",
            "通知内容 @所有人abc",
            "通知内容 @所有人_",
            "通知内容 @所有人１",
            "通知内容 @所有人é",
            "通知内容 @所有人\u0301",
        ],
    )
    def test_at_everyone_followed_by_word_character_is_not_independent(self, content):
        """全员提醒后紧跟中文或 ASCII 单词字符时应补齐独立提醒。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions(content, [], is_at_all=True)

        assert text == f"{content}\n\n@所有人"

    @pytest.mark.parametrize(
        "content",
        [
            "通知内容 @所有人，已发送",
            "通知内容 @所有人 已发送",
            "通知内容\n@所有人",
            "@所有人 通知内容",
            "通知内容 @所有人\r\n下一行",
            "通知内容 @所有人😀已发送",
        ],
    )
    def test_at_everyone_with_independent_boundary_is_not_duplicated(self, content):
        """全员提醒后接标点、空白或行尾时不应重复补齐。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions(content, [], is_at_all=True)

        assert text == content

    def test_multiple_mobile_mentions_only_append_missing_independent_tokens(self):
        """多个手机号只应补齐缺少独立提醒的号码。"""
        from modules.notification import _append_missing_dingtalk_mentions

        text = _append_missing_dingtalk_mentions(
            "通知内容 @13800138000，关联 @13900139000abc",
            ["13800138000", "13900139000"],
        )

        assert text == "通知内容 @13800138000，关联 @13900139000abc\n\n@13900139000"

    def test_later_independent_mention_is_found_after_invalid_candidate(self):
        """先出现无效候选时仍应识别后续独立提醒。"""
        from modules.notification import _append_missing_dingtalk_mentions

        content = "通知内容 @13800138000\u200d后缀，随后 @13800138000。"
        text = _append_missing_dingtalk_mentions(content, ["13800138000"])

        assert text == content

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
    def test_isolated_surrogate_boundary_does_not_crash(self, surrogate):
        """孤立代理字符作为边界时扫描不应崩溃。"""
        from modules.notification import _append_missing_dingtalk_mentions

        content = f"通知内容 @13800138000{surrogate}"
        text = _append_missing_dingtalk_mentions(content, ["13800138000"])

        assert text == content

    def test_duplicate_mobiles_are_deduplicated(self):
        """重复手机号应去重且保持顺序。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": ["13800138000", "13900139000", "13800138000"]})

        assert at["atMobiles"] == ["13800138000", "13900139000"]

    def test_at_mobiles_camel_case_alias_is_supported(self):
        """atMobiles 驼峰别名应被支持。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"atMobiles": ["13800138000"]})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

    def test_is_at_all_flags_are_passed_through(self):
        """is_at_all 与 isAtAll 应传递到 at 结构。"""
        from modules.notification import _normalize_dingtalk_at

        assert _normalize_dingtalk_at({"is_at_all": True})["isAtAll"] is True
        assert _normalize_dingtalk_at({"isAtAll": True})["isAtAll"] is True
        assert _normalize_dingtalk_at({"at": ["13800138000"]})["isAtAll"] is False

    def test_non_mobile_values_are_filtered_out(self):
        """非手机号值应被过滤，不进入 atMobiles。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at({"at": ["13800138000", "alice", "userId123"]})

        assert at == {"atMobiles": ["13800138000"], "isAtAll": False}

    def test_is_at_all_string_false_is_parsed_as_false(self):
        """字符串形式的 false 不应被判定为 @全员。"""
        from modules.notification import _normalize_dingtalk_at

        assert _normalize_dingtalk_at({"is_at_all": "false"})["isAtAll"] is False
        assert _normalize_dingtalk_at({"isAtAll": "False"})["isAtAll"] is False

    def test_is_at_all_string_truthy_values_are_parsed_as_true(self):
        """字符串形式的真值应被解析为 @全员。"""
        from modules.notification import _normalize_dingtalk_at

        assert _normalize_dingtalk_at({"is_at_all": "TRUE"})["isAtAll"] is True
        assert _normalize_dingtalk_at({"is_at_all": "1"})["isAtAll"] is True
        assert _normalize_dingtalk_at({"is_at_all": "yes"})["isAtAll"] is True
        assert _normalize_dingtalk_at({"is_at_all": "on"})["isAtAll"] is True

    def test_dict_at_is_at_all_inside_dict_is_honored(self):
        """at 字典内部的 isAtAll 应被识别为 @全员。"""
        from modules.notification import _normalize_dingtalk_at

        at = _normalize_dingtalk_at(
            {"at": {"atMobiles": ["13800138000"], "isAtAll": True}}
        )

        assert at == {"atMobiles": ["13800138000"], "isAtAll": True}
        assert _normalize_dingtalk_at({"at": {"isAtAll": True}})["isAtAll"] is True

    def test_dict_at_is_at_all_merges_with_top_level(self):
        """at 字典内部与顶层的 isAtAll 应取 or 合并。"""
        from modules.notification import _normalize_dingtalk_at

        assert _normalize_dingtalk_at(
            {"at": {"isAtAll": True}, "isAtAll": False}
        )["isAtAll"] is True
        assert _normalize_dingtalk_at(
            {"at": {"isAtAll": "false"}, "is_at_all": "on"}
        )["isAtAll"] is True

    def test_dict_at_is_at_all_string_false_is_parsed_as_false(self):
        """at 字典内部字符串形式的 false 不应被判定为 @全员。"""
        from modules.notification import _normalize_dingtalk_at

        assert _normalize_dingtalk_at({"at": {"isAtAll": "false"}})["isAtAll"] is False


class TestTwoPushDingTalkPayload:
    """测试 TwoPush 钉钉请求体构造。"""

    def test_build_markdown_payload_with_at(self):
        """markdown 请求体应包含 markdown 与 at 字段。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "at": ["13800138000"]},
            "通知标题",
            "## 通知内容",
        )

        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["title"] == "通知标题"
        assert "## 通知内容" in payload["markdown"]["text"]
        assert "@13800138000" in payload["markdown"]["text"]
        assert payload["at"] == {
            "atMobiles": ["13800138000"],
            "isAtAll": False,
        }

    def test_build_text_payload_with_at(self):
        """text 请求体应包含 text 与 at 字段。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "text", "at": ["13800138000"]},
            "通知标题",
            "通知内容",
        )

        assert payload["msgtype"] == "text"
        assert "通知标题" in payload["text"]["content"]
        assert "通知内容" in payload["text"]["content"]
        assert "@13800138000" in payload["text"]["content"]
        assert payload["at"]["atMobiles"] == ["13800138000"]

    @pytest.mark.parametrize(
        ("msgtype", "body_key"),
        [("text", "text"), ("markdown", "markdown")],
    )
    def test_payload_mention_collision_is_appended_for_each_message_type(
        self, msgtype, body_key
    ):
        """text 与 markdown 正文中的手机号子串碰撞均应补齐独立提醒。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": msgtype, "at": ["13800138000"]},
            "通知标题",
            "通知内容 @138001380001",
        )

        body_field = "content" if msgtype == "text" else "text"
        assert payload[body_key][body_field].endswith("\n\n@13800138000")

    @pytest.mark.parametrize("backslash_count", [1, 2, 3])
    @pytest.mark.parametrize(
        ("msgtype", "body_key", "body_field"),
        [
            ("text", "text", "content"),
            ("markdown", "markdown", "text"),
        ],
    )
    def test_payload_escaped_mention_is_appended_for_each_message_type(
        self, msgtype, body_key, body_field, backslash_count
    ):
        """text 与 markdown 均保守拒绝反斜杠紧邻的提醒。"""
        from modules.notification import _build_dingtalk_payload

        escaped_mention = f"{'\\' * backslash_count}@13800138000"
        payload = _build_dingtalk_payload(
            {"msgtype": msgtype, "at": ["13800138000"]},
            "通知标题",
            f"通知内容 {escaped_mention}",
        )

        assert payload[body_key][body_field].endswith("\n\n@13800138000")

    def test_markdown_inline_code_is_checked_only_by_text_boundary(self):
        """markdown 行内代码中的独立提醒按普通文本处理且不重复。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "at": ["13800138000"]},
            "通知标题",
            "示例 `@13800138000`",
        )

        assert payload["markdown"]["text"] == "示例 `@13800138000`"

    def test_country_code_mobile_checks_normalized_mention_in_body(self):
        """国家码手机号应使用归一化号码检查正文中的独立提醒。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "at": ["+8613800138000"]},
            "通知标题",
            "通知内容 @13800138000",
        )

        assert payload["markdown"]["text"] == "通知内容 @13800138000"
        assert payload["at"]["atMobiles"] == ["13800138000"]

    @pytest.mark.parametrize(
        ("msgtype", "body_key", "body_field"),
        [
            ("text", "text", "content"),
            ("markdown", "markdown", "text"),
        ],
    )
    def test_empty_body_appends_mobile_mention(self, msgtype, body_key, body_field):
        """text 与 markdown 空正文均应正常补齐手机号提醒。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": msgtype, "at": ["13800138000"]},
            "",
            "",
        )

        assert payload[body_key][body_field] == "@13800138000"

    def test_build_text_payload_joins_title_and_content_with_double_newline(self):
        """text 请求体应以双换行分隔 title 与 content，与 onepush 行为一致。"""
        from modules.notification import _build_dingtalk_payload
        from onepush.core import Provider

        payload = _build_dingtalk_payload(
            {"msgtype": "text"},
            "标题",
            "内容",
        )

        content = payload["text"]["content"]

        assert "标题\n\n内容" in content
        assert "标题\n内容" not in content
        assert content == Provider.process_message("标题", "内容")

    def test_default_msgtype_is_markdown_for_enhanced_path(self):
        """增强路径未指定 msgtype 但携带 at 时默认使用 markdown。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"at": ["13800138000"]},
            "通知标题",
            "通知内容",
        )

        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["title"] == "通知标题"

    def test_invalid_msgtype_falls_back_to_markdown(self):
        """非法 msgtype 应兜底为 markdown。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "image", "at": ["13800138000"]},
            "通知标题",
            "通知内容",
        )

        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["title"] == "通知标题"

    def test_no_at_means_no_at_field(self):
        """无 at 时不附加 at 字段。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown"},
            "通知标题",
            "通知内容",
        )

        assert "at" not in payload

    def test_is_at_all_appends_at_field_without_mobiles(self):
        """isAtAll=True 时附加 at 字段（仅含 isAtAll）。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "is_at_all": True},
            "通知标题",
            "通知内容",
        )

        assert payload["at"] == {"isAtAll": True}
        assert "atMobiles" not in payload["at"]

    def test_text_is_at_all_appends_at_everyone(self):
        """text 消息 isAtAll=True 时正文应自动补齐 @所有人。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "text", "isAtAll": True},
            "通知标题",
            "通知内容",
        )

        assert "@所有人" in payload["text"]["content"]
        assert payload["at"] == {"isAtAll": True}

    def test_markdown_is_at_all_appends_at_everyone(self):
        """markdown 消息 isAtAll=True 时 text 应自动补齐 @所有人。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "isAtAll": True},
            "通知标题",
            "通知内容",
        )

        assert "@所有人" in payload["markdown"]["text"]
        assert payload["at"] == {"isAtAll": True}

    def test_text_is_at_all_existing_at_everyone_not_duplicated(self):
        """text 消息正文已有 @所有人 时不应重复补齐。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "text", "isAtAll": True},
            "通知标题",
            "通知内容 @所有人",
        )

        assert payload["text"]["content"].count("@所有人") == 1

    def test_markdown_is_at_all_existing_at_everyone_not_duplicated(self):
        """markdown 消息正文已有 @所有人 时不应重复补齐。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "isAtAll": True},
            "通知标题",
            "通知内容 @所有人",
        )

        assert payload["markdown"]["text"].count("@所有人") == 1

    def test_text_is_at_all_with_mobiles_appends_both(self):
        """text 消息同时配置手机号与 isAtAll=True 时两者都应补齐。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "text", "at": ["13800138000"], "isAtAll": True},
            "通知标题",
            "通知内容",
        )

        content = payload["text"]["content"]
        assert "@13800138000" in content
        assert "@所有人" in content
        assert payload["at"]["atMobiles"] == ["13800138000"]
        assert payload["at"]["isAtAll"] is True

    def test_markdown_is_at_all_with_mobiles_appends_both(self):
        """markdown 消息同时配置手机号与 isAtAll=True 时两者都应补齐。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "markdown", "at": ["13800138000"], "isAtAll": True},
            "通知标题",
            "通知内容",
        )

        text = payload["markdown"]["text"]
        assert "@13800138000" in text
        assert "@所有人" in text
        assert payload["at"]["atMobiles"] == ["13800138000"]
        assert payload["at"]["isAtAll"] is True

    def test_is_at_all_dict_nested_flag_appends_at_everyone(self):
        """at 字典内部 isAtAll=True 时正文也应自动补齐 @所有人。"""
        from modules.notification import _build_dingtalk_payload

        payload = _build_dingtalk_payload(
            {"msgtype": "text", "at": {"isAtAll": True}},
            "通知标题",
            "通知内容",
        )

        assert "@所有人" in payload["text"]["content"]
        assert payload["at"] == {"isAtAll": True}


class TestTwoPushDingTalkDirectSend:
    """测试 TwoPush 钉钉直发请求。"""

    def test_send_dingtalk_webhook_posts_json(self, monkeypatch):
        """钉钉直发应 POST JSON 请求体。"""
        from modules import notification

        captured = {}
        fake_response = unittest.mock.MagicMock()
        fake_response.status_code = 200
        fake_response.text = '{"errcode": 0, "errmsg": "ok"}'
        fake_response.json.return_value = {"errcode": 0, "errmsg": "ok"}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            captured["timeout"] = kwargs.get("timeout")
            return fake_response

        monkeypatch.setattr(notification, "request", fake_request)

        response = notification._send_dingtalk_webhook(
            {
                "token": "abc123",
                "msgtype": "markdown",
                "at": ["13800138000"],
            },
            "通知标题",
            "通知内容",
        )

        assert response is fake_response
        assert captured["method"] == "post"
        assert "access_token=abc123" in captured["url"]
        assert captured["headers"] == {"Content-Type": "application/json"}
        assert captured["timeout"] == 10
        assert captured["json"]["msgtype"] == "markdown"
        assert captured["json"]["at"]["atMobiles"] == ["13800138000"]

    def test_send_dingtalk_webhook_without_token_raises_value_error(self, monkeypatch):
        """缺少 token 时应抛出 ValueError。"""
        from modules import notification

        def fake_request(method, url, **kwargs):
            raise AssertionError("缺少 token 时不应发起请求")

        monkeypatch.setattr(notification, "request", fake_request)

        with pytest.raises(ValueError, match="缺少 token"):
            notification._send_dingtalk_webhook({}, "通知标题", "通知内容")

    def test_send_dingtalk_webhook_blank_token_raises(self, monkeypatch):
        """纯空白 token 应抛出 ValueError，不发起请求。"""
        from modules import notification

        def fake_request(method, url, **kwargs):
            raise AssertionError("空白 token 时不应发起请求")

        monkeypatch.setattr(notification, "request", fake_request)

        with pytest.raises(ValueError, match="缺少 token"):
            notification._send_dingtalk_webhook(
                {"token": "   ", "msgtype": "markdown"},
                "通知标题",
                "通知内容",
            )

    def test_send_dingtalk_webhook_full_url_without_token_raises(self, monkeypatch):
        """完整 Webhook URL 缺少 access_token 时发送前应抛出 ValueError。"""
        from modules import notification

        def fake_request(method, url, **kwargs):
            raise AssertionError("缺少 access_token 时不应发起请求")

        monkeypatch.setattr(notification, "request", fake_request)

        with pytest.raises(ValueError, match="access_token"):
            notification._send_dingtalk_webhook(
                {"token": "https://oapi.dingtalk.com/robot/send"},
                "通知标题",
                "通知内容",
            )


class TestTwoPushDingTalkMasking:
    """测试钉钉日志脱敏与通用脱敏工具。"""

    def test_mask_mobile(self):
        """手机号应脱敏。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields({'text': '通知 @13800138000'}, {'text'})['text']

        assert "13800138000" not in text
        assert "138****8000" in text

    def test_mask_webhook_query(self):
        """Webhook URL query 中的敏感参数应脱敏。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields(
            {'text': "https://oapi.dingtalk.com/robot/send?access_token=abc&sign=xyz"},
            {'text'},
        )['text']

        assert "access_token=abc" not in text
        assert "sign=xyz" not in text
        assert "access_token=***" in text
        # query 链（含 & 连接的 sign=xyz）整体被 access_token 的脱敏覆盖，sign 键名不残留
        assert "&sign=" not in text

    def test_failure_reason_is_masked(self):
        """失败原因日志不得泄露手机号或 token。"""
        from modules.notification import _handle_attempt_failure

        log = unittest.mock.MagicMock()
        _handle_attempt_failure(
            "dingtalk(onepush)",
            1,
            1,
            "手机号 13800138000 access_token=abc sign=xyz",
            0,
            log,
        )

        messages = "\n".join(call.args[0] for call in log.error.call_args_list)
        assert "13800138000" not in messages
        assert "access_token=abc" not in messages
        assert "sign=xyz" not in messages

    def test_mask_secret_and_case_insensitive(self):
        """secret 应脱敏，且 access_token/sign/secret 大小写不敏感。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields(
            {'text': "secret=SECabc ACCESS_TOKEN=abc SIGN=xyz"},
            {'text'},
        )['text']

        assert "SECabc" not in text
        assert "ACCESS_TOKEN=abc" not in text
        assert "SIGN=xyz" not in text
        assert "secret=***" in text
        assert "ACCESS_TOKEN=***" in text
        assert "SIGN=***" in text

    def test_mask_does_not_break_plain_words(self):
        """普通单词（如 design）不应被误脱敏。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields({'text': "design=good assign=bad"}, {'text'})['text']

        assert "design=good" in text
        assert "assign=bad" in text

    def test_mask_none_returns_none(self):
        """None 输入应原样返回。"""
        from modules.utils import mask_sensitive_fields

        assert mask_sensitive_fields({'text': None}, {'text'})['text'] is None

    def test_mask_does_not_break_prefixed_token_words(self):
        """带前缀的 access_token（如 xaccess_token）不应被误脱敏。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields(
            {'text': "xaccess_token=abc ?access_token=xyz"},
            {'text'},
        )['text']

        assert "xaccess_token=abc" in text
        assert "?access_token=xyz" not in text
        assert "?access_token=***" in text

    def test_mask_bare_token_param(self):
        """裸 token= 参数应脱敏。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields(
            {'text': "https://sctapi.ftqq.com/SCTabc.send?token=xyz"},
            {'text'},
        )['text']

        assert "token=xyz" not in text
        assert "token=***" in text

    def test_success_log_does_not_log_title(self, monkeypatch):
        """成功路径不应输出标题相关日志。"""
        from modules import notification

        log = unittest.mock.MagicMock()
        fake_response = unittest.mock.MagicMock()
        fake_response.status_code = 200
        fake_response.text = '{"errcode": 0, "errmsg": "ok"}'
        fake_response.json.return_value = {"errcode": 0, "errmsg": "ok"}

        def fake_request(method, url, **kwargs):
            return fake_response

        monkeypatch.setattr(notification, "request", fake_request)

        result = notification._notify_single_channel(
            {
                "provider": "dingtalk",
                "token": "abc123",
                "msgtype": "markdown",
            },
            "通知 13800138000",
            "内容",
            0,
            1,
            log,
        )

        assert result is True
        assert log.info.call_args_list == []

    def test_send_notification_does_not_log_title(self, monkeypatch):
        """send_notification 不应输出标题相关日志。"""
        from modules import notification

        log = unittest.mock.MagicMock()

        def fake_request(method, url, **kwargs):
            raise AssertionError("不应发起真实请求")

        monkeypatch.setattr(notification, "request", fake_request)
        monkeypatch.setattr(
            notification, "_send_dingtalk_webhook",
            lambda channel, title, content: unittest.mock.MagicMock(),
        )
        # 避免 _is_push_successful 访问未 mock 的 response 属性
        monkeypatch.setattr(
            notification, "_is_push_successful",
            lambda response: (True, ""),
        )

        notification.send_notification(
            "标题 13800138000",
            "内容",
            [
                {
                    "provider": "dingtalk",
                    "token": "abc123",
                    "msgtype": "markdown",
                }
            ],
            retry_settings={"interval": 0, "max_count": 1},
            logger=log,
        )

        messages = "\n".join(call.args[0] for call in log.info.call_args_list)
        assert "13800138000" not in messages
        assert "通知标题" not in messages

    def test_mask_does_not_touch_unrelated_params(self):
        """无关参数形式不应被误脱敏。"""
        from modules.utils import mask_sensitive_fields

        text = mask_sensitive_fields(
            {'text': "design=good assign=bad xaccess_token=abc"},
            {'text'},
        )['text']

        assert "design=good" in text
        assert "assign=bad" in text
        assert "xaccess_token=abc" in text


class TestTwoPushDingTalkMultipleChannels:
    """测试多通道中普通钉钉与增强钉钉并存。"""

    def test_plain_and_enhanced_dingtalk_can_coexist(self, monkeypatch):
        """普通钉钉走 OnePush，增强钉钉走直发，二者各发送一次。"""
        from modules import notification

        calls = {"onepush": 0, "direct": 0}

        class FakeNotifier:
            def request(self, *args, **kwargs):
                """占位请求函数，满足安全注入契约。"""
                response = unittest.mock.MagicMock()
                response.status_code = 200
                response.text = '{"errcode": 0, "errmsg": "ok"}'
                response.json.return_value = {"errcode": 0, "errmsg": "ok"}
                return response

            def notify(self, **kwargs):
                calls["onepush"] += 1
                response = unittest.mock.MagicMock()
                response.status_code = 200
                response.text = '{"errcode": 0, "errmsg": "ok"}'
                response.json.return_value = {"errcode": 0, "errmsg": "ok"}
                return response

        def fake_get_notifier(provider):
            assert provider == "dingtalk"
            return FakeNotifier()

        def fake_direct(channel, title, content, validated_url=None):
            """记录增强通道收到的预构造 URL。"""
            assert validated_url == (
                "https://oapi.dingtalk.com/robot/send?"
                "access_token=enhanced-token"
            )
            calls["direct"] += 1
            response = unittest.mock.MagicMock()
            response.status_code = 200
            response.text = '{"errcode": 0, "errmsg": "ok"}'
            response.json.return_value = {"errcode": 0, "errmsg": "ok"}
            return response

        monkeypatch.setattr(notification, "get_notifier", fake_get_notifier)
        monkeypatch.setattr(notification, "_send_dingtalk_webhook", fake_direct)

        results = notification.send_notification(
            "标题",
            "内容",
            [
                {"provider": "dingtalk", "token": "plain-token"},
                {
                    "provider": "dingtalk",
                    "token": "enhanced-token",
                    "msgtype": "markdown",
                    "at": ["13800138000"],
                },
            ],
            retry_settings={"interval": 0, "max_count": 1},
            logger=unittest.mock.MagicMock(),
        )

        assert results == [("dingtalk", True), ("dingtalk", True)]
        assert calls == {"onepush": 1, "direct": 1}
