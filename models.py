"""
Mumu 服务端数据库模型

架构：
- 全局索引数据库 (data/index.db)：用户列表、Tunnel池、新闻池、配置
- 单用户数据库 (data/users/{client_id}.db)：日志、留言、AI会话、Token使用
"""

from datetime import datetime, date
from typing import Optional, List
from enum import Enum
from sqlmodel import SQLModel, Field, Column, JSON


# ==================== 全局索引数据库表 ====================


class ClientStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


class Client(SQLModel, table=True):
    """客户端/用户信息"""

    __tablename__ = "clients"

    client_id: str = Field(primary_key=True, max_length=64)
    baby_name: str = Field(default="宝宝", max_length=50)
    baby_gender: Optional[str] = Field(default=None, max_length=10)
    baby_birthday: Optional[str] = Field(default=None, max_length=20)
    user_city: Optional[str] = Field(default=None, max_length=50)

    # 连接信息
    ip: Optional[str] = Field(default=None, max_length=50)
    mac_address: Optional[str] = Field(default=None, max_length=20)
    client_port: int = Field(default=3000)

    # Tunnel 信息
    subdomain: Optional[str] = Field(default=None, max_length=100)
    public_url: Optional[str] = Field(default=None, max_length=200)

    # 状态
    status: str = Field(default="offline", max_length=20)
    is_paid: bool = Field(default=False)
    enabled: bool = Field(default=True)

    # 安全
    secret_key: Optional[str] = Field(default=None, max_length=64)

    # Token 使用汇总
    token_total: int = Field(default=0)
    token_prompt: int = Field(default=0)
    token_completion: int = Field(default=0)

    # 自定义 Token 限额（None 表示使用默认配额）
    custom_token_limit: Optional[int] = Field(default=None)

    # 时间戳
    registered_at: datetime = Field(default_factory=datetime.now)
    last_active: Optional[datetime] = Field(default=None)
    last_heartbeat: Optional[datetime] = Field(default=None)

    # 扩展信息 (JSON)
    extra: dict = Field(default_factory=dict, sa_column=Column(JSON))


class TunnelStatus(str, Enum):
    AVAILABLE = "available"
    ALLOCATED = "allocated"


class Tunnel(SQLModel, table=True):
    """Tunnel 池"""

    __tablename__ = "tunnels"

    id: Optional[int] = Field(default=None, primary_key=True)
    tunnel_id: str = Field(max_length=64, unique=True, index=True)
    tunnel_secret: str = Field(max_length=200)
    account_tag: str = Field(max_length=50)
    subdomain: str = Field(max_length=100)

    status: str = Field(default="available", max_length=20)
    client_id: Optional[str] = Field(default=None, max_length=64)

    dns_created: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.now)
    allocated_at: Optional[datetime] = Field(default=None)
    released_at: Optional[datetime] = Field(default=None)


class NewsItem(SQLModel, table=True):
    """新闻池"""

    __tablename__ = "news"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=500)
    source: str = Field(max_length=100)
    url: Optional[str] = Field(default=None, max_length=500)
    date: str = Field(max_length=20, index=True)

    region: Optional[str] = Field(default="国内", max_length=20)
    category: Optional[str] = Field(default=None, max_length=50)
    is_major: bool = Field(default=False)
    views: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.now)


class ServerConfig(SQLModel, table=True):
    """服务端配置"""

    __tablename__ = "server_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(max_length=100, unique=True)
    value: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=datetime.now)


# ==================== 单用户数据库表 ====================


class Log(SQLModel, table=True):
    """日志"""

    __tablename__ = "logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(max_length=20, index=True)
    content: str

    # 天气
    weather_city: Optional[str] = Field(default=None, max_length=50)
    weather_temperature: Optional[float] = Field(default=None)
    weather_condition: Optional[str] = Field(default=None, max_length=50)

    # 日历
    weekday: Optional[str] = Field(default=None, max_length=10)
    lunar: Optional[str] = Field(default=None, max_length=20)

    # 新闻
    news: Optional[str] = Field(default=None)

    # 元数据
    is_ai_generated: bool = Field(default=False)
    generated_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.now)


class Message(SQLModel, table=True):
    """留言"""

    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(max_length=20, index=True)
    content: str
    author: str = Field(default="家人", max_length=50)
    type: str = Field(default="text", max_length=20)  # text, audio

    audio_path: Optional[str] = Field(default=None, max_length=500)
    audio_duration: Optional[float] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.now)


class AISession(SQLModel, table=True):
    """AI 会话记录"""

    __tablename__ = "ai_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(max_length=50, index=True)
    operation: str = Field(
        max_length=50
    )  # generate_log, generate_theme, select_photo, etc.

    # 输入输出
    prompt: Optional[str] = Field(default=None)
    response: Optional[str] = Field(default=None)

    # Token 使用
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    # 模型信息
    model: Optional[str] = Field(default=None, max_length=100)

    # 状态
    success: bool = Field(default=True)
    error_message: Optional[str] = Field(default=None)

    duration_ms: Optional[int] = Field(default=None)

    # 上下文信息（存储操作的详细信息）
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.now, index=True)


class TokenUsage(SQLModel, table=True):
    """Token 使用记录"""

    __tablename__ = "token_usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(max_length=20, index=True)
    operation: str = Field(max_length=50)

    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.now)


class FeaturedPhoto(SQLModel, table=True):
    """精选照片记录"""

    __tablename__ = "featured_photos"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(max_length=20, index=True)
    filename: str = Field(max_length=500)
    file_hash: Optional[str] = Field(default=None, max_length=64)
    ai_description: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.now)


class PhotoDescription(SQLModel, table=True):
    """照片描述记录"""

    __tablename__ = "photo_descriptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(max_length=20, index=True)
    file_hash: str = Field(max_length=64, index=True)
    file_path: str = Field(max_length=500)
    description: Optional[str] = Field(default=None)
    has_baby: bool = Field(default=True)
    scene: Optional[str] = Field(default=None, max_length=100)
    activity: Optional[str] = Field(default=None, max_length=100)

    created_at: datetime = Field(default_factory=datetime.now)


class SpeechRecord(SQLModel, table=True):
    """语音记录"""

    __tablename__ = "speech_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(max_length=20, index=True)
    video_path: str = Field(max_length=500)
    transcript: Optional[str] = Field(default=None)
    duration: float = Field(default=0.0)
    language_analysis: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.now)


class PhotoTag(SQLModel, table=True):
    """照片标签"""

    __tablename__ = "photo_tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(max_length=100, index=True)
    filename: str = Field(max_length=500, index=True)
    tag: str = Field(max_length=100)
    note: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
