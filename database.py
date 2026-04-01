"""
数据库管理模块

架构：
- 全局索引数据库 (data/index.db)：用户列表、Tunnel池、新闻池、配置
- 单用户数据库 (data/users/{client_id}.db)：日志、留言、AI会话、Token使用
"""

import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from datetime import datetime, date

from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy import event

from models import (
    Client,
    Tunnel,
    NewsItem,
    ServerConfig,
    Log,
    Message,
    AISession,
    TokenUsage,
    FeaturedPhoto,
    PhotoDescription,
    SpeechRecord,
    PhotoTag,
    Badge,
    ProfileFeedback,
    DailyCard,
)


# 数据目录
DATA_DIR = Path(__file__).parent / "data"
USERS_DIR = DATA_DIR / "users"
INDEX_DB_PATH = DATA_DIR / "index.db"

# 确保目录存在
DATA_DIR.mkdir(exist_ok=True)
USERS_DIR.mkdir(exist_ok=True)

# 全局索引数据库引擎
_index_engine = None


def get_index_engine():
    """获取全局索引数据库引擎"""
    global _index_engine
    if _index_engine is None:
        _index_engine = create_engine(f"sqlite:///{INDEX_DB_PATH}")
        _apply_sqlite_pragmas(_index_engine)
        SQLModel.metadata.create_all(_index_engine)
    return _index_engine


def get_index_session():
    """获取全局索引数据库会话"""
    engine = get_index_engine()
    return Session(engine)


@contextmanager
def index_session():
    """全局索引数据库会话上下文管理器"""
    session = get_index_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_user_db_path(client_id: str) -> Path:
    """获取用户数据库文件路径"""
    return USERS_DIR / f"{client_id}.db"


def get_user_engine(client_id: str):
    """获取用户数据库引擎"""
    db_path = get_user_db_path(client_id)
    engine = create_engine(f"sqlite:///{db_path}")
    _apply_sqlite_pragmas(engine)

    # 创建表
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Log.__table__,
            Message.__table__,
            AISession.__table__,
            TokenUsage.__table__,
            FeaturedPhoto.__table__,
            PhotoDescription.__table__,
            SpeechRecord.__table__,
            PhotoTag.__table__,
            Badge.__table__,
            ProfileFeedback.__table__,
            DailyCard.__table__,
        ],
    )

    return engine


def _apply_sqlite_pragmas(engine):
    """为 SQLite 引擎设置 WAL 和超时，提升并发写入稳定性"""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=5000;")
        finally:
            cursor.close()


def get_user_session(client_id: str):
    """获取用户数据库会话"""
    engine = get_user_engine(client_id)
    return Session(engine)


@contextmanager
def user_session(client_id: str):
    """用户数据库会话上下文管理器"""
    session = get_user_session(client_id)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ==================== 客户端操作 ====================


def create_client(client_id: str, baby_name: str = "宝宝", **kwargs) -> Dict:
    """创建新客户端，返回字典"""
    import logging
    import traceback
    logger = logging.getLogger("czrz_server")
    
    time_fields = ["registered_at", "last_active", "last_heartbeat"]
    for field in time_fields:
        if field in kwargs and isinstance(kwargs[field], str):
            try:
                kwargs[field] = datetime.fromisoformat(kwargs[field])
            except (ValueError, TypeError):
                kwargs[field] = None

    try:
        with index_session() as session:
            client = Client(
                client_id=client_id,
                baby_name=baby_name,
                **kwargs,
            )
            session.add(client)
            session.flush()
            session.refresh(client)
            logger.info(f"[create_client] 成功创建客户端: {client_id}, baby_name: {baby_name}")
            return client.model_dump()
    except Exception as e:
        logger.error(f"[create_client] 创建客户端 {client_id} 失败: {e}")
        logger.error(traceback.format_exc())
        raise


def get_client(client_id: str) -> Optional[Dict]:
    """获取客户端信息（返回字典，避免 session 问题）"""
    with index_session() as session:
        client = session.exec(
            select(Client).where(Client.client_id == client_id)
        ).first()
        if client:
            return client.model_dump()
        return None


