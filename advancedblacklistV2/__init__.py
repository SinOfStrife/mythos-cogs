# Copyright (c) 2021-2026 Jojo#7791, SinOfStrife and Contributors
# Licensed under MIT

from redbot.core.bot import Red
from .core import AdvancedBlacklistV2


async def setup(bot: Red) -> None:
    cog = AdvancedBlacklistV2(bot)
    await bot.add_cog(cog)