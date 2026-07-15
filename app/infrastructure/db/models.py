from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BIGINT,
    BOOLEAN,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BIGINT, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    telegram_name: Mapped[str] = mapped_column(String(255))
    balance_available: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    balance_frozen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_admin: Mapped[bool] = mapped_column(BOOLEAN, default=False, server_default=text("false"))
    is_blocked: Mapped[bool] = mapped_column(BOOLEAN, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", server_default="ACTIVE")
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_screen: Mapped[str] = mapped_column(String(64), default="HOME", server_default="HOME")
    screen_stack: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    app_chat_id: Mapped[int | None] = mapped_column(BIGINT)
    app_message_id: Mapped[int | None] = mapped_column(BIGINT)
    app_image_file_id: Mapped[str | None] = mapped_column(Text)

    plates: Mapped[list[Plate]] = relationship(
        back_populates="owner", foreign_keys="Plate.owner_id"
    )
    transactions: Mapped[list[Transaction]] = relationship(back_populates="user")

    __table_args__ = (
        CheckConstraint("balance_available >= 0", name="available_nonnegative"),
        CheckConstraint("balance_frozen >= 0", name="frozen_nonnegative"),
    )


class Admin(TimestampMixin, Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    granted_by: Mapped[int | None] = mapped_column(BIGINT)


class UserBlock(TimestampMixin, Base):
    __tablename__ = "user_blocks"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    admin_id: Mapped[int | None] = mapped_column(BIGINT)
    reason: Mapped[str] = mapped_column(Text)
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Plate(TimestampMixin, Base):
    __tablename__ = "plates"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    plate_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_by_state: Mapped[bool] = mapped_column(
        BOOLEAN, default=True, server_default=text("true")
    )
    reserved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    owner: Mapped[User | None] = relationship(back_populates="plates", foreign_keys=[owner_id])
    sales: Mapped[list[Sale]] = relationship(back_populates="plate")
    auctions: Mapped[list[Auction]] = relationship(back_populates="plate")
    ownership_history: Mapped[list[OwnershipHistory]] = relationship(back_populates="plate")


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    plate_id: Mapped[int] = mapped_column(ForeignKey("plates.id", ondelete="RESTRICT"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    buyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    price: Mapped[int] = mapped_column(Integer)
    commission: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), index=True, default="ACTIVE", server_default="ACTIVE"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plate: Mapped[Plate] = relationship(back_populates="sales")

    __table_args__ = (
        CheckConstraint("price > 0", name="sale_price_positive"),
        CheckConstraint("commission >= 0 AND commission <= price", name="sale_commission_range"),
        Index(
            "uq_sales_one_active_per_plate",
            "plate_id",
            unique=True,
            postgresql_where=text("status IN ('ACTIVE', 'RESERVED')"),
        ),
    )


class Auction(TimestampMixin, Base):
    __tablename__ = "auctions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    plate_id: Mapped[int] = mapped_column(ForeignKey("plates.id", ondelete="RESTRICT"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    start_price: Mapped[int] = mapped_column(Integer)
    current_price: Mapped[int] = mapped_column(Integer)
    highest_bidder_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(
        String(32), index=True, default="ACTIVE", server_default="ACTIVE"
    )
    is_finished: Mapped[bool] = mapped_column(BOOLEAN, default=False, server_default=text("false"))
    is_cancelled: Mapped[bool] = mapped_column(BOOLEAN, default=False, server_default=text("false"))

    plate: Mapped[Plate] = relationship(back_populates="auctions")
    bids: Mapped[list[Bid]] = relationship(back_populates="auction")

    __table_args__ = (
        CheckConstraint("start_price > 0", name="auction_start_price_positive"),
        CheckConstraint("current_price >= start_price", name="auction_current_price_valid"),
        Index(
            "uq_auctions_one_open_per_plate",
            "plate_id",
            unique=True,
            postgresql_where=text("NOT is_finished AND NOT is_cancelled"),
        ),
    )


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    auction_id: Mapped[int] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT"), index=True
    )
    bidder_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    anti_sniping_applied: Mapped[bool] = mapped_column(
        BOOLEAN, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    auction: Mapped[Auction] = relationship(back_populates="bids")

    __table_args__ = (CheckConstraint("amount > 0", name="bid_amount_positive"),)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    balance_before: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    frozen_before: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    frozen_after: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reference_type: Mapped[str] = mapped_column(String(64))
    reference_id: Mapped[int | None] = mapped_column(BIGINT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    user: Mapped[User] = relationship(back_populates="transactions")


class OwnershipHistory(Base):
    __tablename__ = "ownership_history"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    plate_id: Mapped[int] = mapped_column(ForeignKey("plates.id", ondelete="RESTRICT"), index=True)
    old_owner_id: Mapped[int | None] = mapped_column(BIGINT)
    new_owner_id: Mapped[int | None] = mapped_column(BIGINT)
    operation_type: Mapped[str] = mapped_column(String(64))
    price: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    plate: Mapped[Plate] = relationship(back_populates="ownership_history")


class BlacklistedSeries(Base):
    __tablename__ = "blacklisted_series"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    series: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_by: Mapped[int | None] = mapped_column(BIGINT)

    __table_args__ = (
        UniqueConstraint("country_code", "series", name="blacklisted_series_country_series"),
    )


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
    updated_by: Mapped[int | None] = mapped_column(BIGINT)


class Banner(TimestampMixin, Base):
    __tablename__ = "banners"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    image_file_id: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(BOOLEAN, default=True, server_default=text("true"))


class BotCard(Base):
    __tablename__ = "bot_cards"

    card_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    image_file_id: Mapped[str | None] = mapped_column(Text)
    banner_id: Mapped[int | None] = mapped_column(ForeignKey("banners.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(BOOLEAN, default=True, server_default=text("true"))
    updated_by: Mapped[int | None] = mapped_column(BIGINT)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    is_sent: Mapped[bool] = mapped_column(
        BOOLEAN, default=False, server_default=text("false"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BIGINT, index=True)
    action_type: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(BIGINT)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Backup(TimestampMixin, Base):
    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255), unique=True)
    storage_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BIGINT)
    created_by: Mapped[int | None] = mapped_column(BIGINT)


class PaymentIntent(TimestampMixin, Base):
    __tablename__ = "payment_intents"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    payload: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), index=True, default="PENDING", server_default="PENDING"
    )
    plate_id: Mapped[int | None] = mapped_column(ForeignKey("plates.id", ondelete="RESTRICT"))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", ondelete="RESTRICT"))
    country_code: Mapped[str | None] = mapped_column(String(8))
    requested_plate_number: Mapped[str | None] = mapped_column(String(20))
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("amount > 0", name="payment_amount_positive"),)


class StateEmissionReservation(Base):
    __tablename__ = "state_emission_reservations"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(8))
    plate_number: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    payment_intent_id: Mapped[int] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="CASCADE"), unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("country_code", "plate_number", name="state_emission_reservation_plate"),
    )


class PlatformLedger(Base):
    __tablename__ = "platform_ledger"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str] = mapped_column(String(64))
    reference_id: Mapped[int | None] = mapped_column(BIGINT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