def update_client(client_id: str, **kwargs) -> Optional[Dict]:
    """更新客户端信息，返回字典"""
    kwargs.pop("client_id", None)

    time_fields = ["registered_at", "last_active", "last_heartbeat"]
    for field in time_fields:
        if field in kwargs and isinstance(kwargs[field], str):
            try:
                kwargs[field] = datetime.fromisoformat(kwargs[field])
            except (ValueError, TypeError):
                kwargs[field] = None

    with index_session() as session:
        client = session.exec(
            select(Client).where(Client.client_id == client_id)
        ).first()
        if client:
            for key, value in kwargs.items():
                if hasattr(client, key):
                    setattr(client, key, value)
            session.flush()
            session.refresh(client)
            return client.model_dump()
        return None


def delete_client(client_id: str) -> bool:
    """删除客户端（包括用户数据库文件）"""
    with index_session() as session:
        client = session.exec(
            select(Client).where(Client.client_id == client_id)
        ).first()
        if client:
            session.delete(client)

    # 删除用户数据库文件
    db_path = get_user_db_path(client_id)
    if db_path.exists():
        db_path.unlink()

    return True


def list_clients() -> List[Dict]:
    """获取所有客户端列表（返回字典列表）"""
    with index_session() as session:
        clients = session.exec(select(Client)).all()
        return [c.model_dump() for c in clients]


def get_all_clients_dict() -> Dict[str, Dict]:
    """获取所有客户端的字典格式（兼容旧代码）"""
    with index_session() as session:
        clients = session.exec(select(Client)).all()
        return {c.client_id: c.model_dump() for c in clients}


# ==================== Tunnel 操作 ====================


def create_tunnel(
    tunnel_id: str, tunnel_secret: str, account_tag: str, subdomain: str
) -> Dict:
    """创建新 Tunnel，返回字典"""
    with index_session() as session:
        tunnel = Tunnel(
            tunnel_id=tunnel_id,
            tunnel_secret=tunnel_secret,
            account_tag=account_tag,
            subdomain=subdomain,
            created_at=datetime.now(),
        )
        session.add(tunnel)
        session.flush()
        session.refresh(tunnel)
        return tunnel.model_dump()


def get_available_tunnel() -> Optional[Dict]:
    """获取一个可用的 Tunnel，返回字典"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.status == "available")
        ).first()
        if tunnel:
            return tunnel.model_dump()
        return None


def allocate_tunnel(tunnel_id: str, client_id: str) -> Optional[Dict]:
    """分配 Tunnel 给客户端，返回字典"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.tunnel_id == tunnel_id)
        ).first()
        if tunnel and tunnel.status == "available":
            tunnel.status = "allocated"
            tunnel.client_id = client_id
            tunnel.allocated_at = datetime.now()
            session.flush()
            session.refresh(tunnel)
            return tunnel.model_dump()
        return None


def release_tunnel(tunnel_id: str) -> Optional[Dict]:
    """释放 Tunnel，返回字典"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.tunnel_id == tunnel_id)
        ).first()
        if tunnel:
            tunnel.status = "available"
            tunnel.client_id = None
            tunnel.released_at = datetime.now()
            session.flush()
            session.refresh(tunnel)
            return tunnel.model_dump()
        return None


def list_tunnels() -> List[Dict]:
    """获取所有 Tunnel，返回字典列表"""
    with index_session() as session:
        tunnels = session.exec(select(Tunnel)).all()
        result = []
        for t in tunnels:
            data = t.model_dump()
            # 处理日期：空字符串转换为None，None转换为空字符串
            for field in ["created_at", "allocated_at", "released_at"]:
                val = data.get(field)
                if val == "":
                    data[field] = ""
                elif val is not None:
                    data[field] = str(val)
                else:
                    data[field] = ""
            result.append(data)
        return result


def get_tunnel_by_id(tunnel_id: str) -> Optional[Dict]:
    """根据 tunnel_id 获取 Tunnel，返回字典"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.tunnel_id == tunnel_id)
        ).first()
        if tunnel:
            return tunnel.model_dump()
        return None


