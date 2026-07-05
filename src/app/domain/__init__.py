from app.domain.clients.models import Client, Platform
from app.domain.conversations.models import Conversation, Message
from app.domain.incoming.models import IncomingEvent
from app.domain.knowledge.models import KnowledgeBase  # 🆕
from app.domain.outbound.models import OutboundDelivery
from app.infrastructure.db.base import Base

__all__ = [
    "Base",
    "Platform",
    "Client",
    "Conversation",
    "Message",
    "IncomingEvent",
    "OutboundDelivery",
    "KnowledgeBase",
]
