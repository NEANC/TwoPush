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

    def test_text_at_all_data_structure(self):
        """text 消息 @所有人 的正确数据结构"""
        body = {
            "msgtype": "text",
            "text": {
                "content": "【通知】@所有人 请查看测试结果。"
            },
            "at": {
                "isAtAll": True
            }
        }
        assert body["msgtype"] == "text"
        assert "@所有人" in body["text"]["content"]
        assert body["at"]["isAtAll"] is True

    def test_text_at_user_by_userid_data_structure(self):
        """text 消息 @指定人(userId) 需使用企业通讯录中的真实 userId

        注意：atUserIds 必须使用企业通讯录 API 返回的真实 userId
        （如 052345678901），昵称（如 NEANC）无效。
        实际测试中 @NEANC 不会触发红点通知。
        """
        body = {
            "msgtype": "text",
            "text": {
                "content": "【提醒】@052345678901 请查收。"
            },
            "at": {
                "atUserIds": ["052345678901"],
                "isAtAll": False
            }
        }
        assert body["msgtype"] == "text"
        # userId 是数字格式，不是昵称
        assert body["at"]["atUserIds"][0].isdigit(), (
            "atUserIds 必须是企业通讯录中的真实数字 userId，昵称无效"
        )
        assert body["at"]["isAtAll"] is False

    def test_text_at_user_by_mobile_data_structure(self):
        """text 消息 @指定人(手机号) 的正确数据结构"""
        body = {
            "msgtype": "text",
            "text": {
                "content": "【提醒】@13800138000 请查收。"
            },
            "at": {
                "atMobiles": ["13800138000"],
                "isAtAll": False
            }
        }
        assert body["msgtype"] == "text"
        assert "@13800138000" in body["text"]["content"]
        assert "13800138000" in body["at"]["atMobiles"]
        assert body["at"]["isAtAll"] is False

    def test_text_at_multiple_users_data_structure(self):
        """text 消息 @多人 的正确数据结构"""
        body = {
            "msgtype": "text",
            "text": {
                "content": "@user1 @user2 请查看。"
            },
            "at": {
                "atUserIds": ["user1", "user2"],
                "isAtAll": False
            }
        }
        assert len(body["at"]["atUserIds"]) == 2
        assert "user1" in body["at"]["atUserIds"]
        assert "user2" in body["at"]["atUserIds"]

    def test_markdown_at_all_data_structure(self):
        """markdown 消息 @所有人 的正确数据结构"""
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": "通知标题",
                "text": "## 通知\n\n@所有人 请查看以下内容。\n\n> 重要通知"
            },
            "at": {
                "isAtAll": True
            }
        }
        assert body["msgtype"] == "markdown"
        assert "@所有人" in body["markdown"]["text"]
        assert body["at"]["isAtAll"] is True

    def test_markdown_at_user_by_userid_data_structure(self):
        """markdown 消息 @指定人(userId) 需使用真实数字 userId"""
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": "提醒",
                "text": "## 提醒\n\n@052345678901 请查看测试结果。\n\n> 来自 OnePush"
            },
            "at": {
                "atUserIds": ["052345678901"],
                "isAtAll": False
            }
        }
        assert body["msgtype"] == "markdown"
        assert body["at"]["atUserIds"] == ["052345678901"]
        assert body["at"]["isAtAll"] is False

    def test_text_at_user_by_mobile_effective_approach(self):
        """text 消息 @指定人推荐使用 atMobiles 手机号方式（最可靠）

        对于自定义 Webhook 机器人，atMobiles 方式不需要额外 API 调用获取 userId，
        只需知道用户的钉钉绑定手机号即可，是最简单的 @指定人方式。

        实际验证：使用 atMobiles=['1**********'] 发送成功，状态码 200。
        """
        body = {
            "msgtype": "text",
            "text": {
                "content": "【提醒】@1********** 请查收。"
            },
            "at": {
                "atMobiles": ["1**********"],
                "isAtAll": False
            }
        }
        assert body["msgtype"] == "text"
        assert "@1**********" in body["text"]["content"]
        assert body["at"]["atMobiles"] == ["1**********"]
        assert body["at"]["isAtAll"] is False

    def test_markdown_at_user_by_mobile_data_structure(self):
        """markdown 消息 @指定人(手机号) 的正确数据结构

        实测验证：Markdown 消息的 atMobiles @指定人也有效，
        包括开头纯 @、## 标题后 @、引用+列表混合 等多种样式。
        """
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": "提醒",
                "text": "## 通知\n\n@1********** 请查收。\n\n> 引用\n\n- 列表项"
            },
            "at": {
                "atMobiles": ["1**********"],
                "isAtAll": False
            }
        }
        assert body["msgtype"] == "markdown"
        assert "@1**********" in body["markdown"]["text"]
        assert body["at"]["atMobiles"] == ["1**********"]
        assert body["at"]["isAtAll"] is False

    def test_at_requires_both_content_and_at_field(self):
        """@ 功能必须同时满足两个条件：content 中有 @文本 且 at 字段正确设置

        参考文档：https://open.dingtalk.com/document/development/custom-robots-send-group-messages
        """
        # 只在 content 中写 @所有人，但 at 字段不设置 → 不会真正 @
        body_no_at = {
            "msgtype": "text",
            "text": {"content": "@所有人 测试"},
        }
        assert "at" not in body_no_at

        # 正确方式：content 中有 @所有人 + at.isAtAll = True
        body_with_at = {
            "msgtype": "text",
            "text": {"content": "@所有人 测试"},
            "at": {"isAtAll": True},
        }
        assert "at" in body_with_at
        assert "@所有人" in body_with_at["text"]["content"]
        assert body_with_at["at"]["isAtAll"] is True


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

        def fake_direct(channel, title, content):
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

    def test_http_full_webhook_url_is_reused(self):
        """http 完整 Webhook URL 应原样复用且不重复追加 access_token。"""
        from modules.notification import _build_dingtalk_webhook_url

        full_url = "http://oapi.dingtalk.com/robot/send?access_token=abc123"
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


