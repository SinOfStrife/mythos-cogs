# Copyright (c) 2020-2026 PhenoM4n4n
# Copyright (c) 2023-2026 AAA3A
# Copyright (c) 2026 SinOfStrife and Contributors
# Licensed under MIT

from redbot.core.bot import Red
from .linkquoter import LinkQuoter


async def setup(bot: Red) -> None:
    cog = LinkQuoter(bot)
    await bot.add_cog(cog)