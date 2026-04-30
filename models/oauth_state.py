from sqlalchemy import Column, String, DateTime
from database import Base
from datetime import datetime, timezone


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state = Column(String, primary_key=True)
    code_challenge = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)