class TestTwoPushDingTalkMasking:
    """测试钉钉日志脱敏。"""

    def test_mask_mobile(self):
        """手机号应脱敏。"""
        from modules.notification import _mask_dingtalk_sensitive_text

        text = _mask_dingtalk_sensitive_text("通知 @13800138000")

        assert "13800138000" not in text
        assert "138****8000" in text

    def test_mask_webhook_query(self):
        """Webhook URL query 中的敏感参数应脱敏。"""
        from modules.notification import _mask_dingtalk_sensitive_text

        text = _mask_dingtalk_sensitive_text(
            "https://oapi.dingtalk.com/robot/send?access_token=abc&sign=xyz"
        )

        assert "access_token=abc" not in text
        assert "sign=xyz" not in text
        assert "access_token=***" in text
        assert "sign=***" in text

    def test_failure_reason_is_masked(self):
        """失败原因日志不得泄露手机号或 token。"""
        from modules.notification import _handle_attempt_failure

        log = unittest.mock.MagicMock()
        _handle_attempt_failure(
            "dingtalk",
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
        from modules.notification import _mask_dingtalk_sensitive_text

        text = _mask_dingtalk_sensitive_text(
            "secret=SECabc ACCESS_TOKEN=abc SIGN=xyz"
        )

        assert "SECabc" not in text
        assert "ACCESS_TOKEN=abc" not in text
        assert "SIGN=xyz" not in text
        assert "secret=***" in text
        assert "ACCESS_TOKEN=***" in text
        assert "SIGN=***" in text

    def test_mask_does_not_break_plain_words(self):
        """普通单词（如 design）不应被误脱敏。"""
        from modules.notification import _mask_dingtalk_sensitive_text

        text = _mask_dingtalk_sensitive_text("design=good assign=bad")

        assert "design=good" in text
        assert "assign=bad" in text

    def test_mask_none_returns_none(self):
        """None 输入应原样返回。"""
        from modules.notification import _mask_dingtalk_sensitive_text

        assert _mask_dingtalk_sensitive_text(None) is None

    def test_mask_does_not_break_prefixed_token_words(self):
        """带前缀的 access_token（如 xaccess_token）不应被误脱敏。"""
        from modules.notification import _mask_dingtalk_sensitive_text

        text = _mask_dingtalk_sensitive_text("xaccess_token=abc ?access_token=xyz")

        assert "xaccess_token=abc" in text
        assert "?access_token=xyz" not in text
        assert "?access_token=***" in text

    def test_success_log_masks_title_sensitive_data(self, monkeypatch):
        """成功日志中的标题含敏感信息时应脱敏。"""
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
        messages = "\n".join(call.args[0] for call in log.info.call_args_list)
        assert "13800138000" not in messages
        assert "138****8000" in messages

    def test_send_notification_logs_masked_title(self, monkeypatch):
        """send_notification 的标题日志应脱敏。"""
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
        assert "138****8000" in messages


class TestTwoPushDingTalkMultipleChannels:
    """测试多通道中普通钉钉与增强钉钉并存。"""

    def test_plain_and_enhanced_dingtalk_can_coexist(self, monkeypatch):
        """普通钉钉走 OnePush，增强钉钉走直发，二者各发送一次。"""
        from modules import notification

        calls = {"onepush": 0, "direct": 0}

        class FakeNotifier:
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

        def fake_direct(channel, title, content):
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