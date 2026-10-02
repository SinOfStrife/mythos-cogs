import random
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

class Odysseus(commands.Cog):
    """Responds to mentions of Odysseus with his family."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543210, force_registration=True
        )
        
        # Set default server settings
        default_guild = {
            "enabled": True,
            "target_channel": None,  # None means active everywhere
            "penelope_weight": 75,   # Default 75% Penelope
            "telemachus_weight": 25, # Default 25% Telemachus
        }
        self.config.register_guild(**default_guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # Check if the feature is enabled for this server
        guild = message.guild
        if not await self.config.guild(guild).enabled():
            return

        # Check if a specific channel is set
        target_channel_id = await self.config.guild(guild).target_channel()
        if target_channel_id and message.channel.id != target_channel_id:
            return

        # Check if "odysseus" is in the message (case-insensitive)
        if "odysseus" in message.content.lower():
            p_weight = await self.config.guild(guild).penelope_weight()
            t_weight = await self.config.guild(guild).telemachus_weight()

            # Choose which character to send
            chosen = random.choices(
                ["Penelope", "Telemachus"], 
                weights=[p_weight, t_weight], 
                k=1
            )[0]
            
            # Format Telemachus in all caps with an exclamation mark
            if chosen == "Telemachus":
                response = "TELEMACHUS!"
            else:
                response = "Penelope"
            
            await message.channel.send(response)

    # --- ADMIN CONFIGURATION COMMANDS ---

    @commands.group(name="odyssettings")
    @commands.admin_or_permissions(manage_guild=True)
    async def odyssettings(self, ctx: commands.Context):
        """Configure the Odysseus easter egg settings."""
        pass

    @odyssettings.command(name="toggle")
    async def odyssettings_toggle(self, ctx: commands.Context):
        """Turn the Odysseus response feature on or off."""
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        status = "enabled" if new_state else "disabled"
        await ctx.send(f"✅ Odysseus responses are now **{status}** for this server.")

    @odyssettings.command(name="channel")
    async def odyssettings_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Restrict responses to a specific channel (leave blank for all channels)."""
        if channel is None:
            await self.config.guild(ctx.guild).target_channel.set(None)
            await ctx.send("✅ Odysseus responses are now active in **all channels**.")
        else:
            await self.config.guild(ctx.guild).target_channel.set(channel.id)
            await ctx.send(f"✅ Odysseus responses are now restricted to {channel.mention}.")

    @odyssettings.command(name="split")
    async def odyssettings_split(self, ctx: commands.Context, penelope: int, telemachus: int):
        """Set custom percentage split for Penelope and Telemachus (e.g., [p]odyssettings split 80 20)."""
        if penelope < 0 or telemachus < 0 or (penelope + telemachus) == 0:
            return await ctx.send("❌ Please provide valid positive numbers for the split.")

        await self.config.guild(ctx.guild).penelope_weight.set(penelope)
        await self.config.guild(ctx.guild).telemachus_weight.set(telemachus)
        
        await ctx.send(f"✅ Split updated! Penelope: **{penelope}%**, Telemachus: **{telemachus}%**.")
      
