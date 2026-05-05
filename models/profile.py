from datetime import datetime, timezone

from database import Base
from sqlalchemy import Column, DateTime, Float, Index, Integer, String
from uuid6 import uuid7


class Profile(Base):
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=lambda: str(uuid7()))
    name = Column(String, nullable=False)
    gender = Column(String, nullable=False)
    gender_probability = Column(Float, nullable=False)
    age = Column(Integer, nullable=False)
    age_group = Column(String, nullable=False)
    country_id = Column(String(2), nullable=False)
    country_name = Column(String, nullable=False)
    country_probability = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_profiles_gender", "gender"),
        Index("idx_profiles_country_id", "country_id"),
        Index("idx_profiles_age_group", "age_group"),
        Index("idx_profiles_age", "age"),
        Index(
            "idx_profiles_country_gender_age",
            "country_id",
            "gender",
            "age_group",
            "age",
        ),
    )
