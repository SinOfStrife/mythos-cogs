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
            "allowed_channels": [],  # Empty list means active everywhere (unless blacklisted)
            "blacklisted_channels": [], # List of blocked channels
            "penelope_weight": 75,   # Default 75% Penelope
            "telemachus_weight": 25, # Default 25% Telemachus
        }
        self.config.register_guild(**default_guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild = message.guild
        # Check if the feature is enabled for this server
        if not await self.config.guild(guild).enabled():
            return

        channel_id = message.channel.id

        # 1. Check if channel is blacklisted
        blacklisted = await self.config.guild(guild).blacklisted_channels()
        if channel_id in blacklisted:
            return

        # 2. Check if a whitelist (allowed channels) is active
        allowed = await self.config.guild(guild).allowed_channels()
        if allowed and channel_id not in allowed:
            return

        # Check if "odysseus" is in the message (case-insensitive)
        if "odysseus" in message.content.lower():
            
            # --- RARE 1% GIF EASTER EGG ---
            if random.randint(1, 100) == 1:
                await message.channel.send("https://klipy.com/gifs/odysseus-odysseus-epic")
                return

            # --- STANDARD PENELOPE / TELEMACHUS ROLL ---
            p_weight = await self.config.guild(guild).penelope_weight()
            t_weight = await self.config.guild(guild).telemachus_weight()

            # Choose which character to send based on weights
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

    @commands.group(name="ody")
    @commands.admin_or_permissions(manage_guild=True)
    async def ody(self, ctx: commands.Context):
        """Configure the Odysseus easter egg settings."""
        pass

    @ody.command(name="toggle")
    async def ody_toggle(self, ctx: commands.Context):
        """Turn the Odysseus response feature on or off."""
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        status = "enabled" if new_state else "disabled"
        await ctx.send(f"✅ Odysseus responses are now **{status}** for this server.")

    @ody.command(name="view")
    async def ody_view(self, ctx: commands.Context):
        """View the current configuration settings for this server."""
        guild_conf = self.config.guild(ctx.guild)
        
        enabled = await guild_conf.enabled()
        p_weight = await guild_conf.penelope_weight()
        t_weight = await guild_conf.telemachus_weight()
        allowed_ids = await guild_conf.allowed_channels()
        blacklisted_ids = await guild_conf.blacklisted_channels()

        # Format channel lists into mentions or text
        allowed_str = ", *None (Active everywhere)*"
        if allowed_ids:
            allowed_str = ", ".join([f"<#{cid}>" for cid in allowed_ids])

        blacklisted_str = "*None*"
        if blacklisted_ids:
            blacklisted_str = ", ".join([f"<#{cid}>" for cid in blacklisted_ids])

        embed = discord.Embed(
            title="🏛️ Odysseus Cog Settings",
            color=discord.Color.gold()
        )
        embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Split Ratio", value=f"Penelope: {p_weight}% | TELEMACHUS!: {t_weight}%", inline=True)
        embed.add_field(name="Allowed Channels", value=allowed_str, inline=False)
        embed.add_field(name="Blacklisted Channels", value=blacklisted_str, inline=False)

        await ctx.send(embed=embed)

    @ody.command(name="split")
    async def ody_split(self, ctx: commands.Context, penelope: int, telemachus: int):
        """Set custom percentage split for Penelope and Telemachus (e.g., [p]ody split 80 20)."""
        if penelope < 0 or telemachus < 0 or (penelope + telemachus) == 0:
            return await ctx.send("❌ Please provide valid positive numbers for the split.")

        await self.config.guild(ctx.guild).penelope_weight.set(penelope)
        await self.config.guild(ctx.guild).telemachus_weight.set(telemachus)
        
        await ctx.send(f"✅ Split updated! Penelope: **{penelope}%**, TELEMACHUS!: **{telemachus}%**.")

    # --- CHANNEL MANAGEMENT SUBCOMMANDS ---

    @ody.group(name="channel")
    async def ody_channel(self, ctx: commands.Context):
        """Manage allowed or blacklisted channels."""
        pass

    @ody_channel.command(name="allow")
    async def channel_allow(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a channel to the allowed list (whitelisting)."""
        allowed = await self.config.guild(ctx.guild).allowed_channels()
        if channel.id in allowed:
            return await ctx.send(f"❌ {channel.mention} is already in the allowed channels list.")
        
        allowed.append(channel.id)
        await self.config.guild(ctx.guild).allowed_channels.set(allowed)
        await ctx.send(f"✅ Added {channel.mention} to the allowed channels.")

    @ody_channel.command(name="disallow")
    async def channel_disallow(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from the allowed list."""
        allowed = await self.config.guild(ctx.guild).allowed_channels()
        if channel.id not in allowed:
            return await ctx.send(f"❌ {channel.mention} is not in the allowed channels list.")
        
        allowed.remove(channel.id)
        await self.config.guild(ctx.guild).allowed_channels.set(allowed)
        await ctx.send(f"✅ Removed {channel.mention} from the allowed channels.")

    @ody_channel.command(name="blacklist")
    async def channel_blacklist(self, ctx: commands.Context, channel: discord.TextChannel):
        """Blacklist a channel so responses never happen there."""
        blacklisted = await self.config.guild(ctx.guild).blacklisted_channels()
        if channel.id in blacklisted:
            return await ctx.send(f"❌ {channel.mention} is already blacklisted.")
        
        blacklisted.append(channel.id)
        await self.config.guild(ctx.guild).blacklisted_channels.set(blacklisted)
        await ctx.send(f"✅ Blacklisted {channel.mention}. Odysseus will stay quiet there.")

    @ody_channel.command(name="unblacklist")
    async def channel_unblacklist(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from the blacklist."""
        blacklisted = await self.config.guild(ctx.guild).blacklisted_channels()
        if channel.id not in blacklisted:
            return await ctx.send(f"❌ {channel.mention} is not blacklisted.")
        
        blacklisted.remove(channel.id)
        await self.config.guild(ctx.guild).blacklisted_channels.set(blacklisted)
        await ctx.send(f"✅ Removed {channel.mention} from the blacklist.")
