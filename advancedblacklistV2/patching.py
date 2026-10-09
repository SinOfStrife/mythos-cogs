# Copyright (c) 2021-2026 Jojo#7791, SinOfStrife and Contributors
# Licensed under MIT

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Coroutine, Dict, Final, List

from discord.utils import maybe_coroutine
from redbot.core.bot import Red

__all__ = ("Patch",)

_log = logging.getLogger("red.strifecogs.advancedblacklistv2.patch")
Coro = Callable[..., Coroutine[Any, Any, Any]]
_lock = asyncio.Lock()
_names: Final[List[str]] = [
    "add_to_blacklist",
    "remove_from_blacklist",
    "clear_blacklist",
    "add_to_whitelist",
    "remove_from_whitelist",
    "clear_whitelist",
]


def _with_lock(func: Callable[[Any], Any]) -> Coro:
    @wraps(func)
    async def inner(*args, **kwargs) -> Any:
        async with _lock:
            return await maybe_coroutine(func, *args, **kwargs)

    return inner


class Patch:
    def __init__(self, bot: Red):
        self.bot = bot
        self._funcs: Dict[str, Coro] = {}
        self._initialized = False

    def _patch_wrapper(self, method_name: str, func: Coro) -> Coro:
        @wraps(func)
        async def inner(*args, **kwargs):
            adv_bl = kwargs.pop("adv_bl", False)
            await func(*args, **kwargs)
            # discord.py client.dispatch automatically prepends "on_"
            self.bot.dispatch(method_name, *args, **kwargs, adv_bl=adv_bl)

        return inner

    @_with_lock
    async def startup(self) -> None:
        if self._initialized:
            return
        for name in _names:
            func = getattr(self.bot, name, None)
            if not func:
                continue

            self._funcs[name] = func
            setattr(self.bot, name, self._patch_wrapper(name, func))
        self._initialized = True

    @_with_lock
    async def destroy(self) -> None:
        if not self._initialized:
            return
        self._initialized = False
        for name, func in self._funcs.items():
            setattr(self.bot, name, func)
        self._funcs.clear()