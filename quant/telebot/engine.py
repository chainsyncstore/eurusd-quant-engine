
import asyncio
import logging
from quant.live.signal_generator import SignalGenerator

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 20
AUTH_RESET_THRESHOLD = 5


class AsyncEngine:
    def __init__(self, generator: SignalGenerator, on_signal=None):
        self.gen = generator
        self.on_signal = on_signal
        self.running = False
        self.task = None

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Async Engine started.")

    async def stop(self):
        if not self.running:
            return
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Async Engine stopped.")

    async def _loop(self):
        logger.info("Engine loop started. First signal in ~60s...")
        consecutive_errors = 0
        while self.running:
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, self.gen.run_once)
                consecutive_errors = 0  # reset on success

                if result and self.on_signal:
                    if asyncio.iscoroutinefunction(self.on_signal):
                        await self.on_signal(result)
                    else:
                        self.on_signal(result)
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    f"Engine loop error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}",
                    exc_info=True,
                )

                # After AUTH_RESET_THRESHOLD errors, force re-authentication
                if consecutive_errors == AUTH_RESET_THRESHOLD:
                    logger.warning("Resetting auth state after %d errors...", AUTH_RESET_THRESHOLD)
                    self.gen._authenticated = False

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error("Too many consecutive errors. Stopping engine loop.")
                    self.running = False
                    await self._notify_crash(consecutive_errors, e)
                    break

                # Exponential backoff: 60s, 120s, 240s... capped at 300s
                backoff = min(60 * (2 ** (consecutive_errors - 1)), 300)
                logger.info(f"Backing off {backoff}s before retry...")
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                continue  # skip normal sleep

            # Normal interval sleep
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
        logger.info("Engine loop exited.")

    async def _notify_crash(self, error_count: int, last_error: Exception):
        """Send crash notification through the on_signal callback."""
        if not self.on_signal:
            return
        crash_signal = {
            "signal": "ENGINE_CRASH",
            "reason": f"Engine stopped after {error_count} consecutive errors. Last: {last_error}",
            "close_price": 0,
            "probability": 0,
            "regime": -1,
            "position": {},
        }
        try:
            if asyncio.iscoroutinefunction(self.on_signal):
                await self.on_signal(crash_signal)
            else:
                self.on_signal(crash_signal)
        except Exception as e:
            logger.error(f"Failed to send crash notification: {e}")
