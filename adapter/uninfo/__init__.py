"""统一会话信息兼容层。"""

from .orm import (
    BotModel,
    UserModel,
    SceneModel,
    SessionModel,
    get_bot_persist_id,
    get_user_persist_id,
    get_scene_persist_id,
    get_session_persist_id,
)
from .model import Role, User, Scene, Member, Session, MuteInfo, SceneType, SupportScope
from .params import Uninfo, QryItrface, UniSession, QueryInterface, get_session
from .interface import Interface, get_interface

__all__ = [
    "BotModel",
    "Interface",
    "Member",
    "MuteInfo",
    "QryItrface",
    "QueryInterface",
    "Role",
    "Scene",
    "SceneModel",
    "SceneType",
    "Session",
    "SessionModel",
    "SupportScope",
    "UniSession",
    "Uninfo",
    "User",
    "UserModel",
    "get_bot_persist_id",
    "get_interface",
    "get_scene_persist_id",
    "get_session",
    "get_session_persist_id",
    "get_user_persist_id",
]
