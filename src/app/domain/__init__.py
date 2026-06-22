from app.infrastructure.db.base import Base
from app.domain.clients.models import Platform, Client
from app.domain.conversations.models import Conversation, Message
from app.domain.knowledge.models import KnowledgeBase  # 🆕

__all__ = [
    "Base",
    "Platform",
    "Client",
    "Conversation",
    "Message",
    "KnowledgeBase",
]