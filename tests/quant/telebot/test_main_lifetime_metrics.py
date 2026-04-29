from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quant.telebot import main as telebot_main
from quant.telebot.models import Base, User, UserContext


@pytest.fixture
def temp_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session


def test_persist_lifetime_snapshot_metrics_bootstraps_missing_demo_anchor(temp_db) -> None:
    Session = temp_db

    session = Session()
    user = User(telegram_id=123, username="testuser")
    user.context = UserContext(telegram_id=123, lifetime_demo_pnl_usd=0.0)
    session.add(user)
    session.commit()
    session.close()

    snapshot = SimpleNamespace(
        equity_usd=10_220.0,
        symbol_notional_usd={"BTCUSDT": 500.0},
        symbol_count=1,
    )

    with patch.object(telebot_main, "SessionLocal", Session):
        telebot_main._persist_lifetime_snapshot_metrics(123, live=False, snapshot=snapshot)

    session = Session()
    db_user = session.query(User).filter_by(telegram_id=123).first()
    assert db_user is not None
    assert db_user.context is not None
    assert db_user.context.lifetime_demo_pnl_usd == pytest.approx(220.0)
    assert db_user.context.last_demo_equity_usd == pytest.approx(10_220.0)
    assert db_user.context.current_demo_equity_usd == pytest.approx(10_220.0)
    assert db_user.context.current_demo_notional_usd == pytest.approx(500.0)
    assert db_user.context.current_demo_symbols == 1
    session.close()



def test_persist_lifetime_snapshot_metrics_uses_delta_when_demo_anchor_present(temp_db) -> None:
    Session = temp_db

    session = Session()
    user = User(telegram_id=456, username="testuser2")
    user.context = UserContext(
        telegram_id=456,
        lifetime_demo_pnl_usd=220.0,
        last_demo_equity_usd=10_220.0,
    )
    session.add(user)
    session.commit()
    session.close()

    snapshot = SimpleNamespace(
        equity_usd=10_305.5,
        symbol_notional_usd={"BTCUSDT": 400.0, "ETHUSDT": 125.0},
        symbol_count=2,
    )

    with patch.object(telebot_main, "SessionLocal", Session):
        telebot_main._persist_lifetime_snapshot_metrics(456, live=False, snapshot=snapshot)

    session = Session()
    db_user = session.query(User).filter_by(telegram_id=456).first()
    assert db_user is not None
    assert db_user.context is not None
    assert db_user.context.lifetime_demo_pnl_usd == pytest.approx(305.5)
    assert db_user.context.last_demo_equity_usd == pytest.approx(10_305.5)
    assert db_user.context.current_demo_equity_usd == pytest.approx(10_305.5)
    assert db_user.context.current_demo_notional_usd == pytest.approx(525.0)
    assert db_user.context.current_demo_symbols == 2
    session.close()
