"""Phase 4: Limit Order Execution tests for BinanceClient.

Tests for:
- place_limit_order() constructs correct request parameters
- get_best_bid_ask() parses response correctly
- _format_quantity() and _format_price() helpers
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestPlaceLimitOrder:
    """Test place_limit_order constructs correct request parameters."""

    def test_place_limit_order_constructs_correct_params(self):
        """Verify place_limit_order builds correct params with _signed_post."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            # Mock _signed_post to capture the params
            with patch.object(client, "_signed_post") as mock_signed:
                mock_signed.return_value = {
                    "orderId": 12345,
                    "status": "NEW",
                    "price": "50000.00",
                }

                # Mock _format_quantity and _format_price
                with patch.object(client, "_format_quantity", return_value=0.01):
                    with patch.object(client, "_format_price", return_value=50000.0):
                        result = client.place_limit_order(
                            symbol="BTCUSDT",
                            side="BUY",
                            quantity=0.01,
                            price=50000.0,
                            time_in_force="GTC",
                            reduce_only=False,
                        )

                # Verify _signed_post was called with correct params
                mock_signed.assert_called_once()
                call_args = mock_signed.call_args
                assert call_args[0][0] == "/fapi/v1/order"

                params = call_args[0][1]
                assert params["symbol"] == "BTCUSDT"
                assert params["side"] == "BUY"
                assert params["type"] == "LIMIT"
                assert params["quantity"] == 0.01
                assert params["price"] == 50000.0
                assert params["timeInForce"] == "GTC"
                # reduce_only=False should NOT add reduceOnly param
                assert "reduceOnly" not in params

                assert result["orderId"] == 12345
                assert result["status"] == "NEW"

    def test_place_limit_order_with_reduce_only(self):
        """Verify reduce_only=True adds reduceOnly parameter."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            with patch.object(client, "_signed_post") as mock_signed:
                mock_signed.return_value = {"orderId": 12345, "status": "NEW"}

                with patch.object(client, "_format_quantity", return_value=0.01):
                    with patch.object(client, "_format_price", return_value=50000.0):
                        client.place_limit_order(
                            symbol="BTCUSDT",
                            side="SELL",
                            quantity=0.01,
                            price=50000.0,
                            time_in_force="GTC",
                            reduce_only=True,
                        )

                params = mock_signed.call_args[0][1]
                assert params["reduceOnly"] == "true"

    def test_place_limit_order_uses_helper_formatters(self):
        """Verify place_limit_order uses _format_quantity and _format_price."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            with patch.object(client, "_signed_post") as mock_signed:
                mock_signed.return_value = {"orderId": 12345, "status": "NEW"}

                # Verify formatters are called
                with patch.object(client, "_format_quantity") as mock_fmt_qty:
                    with patch.object(client, "_format_price") as mock_fmt_price:
                        mock_fmt_qty.return_value = 0.123
                        mock_fmt_price.return_value = 50123.45

                        client.place_limit_order(
                            symbol="ETHUSDT",
                            side="BUY",
                            quantity=0.123456,
                            price=50123.456,
                        )

                        mock_fmt_qty.assert_called_once_with("ETHUSDT", 0.123456)
                        mock_fmt_price.assert_called_once_with("ETHUSDT", 50123.456)


class TestGetBestBidAsk:
    """Test get_best_bid_ask parses response correctly."""

    def test_get_best_bid_ask_parses_response(self):
        """Verify get_best_bid_ask correctly parses bookTicker response."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            # Mock _get to return bookTicker response
            with patch.object(client, "_get") as mock_get:
                mock_get.return_value = {
                    "symbol": "BTCUSDT",
                    "bidPrice": "50000.50",
                    "bidQty": "1.234",
                    "askPrice": "50001.00",
                    "askQty": "0.567",
                    "time": 1234567890123,
                }

                bid, ask = client.get_best_bid_ask("BTCUSDT")

                # Verify correct endpoint was called
                call_args = mock_get.call_args
                url = call_args[0][0]
                params = call_args[0][1]
                assert "/fapi/v1/ticker/bookTicker" in url
                assert params["symbol"] == "BTCUSDT"

                # Verify values are parsed as floats
                assert bid == 50000.50
                assert ask == 50001.00

    def test_get_best_bid_ask_different_symbols(self):
        """Verify get_best_bid_ask works for different symbols."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            test_cases = [
                ("BTCUSDT", 50000.0, 50010.0),
                ("ETHUSDT", 3000.0, 3001.0),
                ("SOLUSDT", 150.0, 150.25),
            ]

            for symbol, expected_bid, expected_ask in test_cases:
                with patch.object(client, "_get") as mock_get:
                    mock_get.return_value = {
                        "symbol": symbol,
                        "bidPrice": str(expected_bid),
                        "bidQty": "1.0",
                        "askPrice": str(expected_ask),
                        "askQty": "1.0",
                    }

                    bid, ask = client.get_best_bid_ask(symbol)

                    assert bid == expected_bid
                    assert ask == expected_ask


