import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./keitaro.db")


class Base(DeclarativeBase):
    pass


class CampaignOperation(Base):
    __tablename__ = "campaign_operations"

    alias: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_json: Mapped[str] = mapped_column(Text)
    campaign_id: Mapped[int | None]
    state: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


Session = sessionmaker(create_engine(DATABASE_URL), expire_on_commit=False)
