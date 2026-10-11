# Copyright (c) 2020-2026 PhenoM4n4n
# Copyright (c) 2023-2026 AAA3A
# Copyright (c) 2026 SinOfStrife and Contributors
# Licensed under MIT

from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement
from .linkquoter import LinkQuoter

__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot: Red) -> None:
    await bot.add_cog(LinkQuoter(bot))
