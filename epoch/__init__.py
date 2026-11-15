from .timestamp_slash import Epoch

async def setup(bot):
    await bot.add_cog(Epoch(bot))
  
