from nonebot.plugin import PluginMetadata

from nonebot import require

require("nonebot_plugin_orm")

from .config import cfg_command_prefixes
from . import native_matchers as native_matchers

memes_prefixes = cfg_command_prefixes()
memes_prefix = memes_prefixes[0] if memes_prefixes else ""

__plugin_meta__ = PluginMetadata(
    name="表情包制作",
    description="制作各种沙雕表情包",
    usage=(
        "- 表情列表\n"
        "发送【表情包制作】查看表情列表\n"
        "- 表情详情\n"
        "发送【表情详情 + 表情名/关键词】查看表情详细信息和表情预览\n"
        "- 表情搜索\n"
        "发送【表情搜索 + 关键词】查找相关的表情\n"
        "- 表情包开关\n"
        "- 群管可以启用或禁用本群的表情\n"
        "发送 启用表情/禁用表情 表情名/关键词，如：禁用表情 摸\n"
        "- 超级用户可以全局禁用/启用表情\n"
        "发送 全局启用表情 表情名/关键词 可全局启用表情；\n"
        "发送 全局禁用表情 表情名/关键词 可全局禁用表情；\n"
        "发送 禁用列表 查看全局禁用的表情列表\n"
        "- 白名单保护（仅超级用户）\n"
        "发送【添加保护@用户】或【添加保护<QQ号>】添加保护白名单\n"
        "发送【移除保护@用户】或【移除保护<QQ号>】移除保护白名单\n"
        "发送【保护表情<表情名>】添加保护表情\n"
        "发送【取消保护表情<表情名>】移除保护表情\n"
        "发送【保护列表】查看保护配置\n"
        "- 表情使用\n"
        f"发送【{memes_prefix}关键词 + 图片/文字】制作表情\n"
        "可使用【自己】、【@某人】获取指定用户的头像作为图片\n"
        "可使用【@ + 用户id】指定任意用户获取头像，如【摸 @114514】\n"
        "可将回复中的消息作为文字和图片的输入\n"
        "- 随机表情\n"
        "发送【随机表情 + 图片/文字】可随机制作表情\n"
        "随机范围为 图片/文字 数量符合要求的表情\n"
        "- 表情调用统计\n"
        "发送【[我的][全局]<时间段>表情调用统计 [表情名]】获取表情调用次数统计图\n"
        "【我的】、【全局】、<时间段>、【表情名】 均为可选项\n"
        "<时间段> 的关键词有：日、本日、周、本周、月、本月、年、本年\n"
        "如：【我的今日表情调用统计 petpet】"
    ),
    type="application",
    homepage="https://github.com/noneplugin/nonebot-plugin-memes",
    supported_adapters={"~onebot.v11"},
)
