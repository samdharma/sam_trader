"""Unit tests for RejectionMonitorActor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from sam_trader.actors.rejection_monitor import (
    RejectionMonitorActor,
    RejectionMonitorActorConfig,
    StrategyHaltRequest,
)


@pytest.fixture
def actor_config() -> RejectionMonitorActorConfig:
    return RejectionMonitorActorConfig(
        max_consecutive=3,
        cooldown_seconds=900,
    )


@pytest.fixture
def registered_actor(
    actor_config: RejectionMonitorActorConfig,
) -> RejectionMonitorActor:
    actor = RejectionMonitorActor(actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


class TestRejectionMonitorActorConfig:
    def test_default_values(self) -> None:
        cfg = RejectionMonitorActorConfig()
        assert cfg.max_consecutive == 3
        assert cfg.cooldown_seconds == 900

    def test_custom_values(self, actor_config: RejectionMonitorActorConfig) -> None:
        assert actor_config.max_consecutive == 3
        assert actor_config.cooldown_seconds == 900


class TestRejectionMonitorActor:
    def test_is_actor_subclass(self, actor_config: RejectionMonitorActorConfig) -> None:
        actor = RejectionMonitorActor(actor_config)
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_subscribes_to_order_events(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        subs = list(registered_actor.msgbus.subscriptions())
        topics = {s.topic for s in subs}
        assert "events.order.*" in topics

    def test_on_stop_unsubscribes_from_order_events(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        subs_before = list(registered_actor.msgbus.subscriptions())
        assert any(s.topic == "events.order.*" for s in subs_before)

        registered_actor.on_stop()
        subs_after = list(registered_actor.msgbus.subscriptions())
        assert not any(s.topic == "events.order.*" for s in subs_after)

    def test_rejection_counter_increments(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        order = TestExecStubs.limit_order()
        rej = TestEventStubs.order_rejected(order)

        registered_actor.msgbus.publish(
            topic=f"events.order.{order.strategy_id}",
            msg=rej,
        )

        key = (rej.instrument_id, rej.strategy_id, rej.reason)
        assert registered_actor._counters[key]["count"] == 1

    def test_halt_request_emitted_after_threshold(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        halts: list[StrategyHaltRequest] = []
        registered_actor.msgbus.subscribe(
            topic="StrategyHaltRequest",
            handler=halts.append,
        )

        order = TestExecStubs.limit_order()
        for _ in range(3):
            rej = TestEventStubs.order_rejected(order)
            registered_actor.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=rej,
            )

        assert len(halts) == 1
        assert halts[0].instrument_id == order.instrument_id
        assert halts[0].strategy_id == order.strategy_id
        assert halts[0].reason == "ORDER_REJECTED"
        assert halts[0].count == 3

    def test_halt_not_emitted_below_threshold(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        halts: list[StrategyHaltRequest] = []
        registered_actor.msgbus.subscribe(
            topic="StrategyHaltRequest",
            handler=halts.append,
        )

        order = TestExecStubs.limit_order()
        for _ in range(2):
            rej = TestEventStubs.order_rejected(order)
            registered_actor.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=rej,
            )

        assert len(halts) == 0

    def test_halt_emitted_only_once_per_streak(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        halts: list[StrategyHaltRequest] = []
        registered_actor.msgbus.subscribe(
            topic="StrategyHaltRequest",
            handler=halts.append,
        )

        order = TestExecStubs.limit_order()
        for _ in range(5):
            rej = TestEventStubs.order_rejected(order)
            registered_actor.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=rej,
            )

        assert len(halts) == 1

    def test_cooldown_resets_streak(
        self, registered_actor: RejectionMonitorActor
    ) -> None:
        registered_actor.on_start()
        halts: list[StrategyHaltRequest] = []
        registered_actor.msgbus.subscribe(
            topic="StrategyHaltRequest",
            handler=halts.append,
        )

        order = TestExecStubs.limit_order()
        base_time = datetime(2024, 1, 8, 12, 0, 0, tzinfo=timezone.utc)

        with patch.object(
            registered_actor,
            "_now",
            return_value=base_time,
        ):
            for _ in range(2):
                rej = TestEventStubs.order_rejected(order)
                registered_actor.msgbus.publish(
                    topic=f"events.order.{order.strategy_id}",
                    msg=rej,
                )

        # After cooldown, publish two more rejections
        later = base_time + timedelta(
            seconds=registered_actor.config.cooldown_seconds + 1
        )
        with patch.object(
            registered_actor,
            "_now",
            return_value=later,
        ):
            for _ in range(2):
                rej = TestEventStubs.order_rejected(order)
                registered_actor.msgbus.publish(
                    topic=f"events.order.{order.strategy_id}",
                    msg=rej,
                )

        # Total: 2 before cooldown + 2 after cooldown = no halt (threshold=3)
        assert len(halts) == 0

        # One more rejection after cooldown should trigger halt
        with patch.object(
            registered_actor,
            "_now",
            return_value=later,
        ):
            rej = TestEventStubs.order_rejected(order)
            registered_actor.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=rej,
            )

        assert len(halts) == 1
        assert halts[0].count == 3

    def test_keys_are_isolated(self, registered_actor: RejectionMonitorActor) -> None:
        registered_actor.on_start()
        halts: list[StrategyHaltRequest] = []
        registered_actor.msgbus.subscribe(
            topic="StrategyHaltRequest",
            handler=halts.append,
        )

        order_a = TestExecStubs.limit_order(
            strategy_id=TestExecStubs.limit_order().strategy_id,
        )
        order_b = TestExecStubs.limit_order(
            strategy_id=TestExecStubs.limit_order().strategy_id,
        )

        # 2 rejections for instrument A
        for _ in range(2):
            rej = TestEventStubs.order_rejected(order_a)
            registered_actor.msgbus.publish(
                topic=f"events.order.{order_a.strategy_id}",
                msg=rej,
            )

        # 3 rejections for instrument B (different strategy)
        for _ in range(3):
            rej = TestEventStubs.order_rejected(order_b)
            registered_actor.msgbus.publish(
                topic=f"events.order.{order_b.strategy_id}",
                msg=rej,
            )

        # Only B should trigger a halt
        assert len(halts) == 1
        assert halts[0].strategy_id == order_b.strategy_id
