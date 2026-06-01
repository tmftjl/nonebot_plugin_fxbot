# -*- coding: utf-8 -*-
"""Bot 状态查询命令。"""
from __future__ import annotations

import asyncio
import base64
import json
import platform
import socket
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil
from functools import wraps
from urllib.parse import quote
from nonebot import get_driver, logger
from nonebot.adapters import Bot, Event
from playwright.async_api import Browser, async_playwright
from playwright.async_api._generated import Playwright as PlaywrightType

from ..adapter import build_message, build_message_segment
from ..config import get_manager
from ..permission import PermLevel, PermScene
from ..utils.http import get_shared_async_client
from ..utils.paths import data_dir
from .registry import P
from .status_monitor import get_monitor

# ========== 背景图片配置 ==========
_BG_DIR = Path(__file__).parent / "resource" / "status_bg"
_DEFAULT_BG_FILE = "1.jpg"

# ========== 消息统计缓存文件 ==========
_MESSAGE_CACHE_FILE = data_dir("system") / "status_message_cache.json"

# ========== 浏览器资源管理 ==========
_PW: Optional[PlaywrightType] = None
_BROWSER: Optional[Browser] = None
_BROWSER_INIT_LOCK = asyncio.Lock()

# ========== 消息统计缓存 ==========
_MESSAGE_CACHE: Dict[str, Dict[str, int]] = defaultdict(lambda: {"received": 0, "sent": 0})
_MESSAGE_CACHE_LOCK = asyncio.Lock()
_MESSAGE_CACHE_DATE = datetime.now().strftime("%Y-%m-%d")

# ========== 中央API统计配置 ==========
_DEFAULT_STATS_API_URL = "http://127.0.0.1:8000"

status_cmd = P.on_regex(
    r"^#状态$",
    name="system_status",
    display_name="Bot状态",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
)


@status_cmd.handle()
async def handle_status(bot: Bot, event: Event):
    """处理状态查询命令"""
    data = await _collect_status_data()
    html = await _build_status_html(data)
    image_bytes = await _render_html_to_image(html)

    image_seg = build_message_segment(bot, "image", image_bytes)
    await status_cmd.finish(build_message(bot, image_seg))


# ========== 数据收集 ==========


