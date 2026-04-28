
from datetime import timezone

from database import Base
from sqlalchemy import Column, String, DateTime
from uuid6 import uuid7
from datetime import datetime, timezone


class User(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid7()))
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))