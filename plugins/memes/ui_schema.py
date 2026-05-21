"""表情包插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "generator": {
        "base_url": "http://127.0.0.1:2233",
        "command_prefixes": None,
        "disabled_list": [],
        "check_resources_on_startup": True,
    },
    "behavior": {
        "use_sender_when_no_image": False,
        "use_default_when_no_text": False,
        "random_meme_show_info": True,
        "notice_prob": 0.1,
        "use_gif": False,
        "use_ban_word": True,
    },
    "list_image": {
        "sort_by": "keywords",
        "sort_reverse": False,
        "text_template": "{keywords}",
        "add_category_icon": True,
        "label_new_days": 30,
        "label_hot_threshold": 21,
        "label_hot_days": 7,
    },
    "mismatch_policy": {
        "too_much_text": "ignore",
        "too_few_text": "ignore",
        "too_much_image": "ignore",
        "too_few_image": "ignore",
    },
    "multiple_image": {
        "direct_send_threshold": 10,
        "send_zip_file": True,
        "send_forward_msg": False,
    },
}


def get_ui_schema() -> dict[str, Any]:
    """返回表情包插件配置界面 schema。"""
    return {
        "key": "memes",
        "title": "表情包制作",
        "cards": [
            {
                "key": "memes.generator",
                "title": "生成器",
                "schemas": [
                    {"field": "base_url", "label": "meme-generator 地址", "component": "Input", "default": DEFAULTS["generator"]["base_url"], "helpMessage": "表情生成服务地址。"},
                    {"field": "command_prefixes", "label": "命令前缀", "component": "GTags", "default": DEFAULTS["generator"]["command_prefixes"], "helpMessage": "留空时使用 NoneBot 默认命令前缀。"},
                    {"field": "disabled_list", "label": "全局禁用表情", "component": "GTags", "default": DEFAULTS["generator"]["disabled_list"], "helpMessage": "填表情 key。"},
                    {"field": "check_resources_on_startup", "label": "启动时检查资源", "component": "Switch", "default": DEFAULTS["generator"]["check_resources_on_startup"], "helpMessage": "启动时检查表情资源是否可用。"},
                ],
            },
            {
                "key": "memes.behavior",
                "title": "行为",
                "schemas": [
                    {"field": "use_sender_when_no_image", "label": "缺图时使用发送者头像", "component": "Switch", "default": DEFAULTS["behavior"]["use_sender_when_no_image"], "helpMessage": "仅对需要图片的表情生效。"},
                    {"field": "use_default_when_no_text", "label": "缺字时使用默认文案", "component": "Switch", "default": DEFAULTS["behavior"]["use_default_when_no_text"], "helpMessage": "仅对需要文字的表情生效。"},
                    {"field": "random_meme_show_info", "label": "随机表情显示关键词", "component": "Switch", "default": DEFAULTS["behavior"]["random_meme_show_info"], "helpMessage": "发送随机表情时附带关键词。"},
                    {"field": "notice_prob", "label": "刷屏提醒概率", "component": "InputNumber", "default": DEFAULTS["behavior"]["notice_prob"], "helpMessage": "随机触发提醒消息的概率。", "componentProps": {"min": 0, "max": 1, "step": 0.01}},
                    {"field": "use_gif", "label": "启用 GIF 输出", "component": "Switch", "default": DEFAULTS["behavior"]["use_gif"], "helpMessage": "把表情转换为 GIF 再发送。"},
                    {"field": "use_ban_word", "label": "启用屏蔽词", "component": "Switch", "default": DEFAULTS["behavior"]["use_ban_word"], "helpMessage": "过滤敏感词。"},
                ],
            },
            {
                "key": "memes.list_image",
                "title": "列表图",
                "schemas": [
                    {"field": "sort_by", "label": "排序方式", "component": "Select", "default": DEFAULTS["list_image"]["sort_by"], "helpMessage": "控制表情列表的排序方式。", "componentProps": {"options": [{"label": "key", "value": "key"}, {"label": "keywords", "value": "keywords"}, {"label": "date_created", "value": "date_created"}, {"label": "date_modified", "value": "date_modified"}]}},
                    {"field": "sort_reverse", "label": "倒序排序", "component": "Switch", "default": DEFAULTS["list_image"]["sort_reverse"], "helpMessage": "反向显示表情列表。"},
                    {"field": "text_template", "label": "显示模板", "component": "Input", "default": DEFAULTS["list_image"]["text_template"], "helpMessage": "支持 {index}、{key}、{keywords}、{shortcuts}、{tags}。"},
                    {"field": "add_category_icon", "label": "显示分类图标", "component": "Switch", "default": DEFAULTS["list_image"]["add_category_icon"], "helpMessage": "给图片表情和文字表情加分类图标。"},
                    {"field": "label_new_days", "label": "new 标签天数", "component": "InputNumber", "default": DEFAULTS["list_image"]["label_new_days"], "helpMessage": "在多少天内添加 new 标签。", "componentProps": {"min": 1}},
                    {"field": "label_hot_threshold", "label": "hot 阈值", "component": "InputNumber", "default": DEFAULTS["list_image"]["label_hot_threshold"], "helpMessage": "统计周期内调用次数超过该值时加 hot 标签。", "componentProps": {"min": 0}},
                    {"field": "label_hot_days", "label": "hot 统计天数", "component": "InputNumber", "default": DEFAULTS["list_image"]["label_hot_days"], "helpMessage": "hot 标签统计周期。", "componentProps": {"min": 1}},
                ],
            },
            {
                "key": "memes.mismatch_policy",
                "title": "参数不匹配",
                "schemas": [
                    {"field": "too_much_text", "label": "文字过多", "component": "Select", "default": DEFAULTS["mismatch_policy"]["too_much_text"], "componentProps": {"options": [{"label": "ignore", "value": "ignore"}, {"label": "prompt", "value": "prompt"}, {"label": "drop", "value": "drop"}]}},
                    {"field": "too_few_text", "label": "文字不足", "component": "Select", "default": DEFAULTS["mismatch_policy"]["too_few_text"], "componentProps": {"options": [{"label": "ignore", "value": "ignore"}, {"label": "prompt", "value": "prompt"}, {"label": "get", "value": "get"}]}},
                    {"field": "too_much_image", "label": "图片过多", "component": "Select", "default": DEFAULTS["mismatch_policy"]["too_much_image"], "componentProps": {"options": [{"label": "ignore", "value": "ignore"}, {"label": "prompt", "value": "prompt"}, {"label": "drop", "value": "drop"}]}},
                    {"field": "too_few_image", "label": "图片不足", "component": "Select", "default": DEFAULTS["mismatch_policy"]["too_few_image"], "componentProps": {"options": [{"label": "ignore", "value": "ignore"}, {"label": "prompt", "value": "prompt"}, {"label": "get", "value": "get"}]}},
                ],
            },
            {
                "key": "memes.multiple_image",
                "title": "多图发送",
                "schemas": [
                    {"field": "direct_send_threshold", "label": "直接发送阈值", "component": "InputNumber", "default": DEFAULTS["multiple_image"]["direct_send_threshold"], "componentProps": {"min": 1}},
                    {"field": "send_zip_file", "label": "发送 zip", "component": "Switch", "default": DEFAULTS["multiple_image"]["send_zip_file"]},
                    {"field": "send_forward_msg", "label": "发送合并转发", "component": "Switch", "default": DEFAULTS["multiple_image"]["send_forward_msg"]},
                ],
            },
        ],
    }
