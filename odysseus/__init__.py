from .odysseus import Odysseus

async def setup(bot):
    await bot.add_cog(Odysseus(bot))
  