def get_tunnel_by_client(client_id: str) -> Optional[Dict]:
    """根据 client_id 获取已分配的 Tunnel，返回字典"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.client_id == client_id)
        ).first()
        if tunnel:
            return tunnel.model_dump()
        return None


def update_tunnel(tunnel_id: str, **kwargs) -> Optional[Dict]:
    """更新 Tunnel 信息，返回字典"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.tunnel_id == tunnel_id)
        ).first()
        if tunnel:
            for key, value in kwargs.items():
                if hasattr(tunnel, key):
                    setattr(tunnel, key, value)
            session.flush()
            session.refresh(tunnel)
            return tunnel.model_dump()
        return None


def delete_tunnel(tunnel_id: str) -> bool:
    """删除 Tunnel"""
    with index_session() as session:
        tunnel = session.exec(
            select(Tunnel).where(Tunnel.tunnel_id == tunnel_id)
        ).first()
        if tunnel:
            session.delete(tunnel)
            return True
        return False


# ==================== 新闻操作 ====================


def add_news(
    title: str,
    source: str,
    date_str: str,
    url: str = None,
    region: str = "国内",
    category: str = None,
    is_major: bool = False,
) -> Dict:
    """添加新闻，返回字典"""
    with index_session() as session:
        news = NewsItem(
            title=title,
            source=source,
            url=url,
            date=date_str,
            region=region,
            category=category,
            is_major=is_major,
            created_at=datetime.now(),
        )
        session.add(news)
        session.flush()
        session.refresh(news)
        return news.model_dump()


def get_news_by_date(date_str: str, limit: int = 10) -> List[Dict]:
    """获取指定日期的新闻，返回字典列表"""
    with index_session() as session:
        news_list = session.exec(
            select(NewsItem).where(NewsItem.date == date_str).limit(limit)
        ).all()
        return [n.model_dump() for n in news_list]


def get_latest_news(limit: int = 10) -> List[Dict]:
    """获取最新新闻，返回字典列表"""
    with index_session() as session:
        news_list = session.exec(
            select(NewsItem).order_by(NewsItem.created_at.desc()).limit(limit)
        ).all()
        return [n.model_dump() for n in news_list]


def list_news(limit: int = 100) -> List[Dict]:
    """获取所有新闻列表，返回字典列表"""
    with index_session() as session:
        news_list = session.exec(
            select(NewsItem).order_by(NewsItem.created_at.desc()).limit(limit)
        ).all()
        return [n.model_dump() for n in news_list]


def delete_news(news_id: int) -> bool:
    """删除新闻"""
    with index_session() as session:
        news = session.exec(select(NewsItem).where(NewsItem.id == news_id)).first()
        if news:
            session.delete(news)
            return True
        return False


def delete_news_by_index(index: int) -> bool:
    """按索引删除新闻（从列表开头计数）"""
    with index_session() as session:
        news_list = session.exec(
            select(NewsItem).order_by(NewsItem.created_at.desc())
        ).all()
        if 0 <= index < len(news_list):
            session.delete(news_list[index])
            return True
        return False


def clear_old_news(days: int = 7) -> int:
    """清理指定天数之前的新闻，返回删除数量"""
    from datetime import timedelta

    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with index_session() as session:
        old_news = session.exec(
            select(NewsItem).where(NewsItem.date < cutoff_date)
        ).all()
        count = len(old_news)
        for news in old_news:
            session.delete(news)
        return count


def get_news_stats() -> Dict:
    """获取新闻统计信息"""
    with index_session() as session:
        all_news = session.exec(select(NewsItem)).all()
        total = len(all_news)
        domestic = sum(1 for n in all_news if n.region == "国内")
        international = sum(1 for n in all_news if n.region == "国际")
        return {
            "total": total,
            "domestic": domestic,
            "international": international,
        }


# ==================== 配置操作 ====================


def get_config(key: str, default: Any = None) -> Any:
    """获取配置"""
    with index_session() as session:
        config = session.exec(
            select(ServerConfig).where(ServerConfig.key == key)
        ).first()
        return config.value if config else default


def set_config(key: str, value: Dict) -> Dict:
    """设置配置，返回字典"""
    with index_session() as session:
        config = session.exec(
            select(ServerConfig).where(ServerConfig.key == key)
        ).first()
        if config:
            config.value = value
            config.updated_at = datetime.now()
        else:
            config = ServerConfig(key=key, value=value, updated_at=datetime.now())
            session.add(config)
        session.flush()
        session.refresh(config)
        return config.model_dump()


