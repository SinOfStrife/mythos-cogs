from redbot.core.bot import Red
from .jail import Jail

async def setup(bot: Red):
    await bot.add_cog(Jail(bot))
  