async def _collect_status_data() -> Dict[str, Any]:
    """收集所有状态数据"""
    await _ensure_today_cache()
    tasks = [
        _collect_bot_info(),
        _collect_hardware_info(),
        _collect_disk_info(),
        _collect_network_info(),
        _collect_process_info(),
        _collect_system_info(),
    ]
    bot_info_list, hardware, disks, network, processes, system_info = await asyncio.gather(*tasks)

    return {
        "bot_info_list": bot_info_list,
        "hardware": hardware,
        "disks": disks,
        "network": network,
        "processes": processes,
        "system_info": system_info,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def _collect_bot_info() -> List[Dict[str, Any]]:
    """收集所有Bot信息"""
    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        return []

    bot_info_list = []
    driver = get_driver()
    start_time = getattr(driver, "_start_time", time.time())
    runtime_str = _format_runtime(time.time() - start_time)

    for bot_self_id, bot in bots.items():
        # 获取适配器名称
        adapter_name = bot.adapter.get_name()

        if adapter_name == "QQ":
            # QQ官方机器人
            info = await bot.me()
            nickname = info.username
            user_id = info.id
            avatar_url = info.avatar if info.avatar else ""
            count_contacts = {}

            # 消息统计（使用bot_self_id作为缓存key，而不是info.id）
            cache = _MESSAGE_CACHE[f"bot_{bot_self_id}"]
            message_count = {"今日接收": cache["received"], "今日发送": cache["sent"]}
        else:
            # OneBot v11 等适配器
            login_info = await bot.get_login_info()
            nickname = login_info.get("nickname", "Unknown")
            user_id = str(login_info.get("user_id", bot_self_id))
            avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

            # 获取群列表和好友列表
            group_list = await bot.get_group_list()
            friend_list = await bot.get_friend_list()
            total_members = sum(g.get("member_count", 0) or g.get("max_member_count", 0) for g in group_list)
            count_contacts = {
                "好友": len(friend_list),
                "群聊": len(group_list),
                "群友": total_members,
            }

            # 消息统计
            cache = _MESSAGE_CACHE[f"bot_{user_id}"]
            message_count = {"今日接收": cache["received"], "今日发送": cache["sent"]}

        bot_info_list.append({
            "nickname": nickname,
            "user_id": user_id,
            "avatar_url": avatar_url,
            "platform": platform.system(),
            "bot_version": f"NoneBot2 ({adapter_name})",
            "bot_runtime": runtime_str,
            "count_contacts": count_contacts,
            "message_count": message_count,
        })

    return bot_info_list


async def _collect_hardware_info() -> List[Dict[str, Any]]:
    """收集硬件信息"""
    def _sync_collect():
        # CPU
        monitor = get_monitor()
        monitor_data = monitor.get_current_data()
        cpu_percent = monitor_data.cpu_history[-1][1] if monitor_data and monitor_data.cpu_history else 0.0
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        cpu_freq_str = f"{cpu_freq.current:.0f}MHz" if cpu_freq else "N/A"

        # 内存
        mem = psutil.virtual_memory()
        # 交换分区
        swap = psutil.swap_memory()

        visual_data = [
            {
                "title": "CPU",
                "inner": f"{cpu_percent:.1f}%",
                "percentage": {
                    "per": _calc_dashoffset(cpu_percent),
                    "color": _get_color_by_percent(cpu_percent),
                },
                "info": [f"{cpu_count} 核心", f"频率: {cpu_freq_str}"],
            },
            {
                "title": "RAM",
                "inner": f"{mem.percent:.1f}%",
                "percentage": {
                    "per": _calc_dashoffset(mem.percent),
                    "color": _get_color_by_percent(mem.percent),
                },
                "info": [f"已用: {_format_bytes(mem.used)}", f"总量: {_format_bytes(mem.total)}"],
            },
        ]

        if swap.percent > 0:
            visual_data.append({
                "title": "SWAP",
                "inner": f"{swap.percent:.1f}%",
                "percentage": {
                    "per": _calc_dashoffset(swap.percent),
                    "color": _get_color_by_percent(swap.percent),
                },
                "info": [f"已用: {_format_bytes(swap.used)}", f"总量: {_format_bytes(swap.total)}"],
            })

        return visual_data

    return await asyncio.to_thread(_sync_collect)


async def _collect_disk_info() -> Dict[str, Any]:
    """收集磁盘信息"""
    def _sync_collect():
        partitions = psutil.disk_partitions()
        disks_size = []
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks_size.append({
                    "mount": part.mountpoint,
                    "size": _format_bytes(usage.total),
                    "used": _format_bytes(usage.used),
                    "use": usage.percent,
                    "color": _get_color_by_percent(usage.percent),
                })
            except Exception:
                continue

        # 磁盘IO
        monitor = get_monitor()
        data = monitor.get_current_data()
        disks_io = []
        if data:
            disks_io.append({
                "name": "总计",
                "rIO_sec": _format_bytes(data.disk_read_speed),
                "wIO_sec": _format_bytes(data.disk_write_speed),
            })

        return {"disksSize": disks_size, "disksIo": disks_io}

    return await asyncio.to_thread(_sync_collect)


async def _collect_network_info() -> Dict[str, Any]:
    """收集网络信息"""
    monitor = get_monitor()
    data = monitor.get_current_data()

    if not data:
        return {}

    # 格式化速度和流量
    def split_size(s: str) -> Tuple[str, str]:
        parts = s.split()
        return (parts[0], parts[1]) if len(parts) == 2 else (s, "")

    upload_speed = _format_bytes(data.upload_speed)
    download_speed = _format_bytes(data.download_speed)
    upload_total = _format_bytes(data.total_upload)
    download_total = _format_bytes(data.total_download)

    up_size, up_suffix = split_size(upload_speed)
    down_size, down_suffix = split_size(download_speed)
    up_total_size, up_total_suffix = split_size(upload_total)
    down_total_size, down_total_suffix = split_size(download_total)

    # 图表数据
    chart_data = {
        "network": {
            "upload": [[int(t * 1000), u] for t, u, _ in data.network_history],
            "download": [[int(t * 1000), d] for t, _, d in data.network_history],
        }
    }

    # 网卡速度
    interfaces = await _collect_network_interfaces()
    # 网站测试
    website_tests = await _collect_website_latency()

    return {
        "speed": {
            "speed": {
                "upload": {"size": up_size, "suffix": up_suffix},
                "download": {"size": down_size, "suffix": down_suffix},
            },
            "traffic": {
                "upload": {"size": up_total_size, "suffix": up_total_suffix},
                "download": {"size": down_total_size, "suffix": down_total_suffix},
            },
        },
        "chart_data": chart_data,
        "interfaces": interfaces,
        "website_tests": website_tests,
    }


async def _collect_network_interfaces() -> List[Dict[str, Any]]:
    """收集网卡速度（只显示物理网卡）"""
    def _sync_collect():
        interfaces = []
        net_io = psutil.net_io_counters(pernic=True)
        monitor = get_monitor()
        data = monitor.get_current_data()

        if not data:
            return interfaces

        # 过滤虚拟网卡
        for iface_name, _ in net_io.items():
            # 跳过回环、Docker、虚拟网桥等
            if any([
                iface_name.startswith("lo"),
                iface_name.startswith("docker"),
                iface_name.startswith("br-"),
                iface_name.startswith("veth"),
                iface_name.startswith("virbr"),
                iface_name.startswith("vmnet"),
            ]):
                continue

            interfaces.append({
                "name": iface_name,
                "upload_speed": _format_bytes(data.upload_speed),
                "download_speed": _format_bytes(data.download_speed),
            })

        return interfaces

    return await asyncio.to_thread(_sync_collect)


async def _collect_website_latency() -> List[Dict[str, Any]]:
    """测试网站延迟"""
    test_sites = [
        {"name": "Baidu", "url": "https://www.baidu.com"},
        {"name": "Google", "url": "https://www.google.com"},
    ]

    async def test_site(site: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        try:
            client = await get_shared_async_client()
            response = await client.get(site["url"], follow_redirects=True, timeout=5.0)
            latency_ms = int((time.time() - start_time) * 1000)
            return {
                "name": site["name"],
                "status": response.status_code,
                "delay": f"{latency_ms}ms",
                "success": True,
            }
        except TimeoutError:
            return {"name": site["name"], "status": "timeout", "delay": "timeout", "success": False}
        except Exception:
            return {"name": site["name"], "status": "error", "delay": "error", "success": False}

    return await asyncio.gather(*[test_site(site) for site in test_sites])


async def _collect_system_info() -> List[Dict[str, Any]]:
    """收集系统信息（FastFetch）"""
    def _sync_collect():
        info_items = []

        # OS
        info_items.append({"first": "OS", "tail": f"{platform.system()} {platform.release()} {platform.machine()}"})

        # Host
        info_items.append({"first": "Host", "tail": socket.gethostname()})

        # Kernel
        info_items.append({"first": "Kernel", "tail": platform.release()})

        # Uptime
        uptime_str = _format_runtime(time.time() - psutil.boot_time())
        info_items.append({"first": "Uptime", "tail": uptime_str})

        # Packages
        info_items.append({"first": "Packages", "tail": "N/A"})

        # Shell
        import os
        shell = os.environ.get("SHELL", "PowerShell / CMD" if platform.system() == "Windows" else "Unknown")
        info_items.append({"first": "Shell", "tail": shell})

        # Display
        info_items.append({"first": "Display (VGA-1)", "tail": "N/A"})

        # Cursor
        info_items.append({"first": "Cursor", "tail": "Default"})

        # Terminal
        info_items.append({"first": "Terminal", "tail": os.environ.get("TERM", "Unknown")})

        # CPU
        cpu_name = platform.processor() or "Unknown"
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        cpu_info = f"{cpu_name} ({cpu_count}) @ {cpu_freq.max / 1000:.2f} GHz" if cpu_freq else f"{cpu_name} ({cpu_count})"
        info_items.append({"first": "CPU", "tail": cpu_info})

        # GPU
        info_items.append({"first": "GPU", "tail": "N/A"})

        # Memory
        mem = psutil.virtual_memory()
        info_items.append({"first": "Memory", "tail": f"{_format_bytes(mem.used)} / {_format_bytes(mem.total)} ({int(mem.percent)}%)"})

        # Swap
        swap = psutil.swap_memory()
        info_items.append({"first": "Swap", "tail": f"{_format_bytes(swap.used)} / {_format_bytes(swap.total)} ({int(swap.percent)}%)"})

        # Disk
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                mount_name = part.mountpoint.replace("\\", "/")
                info_items.append({"first": f"Disk ({mount_name})", "tail": f"{_format_bytes(usage.used)} / {_format_bytes(usage.total)} ({int(usage.percent)}%)"})
            except Exception:
                continue

        # Local IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            iface_name = "unknown"
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address == local_ip:
                        iface_name = iface
                        break

            info_items.append({"first": f"Local IP ({iface_name})", "tail": local_ip})
        except Exception:
            pass

        # Locale
        try:
            import locale
            loc = locale.getdefaultlocale()
            locale_str = f"{loc[0]}.{loc[1]}" if loc[1] else str(loc[0])
            info_items.append({"first": "Locale", "tail": locale_str})
        except Exception:
            pass

        return info_items

    return await asyncio.to_thread(_sync_collect)


async def _collect_process_info() -> Dict[str, Any]:
    """收集进程信息。"""
    process_data = get_monitor().get_process_data()
    top_cpu = process_data.get("top_cpu", [])
    top_mem = process_data.get("top_mem", [])
    status_counts = process_data.get("status_counts", {})
    total_count = process_data.get("total_count", 0)

    return {
        'total_count': total_count,
        'status_counts': status_counts,
        'top_mem': top_mem,
        'top_cpu': top_cpu
    }


# ========== HTML渲染 ==========


def _get_background_base64() -> str:
    """获取背景图片的base64编码"""
    bg_path = _BG_DIR / _DEFAULT_BG_FILE
    if not bg_path.exists():
        return ""

    try:
        with open(bg_path, "rb") as f:
            img_data = f.read()
        base64_str = base64.b64encode(img_data).decode("utf-8")
        # 判断图片类型
        suffix = bg_path.suffix.lstrip(".")
        return f"data:image/{suffix};base64,{base64_str}"
    except Exception as e:
        logger.warning(f"[bot_status] 读取背景图片失败: {e}")
        return ""


async def _build_status_html(data: Dict[str, Any]) -> str:
    """构建状态HTML"""
    bot_info_list = data.get("bot_info_list", [])
    hardware = data.get("hardware", [])
    disks = data.get("disks", {})
    network = data.get("network", {})
    processes = data.get("processes", {})
    system_info = data.get("system_info", [])
    current_time = data.get("time", "")

    # 获取背景图片
    background_url = _get_background_base64()

    chart_data_json = json.dumps(network.get("chart_data", {}))

    # 构建背景样式
    background_style = ""
    if background_url:
        background_style = f".container {{ background-image: url('{background_url}'); }}"

    # 机器人数量
    bot_count = len(bot_info_list)

    # 决定布局类型
    use_two_column = bot_count > 4

    # 左侧：机器人列表
    bot_section = _render_bot_info(bot_info_list)

    # 右侧：其他信息
    info_section = f"""
        {_render_hardware(hardware, current_time)}
        {_render_process_info(processes)}
        {_render_disks(disks)}
        {_render_network(network, chart_data_json)}
        {_render_system_info(system_info)}
    """

    # 根据布局类型生成 HTML
    if use_two_column:
        # 两列布局
        container_class = "container two-column-layout"
        main_content = f"""
        <div class="layout-two-column">
            <div class="left-column">{bot_section}</div>
            <div class="right-column">{info_section}</div>
        </div>
        """
    else:
        # 单列布局
        container_class = "container single-column-layout"
        main_content = f"""
        <div class="layout-single-column">
            {bot_section}
            {info_section}
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot状态</title>
    <style>{_get_yenai_css()}{background_style}</style>
</head>
<body>
    <div class="{container_class}">
        {main_content}
    </div>
</body>
</html>"""
    return html


def _render_bot_info(bot_info_list: List[Dict[str, Any]]) -> str:
    """渲染Bot信息"""
    html_parts = []
    for bot in bot_info_list:
        contacts_html = "".join([f'<span>{label}: {value}</span>' for label, value in bot.get("count_contacts", {}).items()])
        messages_html = "".join([f'<span>{label}: {value}</span>' for label, value in bot.get("message_count", {}).items()])

        html_parts.append(f"""
        <div class="box">
            <div class="botInfo">
                <div class="avatar-box">
                    <div class="avatar">
                        <img src="{bot.get('avatar_url', '')}" alt="avatar">
                    </div>
                    <div class="info">
                        <div class="onlineStatus" style="width:15px;height:15px;border-radius:50%;background:#44b700;"></div>
                        <div class="platform">{bot.get('platform', 'Unknown')}</div>
                    </div>
                </div>
                <div class="header">
                    <h1>{bot.get('nickname', 'Bot')}</h1>
                    <hr noshade>
                    <p>
                        <span style="background: #d799de">🤖 {bot.get('bot_version', 'NoneBot2')}</span>
                        <span style="background: #CBC7C8">⏰ Bot已运行 {bot.get('bot_runtime', '0秒')}</span>
                    </p>
                    <p>{contacts_html}{messages_html}</p>
                </div>
            </div>
        </div>
        """)

    return "\n".join(html_parts)


def _render_hardware(hardware: List[Dict[str, Any]], current_time: str) -> str:
    """渲染硬件信息"""
    if not hardware:
        return ""

    items_html = []
    for item in hardware:
        info_lines = "".join([f"<p>{info}</p>" for info in item.get("info", [])])
        items_html.append(f"""
        <li class="li">
            <div class="container-box" data-num="{item['inner']}" id="box">
                <div class="circle-outer"></div>
                <svg><circle id="circle" stroke="{item['percentage']['color']}" style="stroke-dashoffset:{item['percentage']['per']}"></circle></svg>
            </div>
            <article><summary>{item['title']}</summary>{info_lines}</article>
        </li>
        """)

    return f"""
    <div class="box" data-boxInfo="主硬件">
        <ul class="mainHardware">{"".join(items_html)}</ul>
    </div>
    """


def _render_disks(disks: Dict[str, Any]) -> str:
    """渲染磁盘信息"""
    disks_size = disks.get("disksSize", [])
    disks_io = disks.get("disksIo", [])

    if not disks_size:
        return ""

    size_html = []
    for disk in disks_size:
        size_html.append(f"""
        <li class="HardDisk_li">
            <div class="word mount">{disk['mount']}</div>
            <div class="progress">
                <div class="word">{disk['used']} / {disk['size']}</div>
                <div class="current" style="width:{disk['use']}%;background:{disk['color']}"></div>
            </div>
            <div class="percentage">{disk['use']}%</div>
        </li>
        """)

    io_html = []
    for io_item in disks_io:
        io_html.append(f"""
        <div class="speed">
            <p>{io_item['name']}</p>
            <p>读 {io_item['rIO_sec']}/s | 写 {io_item['wIO_sec']}/s</p>
        </div>
        """)

    return f"""
    <div class="box memory" data-boxInfo="磁盘">
        <ul>{"".join(size_html)}</ul>
        {"".join(io_html)}
    </div>
    """


def _render_network(network: Dict[str, Any], chart_data_json: str) -> str:
    """渲染网络信息"""
    if not network:
        return ""

    speed = network.get("speed", {})
    if not speed:
        return ""

    speed_data = speed.get("speed", {})
    traffic_data = speed.get("traffic", {})
    upload = speed_data.get("upload", {})
    download = speed_data.get("download", {})
    upload_traffic = traffic_data.get("upload", {})
    download_traffic = traffic_data.get("download", {})

    # 网卡信息
    interfaces_html = ""
    for iface in network.get("interfaces", []):
        interfaces_html += f"""
        <div class="speed">
            <p>{iface['name']}</p>
            <p>↑ {iface['upload_speed']}/s | ↓ {iface['download_speed']}/s</p>
        </div>
        """

    # 网站测试 - 一行展示
    website_tests_html = ""
    website_tests = network.get("website_tests", [])
    if website_tests:
        test_items = []
        for test in website_tests:
            color = "#46971d" if test['success'] else "#d73403"
            test_items.append(f'{test["name"]}：<span style="color:{color};">{test["delay"]}</span>')
        website_tests_html = f'<div class="speed"><p>延迟</p><p>{" | ".join(test_items)}</p></div>'

    return f"""
    <div class="box" data-boxInfo="网络">
        <div class="networkInfo">
            <div class="title"><span class="icon">🌐</span>网络状态</div>
            <div class="chartBox"><div id="Chart" style="height: 250px"></div></div>
            <div class="data">
                <div class="upload">
                    <div class="icon">⬆️</div>
                    <div class="info">
                        <p class="text">上传速度</p>
                        <p class="networkData">{upload.get('size', '0')}<span class="unit">{upload.get('suffix', 'B')}/s</span></p>
                    </div>
                </div>
                <div class="download">
                    <div class="icon">⬇️</div>
                    <div class="info">
                        <p class="text">下载速度</p>
                        <p class="networkData">{download.get('size', '0')}<span class="unit">{download.get('suffix', 'B')}/s</span></p>
                    </div>
                </div>
                <div class="uploadVolume">
                    <div class="icon">📤</div>
                    <div class="info">
                        <p class="text">上传量</p>
                        <p class="networkData">{upload_traffic.get('size', '0')}<span class="unit">{upload_traffic.get('suffix', 'B')}</span></p>
                    </div>
                </div>
                <div class="downloadVolume">
                    <div class="icon">📥</div>
                    <div class="info">
                        <p class="text">下载量</p>
                        <p class="networkData">{download_traffic.get('size', '0')}<span class="unit">{download_traffic.get('suffix', 'B')}</span></p>
                    </div>
                </div>
            </div>
        </div>
        {interfaces_html}
        {website_tests_html}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <script>
        var chartData = {chart_data_json};
        var chart = echarts.init(document.getElementById('Chart'));
        chart.setOption({{
            backgroundColor: 'transparent',
            tooltip: {{
                trigger: 'axis',
                axisPointer: {{ type: 'cross' }},
                formatter: function(params) {{
                    let result = new Date(params[0].value[0]).toLocaleTimeString() + '<br/>';
                    params.forEach(param => {{
                        result += param.marker + param.seriesName + ': ' + (param.value[1] / 1024).toFixed(2) + ' KB/s<br/>';
                    }});
                    return result;
                }}
            }},
            legend: {{ data: ['上行', '下行'], textStyle: {{ color: '#333' }} }},
            grid: {{ left: '3%', right: '4%', bottom: '10%', containLabel: true }},
            xAxis: {{
                type: 'time',
                boundaryGap: false,
                axisLabel: {{
                    color: '#333',
                    rotate: 45,
                    formatter: function(value) {{
                        var date = new Date(value);
                        return date.getHours().toString().padStart(2, '0') + ':' +
                               date.getMinutes().toString().padStart(2, '0');
                    }}
                }},
                axisLine: {{ lineStyle: {{ color: '#999' }} }}
            }},
            yAxis: {{
                type: 'value',
                axisLabel: {{ color: '#333', formatter: function(value) {{ return (value / 1024).toFixed(0) + ' KB/s'; }} }},
                axisLine: {{ lineStyle: {{ color: '#999' }} }},
                splitLine: {{ lineStyle: {{ color: 'rgba(0,0,0,0.1)' }} }}
            }},
            series: [
                {{ name: '上行', type: 'line', areaStyle: {{ color: 'rgba(255, 99, 132, 0.3)' }}, showSymbol: false, smooth: true, data: chartData.network.upload, lineStyle: {{ color: '#ff6384' }}, itemStyle: {{ color: '#ff6384' }} }},
                {{ name: '下行', type: 'line', areaStyle: {{ color: 'rgba(54, 162, 235, 0.3)' }}, showSymbol: false, smooth: true, data: chartData.network.download, lineStyle: {{ color: '#36a2eb' }}, itemStyle: {{ color: '#36a2eb' }} }}
            ]
        }});
    </script>
    """


def _render_system_info(system_info: List[Dict[str, Any]]) -> str:
    """渲染系统信息"""
    if not system_info:
        return ""

    items_html = "".join([f'<div class="speed"><p>{item["first"]}</p><p>{item["tail"]}</p></div>' for item in system_info])

    return f"""
    <div class="box" data-boxInfo="FastFetch">
        <div class="title"><span class="icon">📊</span>系统信息</div>
        {items_html}
    </div>
    """


def _render_process_info(processes: Dict[str, Any]) -> str:
    """渲染进程信息"""
    if not processes:
        return ""

    total_count = processes.get('total_count', 0)
    status_counts = processes.get('status_counts', {})
    top_mem = processes.get('top_mem', [])
    top_cpu = processes.get('top_cpu', [])

    # 渲染状态统计 - 全部放一行
    status_items = [f"{status}：{count}" for status, count in status_counts.items()]
    all_info = f"总进程数：{total_count}　　" + "　　".join(status_items)

    # 渲染内存 TOP5 - 去掉序号
    mem_rows = []
    for proc in top_mem:
        mem_rows.append(f"""
        <div class="speed">
            <p style="flex: 1 1 70%; text-align: left;">{proc['name']}</p>
            <p style="flex: 0 0 30%; max-width: 30%;">{proc['mem_str']}</p>
        </div>
        """)

    # 渲染 CPU TOP5 - 去掉序号
    cpu_rows = []
    for proc in top_cpu:
        cpu_rows.append(f"""
        <div class="speed">
            <p style="flex: 1 1 70%; text-align: left;">{proc['name']}</p>
            <p style="flex: 0 0 30%; max-width: 30%;">{proc['cpu_percent']:.1f}%</p>
        </div>
        """)

    return f"""
    <div class="box" data-boxInfo="进程信息">
        <div class="title"><span class="icon">⚙️</span>进程信息</div>
        <div class="process-summary-line">{all_info}</div>
        <div class="process-top-container">
            <div class="process-top-column">
                <div class="subtitle">内存占用 TOP5</div>
                {"".join(mem_rows)}
            </div>
            <div class="process-top-column">
                <div class="subtitle">CPU 占用 TOP5</div>
                {"".join(cpu_rows)}
            </div>
        </div>
    </div>
    """


def _get_yenai_css() -> str:
    """获取yenai样式"""
    return """
        :root {
            --bg: rgba(255, 255, 255);
            --back-bg: #c5ccda;
            --w: 110px;
            --back-shadow: #fff;
            --gap: 8px;
            --inner: calc(var(--w) - var(--gap));
            --text-color: #514f4f;
            --stroke: 13px;
            --circle: calc(var(--inner) - var(--stroke));
            --center: calc(var(--circle) - var(--stroke));
            --high-color: #d73403;
            --medium-color: #ffa500;
            --low-color: #84A0DF;
            --purple: #B4A0D8;
            --orange: #e0be92;
            --blue: #aad5e6;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { margin: 0; padding: 0; background-color: #e9e9e9; color: #000; }
        .container {
            background-color: #e9e9e9;
            background-position: center top;
            background-repeat: no-repeat;
            background-size: cover;
            margin: 0 auto;
            padding: 20px;
        }
        .container.two-column-layout { min-width: 1300px; }
        .container.single-column-layout { max-width: 690px; }
        .container > .box:nth-child(2) { margin-top: 0; }
        li { list-style: none; }
        .box { margin: 20px 10px; position: relative; break-inside: avoid; display: block; }
        .box:not(.clear-style) { border-radius: 18px; background: rgba(255, 255, 255, 0.45); box-shadow: 0 8px 32px 0 rgba(51, 51, 52, 0.5), inset 3px 3px 10px 0px rgba(255, 255, 255, 0.8); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.18); padding: 10px; }
        .box .time { position: absolute; right: 20px; top: -30px; font-size: 11px; color: #0e0e0e; text-shadow: 1px 1px 2px #fff; font-weight: 700; }

        /* Bot信息 */
        .botInfo { display: flex; justify-content: flex-start; align-items: center; }
        .botInfo .avatar-box { display: flex; align-items: center; justify-content: center; flex-direction: column; margin-right: 5px; }
        .botInfo .avatar-box .avatar { position: relative; height: 100px; width: 100px; border-radius: 50%; margin: 0 20px; }
        .botInfo .avatar-box .avatar img { width: 100%; height: 100%; border-radius: 50%; }
        .botInfo .avatar-box .avatar::before { content: ""; position: absolute; inset: 0; z-index: -1; margin: -5px; border-radius: 50%; box-shadow: -2px 2px 5px 1px rgba(8, 8, 8, 0.5); }
        .botInfo .avatar-box .info { display: flex; align-items: center; justify-content: center; margin-top: 10px; }
        .botInfo .avatar-box .info .onlineStatus { width: 15px; height: 15px; margin-right: 5px; }
        .botInfo .avatar-box .info .platform { line-height: 15px; font-size: 11px; }
        .botInfo .header { font-size: 14px; width: 100%; color: #000; }
        .botInfo .header hr { margin: 5px 0px; }
        .botInfo .header p { margin-top: 10px; display: flex; flex-wrap: wrap; row-gap: 10px; }
        .botInfo .header p span { border-radius: 5px; box-shadow: -2px 2px 3px rgba(8, 8, 8, 0.5); padding: 0px 5px; margin-right: 8px; white-space: nowrap; }
        .botInfo .header p:nth-child(3) span:nth-child(1) { background: #d799de; }
        .botInfo .header p:nth-child(3) span:nth-child(2) { background: #CBC7C8; }
        .botInfo .header p:nth-child(4) span:nth-child(1), .botInfo .header p:nth-child(4) span:nth-child(4) { background: var(--purple); }
        .botInfo .header p:nth-child(4) span:nth-child(2), .botInfo .header p:nth-child(4) span:nth-child(5) { background: var(--orange); }
        .botInfo .header p:nth-child(4) span:nth-child(3), .botInfo .header p:nth-child(4) span:nth-child(6) { background: var(--blue); }
        .botInfo .header h1 { display: inline-block; color: #000; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        /* 硬件 */
        .mainHardware { width: 100%; display: flex; justify-content: space-around; }
        .mainHardware .container-box { width: var(--w); height: var(--w); position: relative; }
        .mainHardware .container-box::after { content: attr(data-num); position: absolute; font-size: 20px; left: 50%; top: 50%; transform: translate(-50%, -50%); color: var(--text-color); font-weight: 700; }
        .mainHardware .circle-outer { position: relative; width: 100%; height: 100%; border-radius: 50%; background: var(--bg); }
        .mainHardware .circle-outer::before { width: var(--inner); height: var(--inner); box-shadow: inset 8px 8px 10px var(--back-bg), inset -4px -4px 8px var(--back-shadow); }
        .mainHardware .circle-outer::after { width: var(--center); height: var(--center); box-shadow: 6px 6px 8px var(--back-bg), -2px -2px 8px var(--back-shadow); }
        .mainHardware .circle-outer::before, .mainHardware .circle-outer::after { content: ""; position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); border-radius: 50%; background: var(--bg); }
        .mainHardware svg { width: 100%; height: 100%; position: absolute; left: 0; top: 0; z-index: 1; transform: rotate(-90deg); }
        .mainHardware svg circle { cy: calc(var(--circle) / 2); cx: calc(var(--circle) / 2); r: calc(var(--circle) / 2); fill: none; stroke-linecap: round; position: absolute; --z: calc(var(--w) / 2); --c: calc(var(--circle) / 2); transform: translate(calc(var(--z) - var(--c)), calc(var(--z) - var(--c))); stroke-dasharray: calc(3.14 * var(--circle)); stroke-dashoffset: calc(3.14 * var(--circle)); stroke-width: var(--stroke); }
        .mainHardware article { width: 100px; text-align: center; display: flex; flex-direction: column; align-items: center; white-space: nowrap; }
        .mainHardware article summary { font-size: 22px; margin-top: 5px; font-weight: 700; }
        .mainHardware article p { font-size: 12px; color: #2f4f4f; margin-top: 5px; }
        .mainHardware .li { display: flex; flex-direction: column; align-items: center; }

        /* 磁盘 */
        .memory ul { list-style: none; }
        .memory li { margin: 10px; display: flex; }
        .memory .progress { flex: 1; height: 25px; background: #c1c1c1; margin: 0 5px; position: relative; border-radius: 8px; overflow: hidden; border: 1px solid #929292; }
        .memory .progress .current { background: #90ee90; height: 25px; border-radius: inherit; }
        .memory .progress .word { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); white-space: nowrap; }
        .HardDisk_li { display: flex; }
        .HardDisk_li .mount { min-width: 15px; max-width: 3em; line-height: 25px; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .HardDisk_li .percentage { min-width: 2em; line-height: 25px; text-align: center; }
        .speed { display: flex; align-items: center; justify-content: space-between; gap: 30px; height: 25px; padding: 0px 10px; font-size: 16px; line-height: 1; }
        .speed p { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .speed p:first-child { flex: 0 0 32%; max-width: 32%; }
        .speed p:last-child { flex: 1 1 68%; text-align: right; font-weight: 700; color: #333; }

        /* 网络 */
        .box[data-boxInfo="网络"] .title { display: flex; align-items: center; padding: 10px; padding-top: 5px; border-bottom: 2px solid rgba(180, 180, 182, 0.67); margin-bottom: 10px; }
        .box[data-boxInfo="网络"] .networkInfo .chartBox, .box[data-boxInfo="网络"] .networkInfo .data > div { border: 1px solid rgba(180, 180, 182, 0.67); background-color: rgba(244, 244, 245, 0.58); }
        .box[data-boxInfo="网络"] .networkInfo .chartBox { padding: 5px 10px; margin: 20px; border-radius: 10px; }
        .box[data-boxInfo="网络"] .networkInfo .data { display: grid; margin: 20px; grid-template-columns: repeat(4, 1fr); gap: 10px; white-space: nowrap; }
        .box[data-boxInfo="网络"] .networkInfo .data > div { border-radius: 5px; padding: 7px; display: flex; align-items: center; }
        .box[data-boxInfo="网络"] .networkInfo .data > div .icon { width: 30px; height: 30px; margin-right: 10px; font-size: 24px; display: flex; align-items: center; justify-content: center; }
        .box[data-boxInfo="网络"] .networkInfo .data > div .info { font-size: 13px; }
        .box[data-boxInfo="网络"] .networkInfo .data > div .info .text { color: #696d70; margin-bottom: 3px; }
        .box[data-boxInfo="网络"] .networkInfo .data > div .info .networkData { font-size: 15px; font-weight: 700; }
        .box[data-boxInfo="网络"] .networkInfo .data > div .info .networkData .unit { color: #696d70; font-weight: 500; font-size: 12px; }
        .box[data-boxInfo="网络"] .psTest .title { display: flex; align-items: center; padding: 10px; padding-top: 15px; border-bottom: 2px solid rgba(180, 180, 182, 0.67); margin-bottom: 10px; }
        .box[data-boxInfo="网络"] .psTest .data { display: flex; flex-direction: column; font-size: 17px; margin: 0px 20px; margin-bottom: 10px; }
        .box[data-boxInfo="网络"] .psTest .data .row { display: flex; flex: 1; }
        .box[data-boxInfo="网络"] .psTest .data .header-row { font-size: 18px; color: #494d53; font-weight: 700; }
        .box[data-boxInfo="网络"] .psTest .data .cell { flex: 1; padding: 5px; text-align: center; }
        .box[data-boxInfo="网络"] .psTest .data .cell:first-child { flex: 3; text-align: left; }

        /* FastFetch */
        .box[data-boxInfo="FastFetch"] .title { display: flex; align-items: center; padding: 10px; padding-top: 5px; border-bottom: 2px solid rgba(180, 180, 182, 0.67); margin-bottom: 10px; }
        .box[data-boxInfo="FastFetch"] .speed { padding: 6px 10px; min-height: 28px; font-size: 14px; }

        .title, .header-row { font-weight: 700; }

        /* 进程信息 */
        .box[data-boxInfo="进程信息"] .title { display: flex; align-items: center; padding: 10px; padding-top: 5px; border-bottom: 2px solid rgba(180, 180, 182, 0.67); margin-bottom: 10px; }
        .box[data-boxInfo="进程信息"] .process-summary-line { padding: 5px 10px; font-size: 15px; line-height: 1.8; word-spacing: 0.2em; }
        .box[data-boxInfo="进程信息"] .subtitle { font-weight: 700; font-size: 14px; padding: 8px 10px; border-bottom: 1px solid rgba(180, 180, 182, 0.5); margin-bottom: 5px; }
        /* 2. 将 margin-top: 15px 改为 0px 或 5px，减少与上方元素的距离 */
        .box[data-boxInfo="进程信息"] .process-top-container { display: flex; gap: 15px; margin-top: 5px; }
        .box[data-boxInfo="进程信息"] .process-top-column { flex: 1; }

        /* 布局 */
        .layout-two-column { display: flex; gap: 20px; }
        .layout-two-column .left-column { flex: 1; }
        .layout-two-column .right-column { flex: 1; }
        .layout-single-column { max-width: 650px; margin: 0 auto; }
    """


# ========== 图片渲染 ==========


async def _render_html_to_image(html: str) -> bytes:
    """渲染HTML为图片"""
    browser = await _get_browser()
    page = await browser.new_page(viewport={"width": 650, "height": 100})

    try:
        page.set_default_timeout(5000)
        # 设置路由以处理头像图片的跨域问题
        async def handle_route(route):
            await route.continue_()

        await page.route("**/*", handle_route)
        await page.set_content(html, wait_until="networkidle", timeout=10000)

        try:
            await page.wait_for_selector("#Chart", timeout=2000)
            await asyncio.sleep(0.5)
        except Exception:
            await asyncio.sleep(0.5)

        return await page.screenshot(full_page=True, type="png", timeout=10000)
    finally:
        await page.close()


async def _get_browser() -> Browser:
    """获取Browser实例"""
    global _BROWSER, _PW

    if _BROWSER is not None:
        return _BROWSER

    async with _BROWSER_INIT_LOCK:
        if _BROWSER is not None:
            return _BROWSER

        pw_local = None
        browser_local = None
        try:
            pw_local = await async_playwright().start()
            browser_local = await pw_local.chromium.launch(headless=True)
            _PW = pw_local
            _BROWSER = browser_local
            return _BROWSER
        except Exception:
            if browser_local:
                try:
                    await browser_local.close()
                except Exception:
                    pass
            if pw_local:
                try:
                    await pw_local.stop()
                except Exception:
                    pass
            raise


# ========== 工具函数 ==========


def _format_runtime(seconds: float) -> str:
    """格式化运行时间"""
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}时")
    if minutes > 0:
        parts.append(f"{minutes}分")
    if secs > 0 or not parts:
        parts.append(f"{secs}秒")

    return "".join(parts)


def _format_bytes(bytes_value: float) -> str:
    """格式化字节数"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def _calc_dashoffset(percent: float) -> float:
    """计算SVG圆环dashoffset"""
    return 440 - (440 * percent / 100)


def _get_color_by_percent(percent: float) -> str:
    """根据百分比返回颜色"""
    if percent >= 90:
        return "#d73403"
    elif percent >= 70:
        return "#ffa500"
    else:
        return "#84A0DF"


# ========== 资源清理 ==========


_driver = get_driver()


def _today() -> str:
    """返回当前日期字符串。"""
    return datetime.now().strftime("%Y-%m-%d")


async def _ensure_today_cache() -> None:
    """跨天时重置本地收发统计。"""
    global _MESSAGE_CACHE_DATE
    today = _today()
    if _MESSAGE_CACHE_DATE == today:
        return
    async with _MESSAGE_CACHE_LOCK:
        if _MESSAGE_CACHE_DATE != today:
            _MESSAGE_CACHE.clear()
            _MESSAGE_CACHE_DATE = today


@_driver.on_startup
async def _setup_driver():
    """启动时设置驱动启动时间"""
    global _MESSAGE_CACHE_DATE
    _driver._start_time = time.time()

    # 读取消息统计缓存
    if _MESSAGE_CACHE_FILE.exists():
        try:
            with open(_MESSAGE_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cache_date = str(data.get("date") or "")
            cache_bots = data.get("bots") if cache_date == _today() else {}
            async with _MESSAGE_CACHE_LOCK:
                _MESSAGE_CACHE.clear()
                if isinstance(cache_bots, dict):
                    _MESSAGE_CACHE.update(cache_bots)
                _MESSAGE_CACHE_DATE = _today()
            logger.info(f"[bot_status] 已加载消息统计缓存: {len(_MESSAGE_CACHE)} 个Bot")
        except Exception as e:
            logger.error(f"[bot_status] 读取消息统计缓存失败: {e}")


@_driver.on_shutdown
async def _shutdown_renderer():
    """关闭渲染器并保存消息统计"""
    global _BROWSER, _PW

    # 保存消息统计缓存
    try:
        await _ensure_today_cache()
        async with _MESSAGE_CACHE_LOCK:
            cache_data = {"date": _MESSAGE_CACHE_DATE, "bots": dict(_MESSAGE_CACHE)}
        with open(_MESSAGE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logger.info(f"[bot_status] 已保存消息统计缓存: {len(cache_data['bots'])} 个Bot")
    except Exception as e:
        logger.error(f"[bot_status] 保存消息统计失败: {e}")

    # 关闭浏览器
    if _BROWSER:
        try:
            # 检查浏览器是否已连接
            if _BROWSER.is_connected():
                await _BROWSER.close()
            else:
                logger.debug("[bot_status] Browser已断开连接，跳过关闭")
        except Exception as e:
            # 如果底层传输已关闭，这是预期内的，只记录debug级别
            if "closed=True" in str(e) or "handler is closed" in str(e):
                logger.debug(f"[bot_status] Browser底层传输已关闭: {e}")
            else:
                logger.error(f"[bot_status] 关闭Browser失败: {e}")
        finally:
            _BROWSER = None

    if _PW:
        try:
            await _PW.stop()
        except Exception as e:
            # Playwright 停止时的异常也降级为 warning
            logger.warning(f"[bot_status] 停止Playwright时出现异常（可忽略）: {e}")
        finally:
            _PW = None


# ========== 消息统计 ==========


def _classify_chat(event: Any) -> Optional[Tuple[str, str]]:
    """识别消息类型并提取目标ID，返回 (chat_type, target_id) 或 None"""
    if event is None:
        return None

    # 排除频道消息（QQ适配器）
    if hasattr(event, "guild_id") and hasattr(event, "channel_id"):
        return None

    # OneBot v11 适配器
    if group_id := getattr(event, "group_id", None):
        return ("group", str(group_id))
    if user_id := getattr(event, "user_id", None):
        return ("private", str(user_id))

    # QQ 官方适配器
    if group_target := (
        getattr(event, "group_openid", None) or getattr(event, "group_code", None)
    ):
        return ("group", str(group_target))
    if private_target := (
        getattr(event, "user_openid", None) or _get_nested(event, "author.user_openid")
    ):
        return ("private", str(private_target))

    return None


def _get_nested(obj: Any, path: str) -> Any:
    """安全提取嵌套属性"""
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _stats_api_url() -> str:
    """读取消息统计服务地址。"""
    try:
        cfg = get_manager().get_system()
        value = str((cfg.get("console") or {}).get("stats_api_url") or "").strip()
        return value.rstrip("/") or _DEFAULT_STATS_API_URL
    except Exception:
        return _DEFAULT_STATS_API_URL


async def _report_to_central_api(chat_type: str, bot_id: str, target_id: str) -> None:
    """向中央API上报计数"""
    if not bot_id or not target_id:
        return
    try:
        url = f"{_stats_api_url()}/increment/{quote(chat_type)}/{quote(bot_id)}/{quote(target_id)}"
        client = await get_shared_async_client()
        await client.post(url, timeout=5.0)
    except Exception as e:
        logger.warning(f"[bot_status] 中央API上报失败: {e}")


@_driver.on_bot_connect
async def _hook_bot_methods(bot: Bot):
    """Hook Bot方法统计消息收发"""
    if getattr(bot, "__bot_status_patched__", False):
        return

    # 初始化缓存
    await _ensure_today_cache()
    cache_key = f"bot_{bot.self_id}"
    async with _MESSAGE_CACHE_LOCK:
        if cache_key not in _MESSAGE_CACHE:
            _MESSAGE_CACHE[cache_key] = {"received": 0, "sent": 0}

    # Hook handle_event 统计接收
    if hasattr(bot, "handle_event"):
        original_handle_event = bot.handle_event

        @wraps(original_handle_event)
        async def patched_handle_event(event: Event) -> None:
            result = await original_handle_event(event)

            # 统计接收消息
            if classified := _classify_chat(event):
                try:
                    await _ensure_today_cache()
                    async with _MESSAGE_CACHE_LOCK:
                        _MESSAGE_CACHE[f"bot_{bot.self_id}"]["received"] += 1
                except Exception as e:
                    logger.error(f"[bot_status] 接收消息统计失败: bot={bot.self_id} error={e}")

            return result

        bot.handle_event = patched_handle_event

    # Hook send 统计发送
    if hasattr(bot, "send"):
        original_send = bot.send

        @wraps(original_send)
        async def patched_send(event: Any, message: Any, *args: Any, **kwargs: Any) -> Any:
            result = await original_send(event, message, *args, **kwargs)

            # 统计发送消息并上报
            if classified := _classify_chat(event):
                chat_type, target_id = classified
                try:
                    await _ensure_today_cache()
                    async with _MESSAGE_CACHE_LOCK:
                        _MESSAGE_CACHE[f"bot_{bot.self_id}"]["sent"] += 1
                except Exception as e:
                    logger.error(f"[bot_status] 发送消息统计失败: bot={bot.self_id} error={e}")

                # 异步上报中央API（不阻塞）
                asyncio.create_task(_report_to_central_api(chat_type, bot.self_id, target_id))

            return result

        bot.send = patched_send

    setattr(bot, "__bot_status_patched__", True)
    logger.info(f"[bot_status] 已为 Bot {bot.self_id} 安装消息统计补丁")