# ==================== 用户数据操作 ====================


# 日志
def save_log(
    client_id: str,
    date_str: str,
    content: str,
    weather: Dict = None,
    calendar: Dict = None,
    news: str = None,
    is_ai_generated: bool = False,
) -> Dict:
    """保存日志，返回字典"""
    with user_session(client_id) as session:
        existing = session.exec(select(Log).where(Log.date == date_str)).first()

        if existing:
            existing.content = content
            existing.updated_at = datetime.now()
            if weather:
                existing.weather_city = weather.get("city")
                existing.weather_temperature = weather.get("temperature")
                existing.weather_condition = weather.get("condition")
            if calendar:
                existing.weekday = calendar.get("weekday")
                existing.lunar = calendar.get("lunar")
            if news:
                existing.news = news
            session.flush()
            session.refresh(existing)
            return existing.model_dump()

        log = Log(
            date=date_str,
            content=content,
            weather_city=weather.get("city") if weather else None,
            weather_temperature=weather.get("temperature") if weather else None,
            weather_condition=weather.get("condition") if weather else None,
            weekday=calendar.get("weekday") if calendar else None,
            lunar=calendar.get("lunar") if calendar else None,
            news=news,
            is_ai_generated=is_ai_generated,
            generated_at=datetime.now(),
        )
        session.add(log)
        session.flush()
        session.refresh(log)
        return log.model_dump()


def get_log(client_id: str, date_str: str) -> Optional[Dict]:
    """获取日志（返回字典，避免 session detached 问题）"""
    with user_session(client_id) as session:
        log = session.exec(select(Log).where(Log.date == date_str)).first()
        if log:
            return {
                "date": log.date,
                "content": log.content,
                "weather_city": log.weather_city,
                "weather_temperature": log.weather_temperature,
                "weather_condition": log.weather_condition,
                "weekday": log.weekday,
                "lunar": log.lunar,
                "news": log.news,
                "is_ai_generated": log.is_ai_generated,
                "generated_at": log.generated_at.isoformat()
                if log.generated_at
                else None,
            }
        return None


def get_log_dict(client_id: str, date_str: str) -> Optional[Dict]:
    """获取日志（字典格式）"""
    log = get_log(client_id, date_str)
    if log:
        return {
            "date": log["date"],
            "content": log["content"],
            "weather": {
                "city": log["weather_city"],
                "temperature": log["weather_temperature"],
                "condition": log["weather_condition"],
            }
            if log["weather_city"]
            else None,
            "calendar": {
                "weekday": log["weekday"],
                "lunar": log["lunar"],
            }
            if log["weekday"]
            else None,
            "news": log["news"],
            "is_ai_generated": log["is_ai_generated"],
            "generated_at": log["generated_at"],
        }
    return None


def get_log_dates_by_month(client_id: str, year: int, month: int) -> List[str]:
    """获取指定月份有日志的日期列表"""
    with user_session(client_id) as session:
        from sqlalchemy import and_

        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"

        logs = session.exec(
            select(Log.date).where(
                and_(
                    Log.date >= start_date,
                    Log.date < end_date,
                )
            )
        ).all()

        return [log for log in logs if log]


def get_recent_logs(client_id: str, limit: int = 50) -> List[Dict]:
    """获取最近的日志列表"""
    with user_session(client_id) as session:
        logs = session.exec(select(Log).order_by(Log.date.desc()).limit(limit)).all()
        return [
            {
                "date": log.date,
                "content": log.content[:200] + "..."
                if len(log.content) > 200
                else log.content,
                "is_ai_generated": log.is_ai_generated,
                "generated_at": log.generated_at.isoformat()
                if log.generated_at
                else None,
            }
            for log in logs
        ]


