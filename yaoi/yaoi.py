import ssl
from io import BytesIO

import aiohttp
import discord
from redbot.core import commands


class Yaoi(commands.Cog):
    """A cog that delivers random yaoi imagery in age-restricted channels."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="yaoi", hidden=True)
    @commands.is_nsfw()
    async def yaoi(self, ctx: commands.Context):
        """Sends a random GIF from the API (NSFW channels only)."""
        api_url = "https://api.purrbot.site/v2/img/nsfw/yaoi/gif"

        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)

            async with aiohttp.ClientSession(connector=connector) as session:
                # Get the API response
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

                if data.get("error") is not False:
                    return await ctx.send("❌ API returned an error.")

                # Download the image using the SAME session
                async with session.get(data["link"], timeout=aiohttp.ClientTimeout(total=15)) as img_resp:
                    image_bytes = await img_resp.read()

            spoiler_name = "SPOILER_yaoi.gif"
            image_file = discord.File(BytesIO(image_bytes), filename=spoiler_name)

            embed = discord.Embed()
            embed.set_image(url=f"attachment://{spoiler_name}")

            await ctx.send(embed=embed, file=image_file)

        except Exception as e:
            await ctx.send(f"❌ API request failed: {e}")

    @yaoi.error
    async def yaoi_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ This command can only be used in an age-restricted (NSFW) channel.")