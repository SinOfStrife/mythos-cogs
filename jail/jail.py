import asyncio
import datetime
import re
from typing import Optional, Dict, Set

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class Jail(commands.Cog):
    """A secure native timeout (jail) cog using Discord Components V2."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987654321123, force_registration=True)
        self.config.register_guild(trusted_staff=[])
        # Tracks clicked users per message ID: {message_id: set(user_ids)}
        self.f_clicks: Dict[int, Set[int]] = {}

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
            unit = "s"

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

        if delta > datetime.timedelta(days=28):
            return None

        return delta

    @commands.Cog.listener("on_interaction")
    async def on_jail_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("jail_f_btn_"):
            return

        message_id = interaction.message.id
        user_id = interaction.user.id

        if message_id not in self.f_clicks:
            self.f_clicks[message_id] = set()

        if user_id in self.f_clicks[message_id]:
            await interaction.response.send_message("❌ You have already paid your respects!", ephemeral=True)
            return

        self.f_clicks[message_id].add(user_id)
        await interaction.response.send_message("✅ You paid your respects.", ephemeral=True)
        await interaction.channel.send(f"F in chat from {interaction.user.display_name}")

    @commands.hybrid_command(name="jail")
    async def jail(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str,
    ):
        """Timeout a member natively with Components V2 layout and custom duration."""
        if not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send("❌ I do not have the **Moderate Members** permission.", ephemeral=True)
            return

        if member.id == ctx.guild.owner_id:
            await ctx.send("❌ You cannot jail the server owner.", ephemeral=True)
            return

        trusted = await self.config.guild(ctx.guild).trusted_staff()
        if member.id in trusted:
            await ctx.send("❌ You cannot jail a trusted staff member.", ephemeral=True)
            return

        delta = self.parse_duration(duration)
        if not delta:
            await ctx.send(
                "❌ Invalid duration format or exceeds the 28-day maximum limit. "
                "Examples: `20s`, `15m`, `2h`, `1d`.",
                ephemeral=True,
            )
            return

        unlock_time = discord.utils.utcnow() + delta
        try:
            await member.timeout(unlock_time, reason=reason)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to apply timeout: {e}", ephemeral=True)
            return

        # Components V2 Payload Structure
        payload = {
            "flags": 32768,  # IS_COMPONENTS_V2 flag
            "components": [
                {
                    "type": 17,  # Container Component
                    "accent_color": 16711680,  # Red accent color
                    "components": [
                        {
                            "type": 10,  # TextDisplay Component
                            "content": (
                                "### 🚨 JAILED !!!\n"
                                "────────────────────────────────────────\n"
                                f"**User:** {member.mention} (`{member.id}`)\n"
                                f"**Duration:** {duration}\n"
                                f"**Reason:** {reason}"
                            )
                        },
                        {
                            "type": 1,  # ActionRow Component
                            "components": [
                                {
                                    "type": 2,  # Button Component
                                    "style": 2,  # Secondary style
                                    "label": "F in chat",
                                    "emoji": {"name": "🇫"},
                                    "custom_id": f"jail_f_btn_{member.id}_{discord.utils.utcnow().timestamp()}"
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        # Send DM notice using V2 payload
        dm_success = True
        dm_payload = {
            "flags": 32768,
            "components": [
                {
                    "type": 17,
                    "accent_color": 16711680,
                    "components": [
                        {
                            "type": 10,
                            "content": (
                                "### 🚨 YOU HAVE BEEN JAILED!\n"
                                "────────────────────────────────────────\n"
                                f"**Server:** {ctx.guild.name}\n"
                                f"**Duration:** {duration}\n"
                                f"**Reason:** {reason}"
                            )
                        }
                    ]
                }
            ]
        }
        try:
            await member.send(**dm_payload)
        except discord.Forbidden:
            dm_success = False

        channel_content = (
            f"{member.mention} *(User has DMs closed, notice sent here)*"
            if not dm_success
            else None
        )

        # Send the V2 container payload to the channel
        await ctx.send(content=channel_content, **payload)

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
