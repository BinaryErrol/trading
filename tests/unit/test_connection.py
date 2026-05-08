"""Tests for ConnectionManager — mock ib_async, test reconnection and account verification."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import ConnectionConfig
from src.connection.manager import ConnectionManager


@pytest.fixture
def connection_config() -> ConnectionConfig:
    """Minimal connection config for testing."""
    return ConnectionConfig(
        mode="gateway",
        host="127.0.0.1",
        port=4002,
        client_id=1,
        timeout=10,
        readonly=False,
    )


@pytest.fixture
def mock_ib():
    """Mock ib_async.IB instance."""
    with patch("src.connection.manager.IB") as MockIB:
        ib_instance = MagicMock()
        ib_instance.connectAsync = AsyncMock(return_value=ib_instance)
        ib_instance.disconnect = MagicMock()
        ib_instance.isConnected = MagicMock(return_value=True)
        ib_instance.managedAccounts = MagicMock(return_value=["DU1234567"])
        ib_instance.reqCurrentTime = MagicMock()
        ib_instance.reqMktData = MagicMock()
        ib_instance.reqAccountUpdates = MagicMock()
        ib_instance.disconnectedEvent = MagicMock()
        ib_instance.disconnectedEvent.__iadd__ = MagicMock(return_value=ib_instance.disconnectedEvent)
        MockIB.return_value = ib_instance
        yield ib_instance


class TestConnect:
    """Test connect() method."""

    async def test_connect_calls_connect_async_with_config(self, connection_config, mock_ib):
        """connect() calls ib.connectAsync with correct parameters."""
        mgr = ConnectionManager(connection_config)
        await mgr.connect()

        mock_ib.connectAsync.assert_called_once_with(
            host="127.0.0.1",
            port=4002,
            clientId=1,
            timeout=10,
            readonly=False,
        )

    async def test_connect_sets_connected_flag(self, connection_config, mock_ib):
        """connect() sets _connected to True on success."""
        mgr = ConnectionManager(connection_config)
        await mgr.connect()
        assert mgr._connected is True

    async def test_connect_resets_reconnect_attempts(self, connection_config, mock_ib):
        """connect() resets _reconnect_attempts to 0."""
        mgr = ConnectionManager(connection_config)
        mgr._reconnect_attempts = 3
        await mgr.connect()
        assert mgr._reconnect_attempts == 0

    async def test_connect_verifies_account(self, connection_config, mock_ib):
        """connect() calls _verify_account after connecting."""
        mgr = ConnectionManager(connection_config)
        await mgr.connect()
        # _verify_account calls managedAccounts
        mock_ib.managedAccounts.assert_called()


class TestDisconnect:
    """Test disconnect() method."""

    async def test_disconnect_calls_ib_disconnect(self, connection_config, mock_ib):
        """disconnect() calls ib.disconnect()."""
        mgr = ConnectionManager(connection_config)
        await mgr.connect()
        await mgr.disconnect()
        mock_ib.disconnect.assert_called_once()

    async def test_disconnect_sets_connected_false(self, connection_config, mock_ib):
        """disconnect() sets _connected to False."""
        mgr = ConnectionManager(connection_config)
        await mgr.connect()
        await mgr.disconnect()
        assert mgr._connected is False


class TestReconnection:
    """Test _on_disconnected() exponential backoff logic."""

    async def test_reconnection_uses_exponential_backoff(self, connection_config, mock_ib):
        """_on_disconnected() waits with exponential backoff between retries."""
        mgr = ConnectionManager(connection_config)
        # Make first reconnect attempt succeed
        mock_ib.connectAsync = AsyncMock(return_value=mock_ib)

        delays = []
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            delays.append(seconds)
            # Don't actually sleep in tests

        with patch("src.connection.manager.asyncio.sleep", mock_sleep):
            await mgr._on_disconnected()

        # First attempt: delay = 2^1 = 2
        assert delays[0] == 2

    async def test_reconnection_succeeds_resets_state(self, connection_config, mock_ib):
        """Successful reconnection resets _reconnect_attempts and sets _connected."""
        mgr = ConnectionManager(connection_config)
        mock_ib.connectAsync = AsyncMock(return_value=mock_ib)

        with patch("src.connection.manager.asyncio.sleep", AsyncMock()):
            await mgr._on_disconnected()

        assert mgr._reconnect_attempts == 0
        assert mgr._connected is True

    async def test_all_retries_exhausted_sets_halted(self, connection_config, mock_ib):
        """After max retries, _halted is set to True."""
        mgr = ConnectionManager(connection_config)
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("src.connection.manager.asyncio.sleep", AsyncMock()):
            await mgr._on_disconnected()

        assert mgr._halted is True
        assert mgr._reconnect_attempts == 5

    async def test_all_retries_exhausted_calls_callback(self, connection_config, mock_ib):
        """After max retries, on_connection_lost callback is called."""
        callback = AsyncMock()
        mgr = ConnectionManager(connection_config, on_connection_lost=callback)
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("src.connection.manager.asyncio.sleep", AsyncMock()):
            await mgr._on_disconnected()

        callback.assert_called_once()

    async def test_halted_state_prevents_reconnection(self, connection_config, mock_ib):
        """When halted, _on_disconnected() returns immediately."""
        mgr = ConnectionManager(connection_config)
        mgr._halted = True

        await mgr._on_disconnected()
        # connectAsync should not be called
        mock_ib.connectAsync.assert_not_called()


class TestVerifyAccount:
    """Test _verify_account() method."""

    async def test_verify_account_detects_paper(self, connection_config, mock_ib):
        """Paper accounts (starting with 'D') are detected correctly."""
        mock_ib.managedAccounts.return_value = ["DU1234567"]
        mgr = ConnectionManager(connection_config)
        result = await mgr._verify_account()
        assert result["is_paper"] is True
        assert result["account_type"] == "paper"
        assert result["account_id"] == "DU1234567"

    async def test_verify_account_detects_live(self, connection_config, mock_ib):
        """Live accounts (not starting with 'D') are detected correctly."""
        mock_ib.managedAccounts.return_value = ["U1234567"]
        mgr = ConnectionManager(connection_config)
        result = await mgr._verify_account()
        assert result["is_paper"] is False
        assert result["account_type"] == "live"


class TestSubscriptions:
    """Test market data and account subscriptions."""

    async def test_subscribe_market_data(self, connection_config, mock_ib):
        """subscribe_market_data calls ib.reqMktData."""
        mgr = ConnectionManager(connection_config)
        contract = MagicMock()
        contract.symbol = "AAPL"
        mgr.subscribe_market_data(contract)
        mock_ib.reqMktData.assert_called_once_with(contract)

    async def test_subscribe_account_updates(self, connection_config, mock_ib):
        """subscribe_account_updates calls ib.reqAccountUpdates."""
        mgr = ConnectionManager(connection_config)
        mgr.subscribe_account_updates()
        mock_ib.reqAccountUpdates.assert_called_once_with(
            subscribe=True, account="DU1234567"
        )
