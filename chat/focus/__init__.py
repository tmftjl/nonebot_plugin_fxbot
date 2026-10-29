"""Focus 共享组：多个聊天共享一条观察时间线，由同一规划器决定下一次在哪发言。"""

from .manager import focus_config, focus_mode_manager

__all__ = ["focus_config", "focus_mode_manager"]
