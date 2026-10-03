import aiohttp
from redbot.core import commands

class Yaoi(commands.Cog):
    """A cog that delivers random yaoi imagery in age-restricted channels."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="yaoi")
    @commands.is_nsfw()
    async def yaoi(self, ctx: commands.Context):
        """Sends a random image from the API (NSFW channels only)."""
        api_url = "https://api.purrbot.site/v2/img/nsfw/yaoi/gif"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Purrbot returns the URL in "link" key within "data" object
                        if data.get("data") and data["data"].get("link"):
                            await ctx.send(data["data"]["link"])
                        else:
                            await ctx.send("❌ Invalid API response format.")
                    else:
                        await ctx.send(f"❌ Failed to reach API (Status: {resp.status}).")
        except asyncio.TimeoutError:
            await ctx.send("❌ API request timed out.")
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")

    @yaoi.error
    async def yaoi_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ This command can only be used in an age-restricted (NSFW) channel.")