def get_all_clients_stats() -> List[Dict]:
    """获取所有客户端的统计信息"""
    stats = []
    clients = list_clients()
    today = datetime.now().strftime("%Y-%m-%d")

    for client in clients:
        client_id = client.get("client_id")

        # 从用户数据库获取日志数量
        with user_session(client_id) as session:
            log_count = session.exec(select(Log)).all()
            log_count = len(log_count)

            # 获取最新日志日期
            latest_log = session.exec(
                select(Log.date).order_by(Log.date.desc()).limit(1)
            ).first()

        stats.append(
            {
                "client_id": client_id,
                "baby_name": client.get("baby_name", "未知"),
                "log_count": log_count,
                "photo_count": 0,  # 照片在客户端本地，服务端无法统计
                "latest_log": latest_log,
                "registered_at": client.get("registered_at"),
                "last_active": client.get("last_active"),
            }
        )

    # 按最新日志日期排序
    stats.sort(key=lambda x: x["latest_log"] or "", reverse=True)
    return stats


# 留言
def save_message(
    client_id: str,
    date_str: str,
    content: str,
    author: str = "家人",
    msg_type: str = "text",
    audio_path: str = None,
    audio_duration: float = None,
) -> Dict:
    """保存留言，返回字典格式"""
    with user_session(client_id) as session:
        message = Message(
            date=date_str,
            content=content,
            author=author,
            type=msg_type,
            audio_path=audio_path,
            audio_duration=audio_duration,
            created_at=datetime.now(),
        )
        session.add(message)
        session.flush()
        session.refresh(message)
        return message.model_dump()


def get_messages(client_id: str, date_str: str = None) -> List[Dict]:
    """获取留言，返回字典列表"""
    with user_session(client_id) as session:
        if date_str:
            messages = session.exec(
                select(Message).where(Message.date == date_str)
            ).all()
        else:
            messages = session.exec(select(Message)).all()
        return [m.model_dump() for m in messages]


def get_messages_dict(client_id: str, date_str: str = None) -> List[Dict]:
    """获取留言（字典格式）"""
    return get_messages(client_id, date_str)


# AI 会话
def save_ai_session(
    client_id: str,
    session_id: str,
    operation: str,
    prompt: str = None,
    response: str = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    model: str = None,
    success: bool = True,
    error_message: str = None,
    duration_ms: int = None,
    context: dict = None,
) -> Dict:
    """保存 AI 会话，返回字典"""
    with user_session(client_id) as session:
        ai_session = AISession(
            session_id=session_id,
            operation=operation,
            prompt=prompt,
            response=response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model,
            success=success,
            error_message=error_message,
            duration_ms=duration_ms,
            context=context or {},
            created_at=datetime.now(),
        )
        session.add(ai_session)
        session.flush()
        session.refresh(ai_session)

        update_client_token_summary(
            client_id, prompt_tokens, completion_tokens, total_tokens
        )

        return ai_session.model_dump()


def update_client_token_summary(
    client_id: str, prompt: int, completion: int, total: int
):
    """更新客户端 Token 汇总"""
    with index_session() as session:
        client = session.exec(
            select(Client).where(Client.client_id == client_id)
        ).first()
        if client:
            client.token_total = (client.token_total or 0) + total
            client.token_prompt = (client.token_prompt or 0) + prompt
            client.token_completion = (client.token_completion or 0) + completion


def get_ai_sessions(client_id: str, limit: int = 100) -> List[Dict]:
    """获取 AI 会话列表，返回字典列表"""
    with user_session(client_id) as session:
        sessions = session.exec(
            select(AISession).order_by(AISession.created_at.desc()).limit(limit)
        ).all()
        return [s.model_dump() for s in sessions]


def get_ai_sessions_light(client_id: str, limit: int = 100) -> List[Dict]:
    """获取 AI 会话列表（轻量版，不含prompt/response）"""
    with user_session(client_id) as session:
        sessions = session.exec(
            select(AISession).order_by(AISession.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": s.id,
                "operation": s.operation,
                "total_tokens": s.total_tokens,
                "created_at": str(s.created_at) if s.created_at else None,
            }
            for s in sessions
        ]


def get_token_usage(client_id: str, date_str: str = None) -> List[Dict]:
    """获取 Token 使用记录，返回字典列表"""
    with user_session(client_id) as session:
        if date_str:
            usages = session.exec(
                select(TokenUsage).where(TokenUsage.date == date_str)
            ).all()
        else:
            usages = session.exec(select(TokenUsage)).all()
        return [u.model_dump() for u in usages]


