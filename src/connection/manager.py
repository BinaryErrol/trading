"""IBKR connection manager with reconnection logic and account verification."""

import asyncio
from typing import Any, Callable, Awaitable

import structlog
from ib_async import IB, Contract

from src.config.settings import ConnectionConfig

logger = structlog.get_logger(__name__)


class ConnectionManager:
    """Manages IBKR connectivity via ib_async with reconnection and heartbeat."""

    def __init__(
        self,
        config: ConnectionConfig,
        on_connection_lost: Callable[[], Awaitable[None]] | None = None,
    ):
        self.config = config
        self.ib = IB()
        self._reconnect_attempts = 0
        self._max_retries = 5
        self._halted = False
        self._heartbeat_task: asyncio.Task | None = None
        self._on_connection_lost = on_connection_lost
        self._connected = False

        # Register disconnect handler
        self.ib.disconnectedEvent += self._on_disconnected_sync

    @property
    def is_connected(self) -> bool:
        """Return True if connected to IBKR."""
        return self._connected and self.ib.isConnected()

    @property
    def is_halted(self) -> bool:
        """Return True if connection is halted after max retries."""
        return self._halted

    async def connect(self) -> None:
        """Establish connection to TWS or IB Gateway."""
        logger.info(
            "connecting_to_ibkr",
            mode=self.config.mode,
            host=self.config.host,
            port=self.config.port,
            client_id=self.config.client_id,
        )
        await self.ib.connectAsync(
            host=self.config.host,
            port=self.config.port,
            clientId=self.config.client_id,
            timeout=self.config.timeout,
            readonly=self.config.readonly,
        )
        self._connected = True
        self._reconnect_attempts = 0
        self._halted = False

        await self._verify_account()
        self._start_heartbeat()
        logger.info("connected_to_ibkr", host=self.config.host, port=self.config.port)

    async def disconnect(self) -> None:
        """Gracefully disconnect from IBKR."""
        self._stop_heartbeat()
        self.ib.disconnect()
        self._connected = False
        logger.info("disconnected_from_ibkr")

    def _on_disconnected_sync(self) -> None:
        """Sync wrapper for disconnect event (ib_async fires sync events)."""
        self._connected = False
        asyncio.ensure_future(self._on_disconnected())

    async def _on_disconnected(self) -> None:
        """Handle disconnection with exponential backoff reconnection."""
        if self._halted:
            return

        logger.warning("ibkr_connection_lost", attempts_so_far=self._reconnect_attempts)

        while self._reconnect_attempts < self._max_retries:
            self._reconnect_attempts += 1
            delay = 2 ** self._reconnect_attempts  # 2, 4, 8, 16, 32 seconds
            logger.info(
                "reconnection_attempt",
                attempt=self._reconnect_attempts,
                max_retries=self._max_retries,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)

            try:
                await self.ib.connectAsync(
                    host=self.config.host,
                    port=self.config.port,
                    clientId=self.config.client_id,
                    timeout=self.config.timeout,
                    readonly=self.config.readonly,
                )
                self._connected = True
                self._reconnect_attempts = 0
                self._start_heartbeat()
                logger.info("reconnected_to_ibkr")
                return
            except Exception as exc:
                logger.error(
                    "reconnection_failed",
                    attempt=self._reconnect_attempts,
                    error=str(exc),
                )

        # All retries exhausted
        await self._on_all_retries_exhausted()

    async def _on_all_retries_exhausted(self) -> None:
        """Handle case when all reconnection attempts fail."""
        self._halted = True
        logger.error(
            "all_reconnection_attempts_exhausted",
            max_retries=self._max_retries,
            msg="Connection halted. Strategies should be stopped.",
        )
        if self._on_connection_lost:
            await self._on_connection_lost()

    async def _verify_account(self) -> dict[str, Any]:
        """Verify account permissions and log account type (paper/live)."""
        accounts = self.ib.managedAccounts()
        account_id = accounts[0] if accounts else "unknown"

        # Paper accounts typically start with 'D' prefix
        is_paper = account_id.startswith("D") if account_id != "unknown" else True
        account_type = "paper" if is_paper else "live"

        logger.info(
            "account_verified",
            account_id=account_id,
            account_type=account_type,
            total_accounts=len(accounts),
        )
        return {"account_id": account_id, "account_type": account_type, "is_paper": is_paper}

    def _start_heartbeat(self) -> None:
        """Start periodic heartbeat to maintain connection."""
        self._stop_heartbeat()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat task."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat requests to keep connection alive."""
        try:
            while True:
                await asyncio.sleep(30)  # Every 30 seconds
                if self.ib.isConnected():
                    self.ib.reqCurrentTime()
                else:
                    break
        except asyncio.CancelledError:
            pass

    def subscribe_market_data(self, contract: Contract) -> Any:
        """Subscribe to real-time market data for a contract."""
        logger.debug("subscribing_market_data", symbol=contract.symbol)
        return self.ib.reqMktData(contract)

    def subscribe_account_updates(self) -> None:
        """Subscribe to account value and position updates."""
        accounts = self.ib.managedAccounts()
        if accounts:
            self.ib.reqAccountUpdates(subscribe=True, account=accounts[0])
            logger.info("subscribed_account_updates", account=accounts[0])
