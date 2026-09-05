"""统一会话信息兼容层。"""

from .interface import Interface, get_interface
from .model import Member, MuteInfo, Role, Scene, SceneType, Session, SupportScope, User
from .orm import (
    BotModel,
    SceneModel,
    SessionModel,
    UserModel,
    get_bot_persist_id,
    get_scene_persist_id,
    get_session_persist_id,
    get_user_persist_id,
)
from .params import QryItrface, QueryInterface, UniSession, Uninfo, get_session

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