def save_featured_photo(
    client_id: str,
    date_str: str,
    filename: str,
    file_hash: str = None,
    ai_description: str = None,
) -> Dict:
    """保存精选照片，返回字典（先删除当天已有的，再插入新的）"""
    with user_session(client_id) as session:
        existing = session.exec(
            select(FeaturedPhoto).where(FeaturedPhoto.date == date_str)
        ).all()
        for e in existing:
            session.delete(e)

        featured = FeaturedPhoto(
            date=date_str,
            filename=filename,
            file_hash=file_hash,
            ai_description=ai_description,
            created_at=datetime.now(),
        )
        session.add(featured)
        session.flush()
        session.refresh(featured)
        return featured.model_dump()


def get_featured_photo(client_id: str, date_str: str) -> Optional[Dict]:
    """获取精选照片（返回字典，避免 session detached 问题）"""
    with user_session(client_id) as session:
        featured = session.exec(
            select(FeaturedPhoto).where(FeaturedPhoto.date == date_str)
        ).first()
        if featured:
            return {
                "filename": featured.filename,
                "file_hash": featured.file_hash,
                "ai_description": featured.ai_description,
                "date": featured.date,
                "created_at": featured.created_at.isoformat()
                if featured.created_at
                else None,
            }
        return None


# 照片描述
def save_photo_description(
    client_id: str,
    date_str: str,
    file_hash: str,
    file_path: str,
    description: str = None,
    has_baby: bool = True,
    scene: str = None,
    activity: str = None,
):
    """保存照片描述"""
    with user_session(client_id) as session:
        photo_desc = PhotoDescription(
            date=date_str,
            file_hash=file_hash,
            file_path=file_path,
            description=description,
            has_baby=has_baby,
            scene=scene,
            activity=activity,
        )
        session.add(photo_desc)


def save_photo_descriptions(client_id: str, date_str: str, photos: list):
    """批量保存照片描述（使用 UPSERT：已存在的更新，不存在的插入）"""
    with user_session(client_id) as session:
        # 获取当天已有的记录
        existing_records = session.exec(
            select(PhotoDescription).where(PhotoDescription.date == date_str)
        ).all()
        
        # 构建 path -> record 的映射
        existing_map = {r.file_path: r for r in existing_records}
        
        for photo in photos:
            file_path = photo.get("path", "")
            
            if file_path in existing_map:
                # 更新现有记录
                record = existing_map[file_path]
                record.file_hash = photo.get("hash", "")
                record.description = photo.get("description")
                record.has_baby = photo.get("has_baby", True)
                record.scene = photo.get("scene")
                record.activity = photo.get("activity")
                session.add(record)
            else:
                # 插入新记录
                photo_desc = PhotoDescription(
                    date=date_str,
                    file_hash=photo.get("hash", ""),
                    file_path=file_path,
                    description=photo.get("description"),
                    has_baby=photo.get("has_baby", True),
                    scene=photo.get("scene"),
                    activity=photo.get("activity"),
                )
                session.add(photo_desc)


def get_photo_descriptions(client_id: str, date_str: str) -> List[Dict]:
    """获取指定日期的照片描述"""
    with user_session(client_id) as session:
        photos = session.exec(
            select(PhotoDescription).where(PhotoDescription.date == date_str)
        ).all()
        return [
            {
                "path": p.file_path,
                "hash": p.file_hash,
                "description": p.description,
                "has_baby": p.has_baby,
                "scene": p.scene,
                "activity": p.activity,
            }
            for p in photos
        ]


def save_speech_record(
    client_id: str,
    date_str: str,
    video_path: str,
    transcript: str = None,
    duration: float = 0.0,
    language_analysis: dict = None,
    file_hash: str = "",
) -> Dict:
    """保存语音记录，返回字典。使用 UPSERT：已存在则更新，不存在则插入"""
    import json
    from sqlalchemy.dialects.sqlite import insert

    with user_session(client_id) as session:
        # 先检查是否已存在
        existing = session.exec(
            select(SpeechRecord).where(
                (SpeechRecord.date == date_str) & 
                (SpeechRecord.video_path == video_path)
            )
        ).first()
        
        if existing:
            # 更新现有记录
            existing.transcript = transcript
            existing.duration = duration
            existing.language_analysis = json.dumps(language_analysis, ensure_ascii=False) if language_analysis else None
            existing.file_hash = file_hash
            session.add(existing)
            session.flush()
            session.refresh(existing)
            return existing.model_dump()
        else:
            # 插入新记录
            speech = SpeechRecord(
                date=date_str,
                video_path=video_path,
                file_hash=file_hash,
                transcript=transcript,
                duration=duration,
                language_analysis=json.dumps(language_analysis, ensure_ascii=False)
                if language_analysis
                else None,
                created_at=datetime.now(),
            )
            session.add(speech)
            session.flush()
            session.refresh(speech)
            return speech.model_dump()