class TestFormatHelpers:
    """Test _format_quantity and _format_price helpers."""

    def test_format_quantity_with_step_size(self):
        """Verify _format_quantity floors to step size."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            # Mock get_symbol_filters
            with patch.object(client, "get_symbol_filters") as mock_filters:
                mock_filters.return_value = {"step_size": 0.001}

                # Quantity should be floored to 3 decimal places
                result = client._format_quantity("BTCUSDT", 0.123456)
                assert result == 0.123

                mock_filters.return_value = {"step_size": 0.01}
                result = client._format_quantity("BTCUSDT", 0.129)
                assert result == 0.12

    def test_format_quantity_no_step_size(self):
        """Verify _format_quantity returns original when no step size."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            with patch.object(client, "get_symbol_filters") as mock_filters:
                mock_filters.return_value = {}

                result = client._format_quantity("BTCUSDT", 0.123456)
                assert result == 0.123456

    def test_format_price_with_tick_size(self):
        """Verify _format_price rounds to tick size."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            with patch.object(client, "get_symbol_filters") as mock_filters:
                # Tick size of 0.01 means prices should be rounded to 2 decimal places
                mock_filters.return_value = {"tick_size": 0.01}

                result = client._format_price("BTCUSDT", 50000.123)
                assert result == 50000.12

                # Test rounding up
                result = client._format_price("BTCUSDT", 50000.126)
                assert result == 50000.13

    def test_format_price_no_tick_size(self):
        """Verify _format_price returns original when no tick size."""
        with patch("quant.data.binance_client.get_binance_config") as mock_config:
            mock_config.return_value = MagicMock(
                base_url="https://fapi.binance.com",
                api_key="test_key",
                api_secret="test_secret",
                recv_window=5000,
            )
            from quant.data.binance_client import BinanceClient

            client = BinanceClient()

            with patch.object(client, "get_symbol_filters") as mock_filters:
                mock_filters.return_value = {}

                result = client._format_price("BTCUSDT", 50000.123456)
                assert result == 50000.123456


class TestExecutionServicePartialFillHandling:
    """Test partial fill handling in execution service."""

    def test_check_partial_fills_skips_if_not_live(self):
        """Partial fill check should be skipped for paper mode."""
        import asyncio

        from quant_v2.execution.service import RoutedExecutionService, SessionRequest

        service = RoutedExecutionService()
        request = SessionRequest(user_id=123, live=False)

        # Start a paper session
        asyncio.run(service.start_session(request))

        # Check partial fills - should return empty for paper mode
        result = asyncio.run(service.check_and_handle_partial_fills(123))
        assert result == []

    def test_check_partial_fills_skips_if_no_session(self):
        """Partial fill check should return empty if no session exists."""
        import asyncio

        from quant_v2.execution.service import RoutedExecutionService

        service = RoutedExecutionService()

        result = asyncio.run(service.check_and_handle_partial_fills(999))
        assert result == []

    def test_check_partial_fills_respects_one_hour_cooldown(self):
        """Partial fill check should only run once per hour."""
        import asyncio
        from datetime import datetime, timezone

        from quant_v2.execution.service import (
            RoutedExecutionService,
            SessionRequest,
            _SessionState,
        )

        service = RoutedExecutionService()

        # Create a mock state with live mode
        mock_state = _SessionState(
            request=SessionRequest(user_id=456, live=True),
            adapter=MagicMock(),
            mode="live",
            snapshot=MagicMock(),
            effective_risk_policy=MagicMock(),
            last_partial_fill_check=datetime.now(timezone.utc),  # Just checked
        )

        service._sessions[456] = mock_state

        # Mock the adapter methods
        mock_state.adapter.get_open_orders = MagicMock(return_value=[])
        mock_state.adapter.cancel_order = MagicMock()

        # Should return empty since we just checked
        result = asyncio.run(service.check_and_handle_partial_fills(456))
        assert result == []
        # get_open_orders should not be called
        mock_state.adapter.get_open_orders.assert_not_called()
