# Copyright (c) 2026 SinOfStrife and Contributors
# Licensed under MIT

from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement
from .timestamp_slash import Epoch

__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot: Red) -> None:
    await bot.add_cog(Epoch(bot))
  
