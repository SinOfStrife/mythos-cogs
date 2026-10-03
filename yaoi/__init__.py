from .yaoi import Yaoi

async def setup(bot):
    await bot.add_cog(Yaoi(bot))
  
