import asyncio
import datetime
import re
from typing import Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class FButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)  # Active for 15 minutes

    @discord.ui.button(label="F in chat", style=discord.ButtonStyle.secondary, emoji="🇫")
    async def f_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.channel.send("F")
        await interaction.response.defer(ephemeral=True)


class Jail(commands.Cog):
    """A secure native timeout (jail) cog for Red-DiscordBot."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987654321123, force_registration=True)
        self.config.register_guild(trusted_staff=[])

    async def is_authorized_staff(self, member: discord.Member) -> bool:
        if await self.bot.is_owner(member):
            return True
        if member.id == member.guild.owner_id:
            return True
        trusted = await self.config.guild(member.guild).trusted_staff()
        return member.id in trusted

    async def cog_check(self, ctx: commands.Context) -> bool:
        if await self.is_authorized_staff(ctx.author):
            return True
        raise commands.UserFeedbackCheckFailure("❌ You do not have permission to use jail commands.")

    def parse_duration(self, duration_str: str) -> Optional[datetime.timedelta]:
        match = re.match(r"^(\d+)\s*([a-zA-Z]+)?$", duration_str.strip())
        if not match:
            return None
        amount_str, unit = match.groups()
        amount = int(amount_str)

        if not unit:
            unit = "s"  # Default to seconds if no unit is provided

        unit = unit.lower()
        if unit.startswith("s"):
            delta = datetime.timedelta(seconds=amount)
        elif unit.startswith("m"):
            delta = datetime.timedelta(minutes=amount)
        elif unit.startswith("h"):
            delta = datetime.timedelta(hours=amount)
        elif unit.startswith("d"):
            delta = datetime.timedelta(days=amount)
        else:
            return None

        # Discord max timeout limit is 28 days
        if delta > datetime.timedelta(days=28):
            return None

        return delta

    @commands.hybrid_command(name="jail")
    async def jail(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str,
    ):
        """Timeout a member natively with custom duration and reason."""
        # 1. Check Bot Permissions
        if not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send("❌ I do not have the **Moderate Members** permission.", ephemeral=True)
            return

        # 2. Check Target Immunity
        if member.id == ctx.guild.owner_id or await self.bot.is_owner(member):
            await ctx.send("❌ You cannot jail the server owner or bot owner.", ephemeral=True)
            return

        trusted = await self.config.guild(ctx.guild).trusted_staff()
        if member.id in trusted:
            await ctx.send("❌ You cannot jail a trusted staff member.", ephemeral=True)
            return

        # 3. Parse Duration
        delta = self.parse_duration(duration)
        if not delta:
            await ctx.send(
                "❌ Invalid duration format or exceeds the 28-day maximum limit. "
                "Examples: `20s`, `15m`, `2h`, `1d`, `20 seconds`, `1 minute`.",
                ephemeral=True,
            )
            return

        # 4. Apply Native Timeout
        unlock_time = discord.utils.utcnow() + delta
        try:
            await member.timeout(unlock_time, reason=reason)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to apply timeout: {e}", ephemeral=True)
            return

        # 5. Build Notice Embed
        embed = discord.Embed(
            title="🚨 JAILED !!!",
            description=(
                "────────────────────────────────────────\n"
                f"**User:** {member.mention} (`{member.id}`)\n"
                f"**Duration:** {duration}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.red(),
        )

        # 6. Send to DMs & Channel Simultaneously
        dm_success = True
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            dm_success = False

        channel_content = (
            f"{member.mention} *(User has DMs closed, notice sent here)*"
            if not dm_success
            else None
        )
        view = FButtonView()
        await ctx.send(content=channel_content, embed=embed, view=view)

    @commands.hybrid_command(name="unjail")
    async def unjail(self, ctx: commands.Context, member: discord.Member):
        """Remove a native timeout from a member early."""
        if not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send("❌ I do not have the **Moderate Members** permission.", ephemeral=True)
            return

        try:
            await member.timeout(None, reason=f"Unjailed by {ctx.author}")
            await ctx.send(f"✅ Successfully unjailed {member.mention}.", ephemeral=True)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to unjail member: {e}", ephemeral=True)

    @commands.hybrid_group(name="jailstaff")
    async def jailstaff(self, ctx: commands.Context):
        """Manage trusted staff for the jail system."""
        pass

    @jailstaff.command(name="add")
    async def jailstaff_add(self, ctx: commands.Context, member: discord.Member):
        """Add a trusted staff member (Server Owner or Bot Owner only)."""
        if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author):
            await ctx.send("❌ Only the server owner or bot owner can manage trusted jail staff.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).trusted_staff() as trusted:
            if member.id in trusted:
                await ctx.send(f"❌ {member.mention} is already trusted staff.", ephemeral=True)
                return
            trusted.append(member.id)

        await ctx.send(f"✅ Added {member.mention} to trusted jail staff.", ephemeral=True)

    @jailstaff.command(name="remove")
    async def jailstaff_remove(self, ctx: commands.Context, member: discord.Member):
        """Remove a trusted staff member (Server Owner or Bot Owner only)."""
        if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author):
            await ctx.send("❌ Only the server owner or bot owner can manage trusted jail staff.", ephemeral=True)
            return

        async with self.config.guild(ctx.guild).trusted_staff() as trusted:
            if member.id not in trusted:
                await ctx.send(f"❌ {member.mention} is not in trusted staff.", ephemeral=True)
                return
            trusted.remove(member.id)

        await ctx.send(f"✅ Removed {member.mention} from trusted jail staff.", ephemeral=True)

    @jailstaff.command(name="list")
    async def jailstaff_list(self, ctx: commands.Context):
        """List trusted jail staff members."""
        if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author):
            is_trusted = await self.is_authorized_staff(ctx.author)
            if not is_trusted:
                await ctx.send("❌ You do not have permission to view the trusted staff list.", ephemeral=True)
                return

        trusted = await self.config.guild(ctx.guild).trusted_staff()
        if not trusted:
            await ctx.send("ℹ️ There are no trusted jail staff configured for this server.", ephemeral=True)
            return

        mentions = [f"<@{uid}>" for uid in trusted]
        await ctx.send(f"📋 **Trusted Jail Staff:**\n" + ", ".join(mentions), ephemeral=True)