def get_speech_records(client_id: str, date_str: str = None) -> List[Dict]:
    """获取语音记录，返回字典列表（过滤空 transcript）"""
    import json

    with user_session(client_id) as session:
        if date_str:
            records = session.exec(
                select(SpeechRecord)
                .where(SpeechRecord.date == date_str)
                .order_by(SpeechRecord.created_at.desc())
            ).all()
        else:
            records = session.exec(
                select(SpeechRecord).order_by(SpeechRecord.created_at.desc())
            ).all()
        # 过滤掉空 transcript 的记录
        return [
            {
                "id": r.id,
                "date": r.date,
                "video_path": r.video_path,
                "transcript": r.transcript,
                "duration": r.duration,
                "language_analysis": json.loads(r.language_analysis)
                if r.language_analysis
                else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
            if r.transcript and r.transcript.strip()  # 只返回有效 transcript
        ]


# ==================== 照片标签操作 ====================


def get_photo_tag(client_id: str, filename: str) -> Optional[Dict]:
    """获取照片标签"""
    with user_session(client_id) as session:
        tag = session.exec(
            select(PhotoTag).where(PhotoTag.filename == filename)
        ).first()
        if tag:
            return {
                "tag": tag.tag,
                "note": tag.note,
                "updated_at": tag.updated_at.isoformat() if tag.updated_at else None,
            }
        return None


def set_photo_tag(client_id: str, filename: str, tag: str, note: str = "") -> bool:
    """设置照片标签"""
    try:
        with user_session(client_id) as session:
            existing = session.exec(
                select(PhotoTag).where(PhotoTag.filename == filename)
            ).first()
            if existing:
                existing.tag = tag
                existing.note = note
                existing.updated_at = datetime.now()
            else:
                photo_tag = PhotoTag(
                    client_id=client_id,
                    filename=filename,
                    tag=tag,
                    note=note,
                )
                session.add(photo_tag)
        return True
    except Exception as e:
        print(f"[ERROR] 保存标签失败: {e}")
        return False


def delete_photo_tag(client_id: str, filename: str) -> bool:
    """删除照片标签"""
    try:
        with user_session(client_id) as session:
            tag = session.exec(
                select(PhotoTag).where(PhotoTag.filename == filename)
            ).first()
            if tag:
                session.delete(tag)
                return True
        return False
    except Exception as e:
        print(f"[ERROR] 删除标签失败: {e}")
        return False


def get_all_photo_tags(client_id: str) -> Dict[str, Dict]:
    """获取所有照片标签"""
    with user_session(client_id) as session:
        tags = session.exec(select(PhotoTag)).all()
        return {
            t.filename: {
                "tag": t.tag,
                "note": t.note,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tags
        }


def get_all_tag_names(client_id: str) -> List[str]:
    """获取所有标签名称（去重）"""
    with user_session(client_id) as session:
        tags = session.exec(select(PhotoTag.tag).distinct()).all()
        return sorted([t for t in tags if t])


# 初始化数据库
def init_database():
    """初始化数据库"""
    # 创建全局索引数据库
    engine = get_index_engine()
    SQLModel.metadata.create_all(engine)

    # 迁移：添加 ai_child_id 列（如果不存在）
    with engine.connect() as conn:
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN ai_child_id VARCHAR(64)")
            print("   已添加 ai_child_id 列")
        except Exception:
            pass  # 列已存在，忽略

    print(f"✅ 数据库初始化完成: {INDEX_DB_PATH}")
    print(f"   用户数据库目录: {USERS_DIR}")


if __name__ == "__main__":
    init_database()
