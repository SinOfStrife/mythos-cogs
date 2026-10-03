import aiohttp
from redbot.core import commands

class Yaoi(commands.Cog):
    """A cog that delivers random yaoi imagery in age-restricted channels."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="yaoi", hidden=True)
    @commands.is_nsfw()
    async def yaoi(self, ctx: commands.Context):
        """Sends a random image from the API (NSFW channels only)."""
        api_url = "https://api.purrbot.site/v2/img/nsfw/yaoi/gif"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data.get("error"):
                        await ctx.send(data["link"])
                    else:
                        await ctx.send("❌ API returned an error.")
                else:
                    await ctx.send(f"❌ Failed to reach API (Status: {resp.status}).")

    @yaoi.error
    async def yaoi_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ This command can only be used in an age-restricted (NSFW) channel.")
                      
