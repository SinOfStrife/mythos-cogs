# Copyright (c) 2026 SinOfStrife and Contributors
# Licensed under MIT

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Final, List, Literal, Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red

log = logging.getLogger("red.strifecogs.servermanager")

FEATURE_MAPPING: Final[dict[str, str]] = {
    "ANIMATED_ICON": "Animated Icon",
    "BANNER": "Server Banner",
    "COMMUNITY": "Community Server",
    "DISCOVERABLE": "Server Discovery",
    "FEATURABLE": "Featurable",
    "INVITE_SPLASH": "Invite Splash Background",
    "MEMBER_VERIFICATION_GATE_ENABLED": "Membership Screening",
    "MORE_EMOJIS": "More Emojis",
    "MORE_STICKERS": "More Stickers",
    "NEWS": "Announcement Channels",
    "PARTNERED": "Partnered Server",
    "PREVIEW_ENABLED": "Preview Enabled",
    "VANITY_URL": "Custom Invite URL",
    "VERIFIED": "Verified Server",
    "WELCOME_SCREEN_ENABLED": "Welcome Screen",
    "AUTO_MODERATION": "Auto Moderation",
    "ROLE_ICONS": "Role Icons",
    "SOUNDBOARD": "Soundboard",
}

VERIFICATION_LEVELS: Final[dict[discord.VerificationLevel, str]] = {
    discord.VerificationLevel.none: "None",
    discord.VerificationLevel.low: "Low",
    discord.VerificationLevel.medium: "Medium",
    discord.VerificationLevel.high: "High",
    discord.VerificationLevel.highest: "Highest",
}


class LeaveConfirmationView(discord.ui.View):
    def __init__(self, guild: discord.Guild, owner_id: int):
        super().__init__(timeout=60.0)
        self.guild = guild
        self.owner_id = owner_id
        self.message: Optional[discord.Message | discord.WebhookMessage] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("You are not authorized to use this control.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.stop()
        if self.message:
            with suppress(discord.HTTPException):
                await self.message.delete()

    @discord.ui.button(label="Confirm Leave", style=discord.ButtonStyle.danger, emoji="⚠️", row=0)
    async def confirm_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            guild_name = self.guild.name
            guild_id = self.guild.id
            await self.guild.leave()
            self.stop()
            await interaction.response.edit_message(
                content=f"✅ Successfully left **{guild_name}** (`{guild_id}`).",
                view=None,
            )
        except Exception as e:
            log.exception("Failed to leave guild %s (%s)", self.guild.name, self.guild.id)
            await interaction.response.edit_message(
                content=f"❌ Failed to leave the server. Error: `{e}`",
                view=None,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️", row=0)
    async def cancel_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="❌ Server leave action cancelled.", view=None)


class ServerManagerView(discord.ui.View):
    def __init__(self, bot: Red, guilds: List[discord.Guild], author_id: int):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.guilds = guilds
        self.author_id = author_id
        self.current_page = 0
        self.max_page = len(guilds)
        self.message: Optional[discord.Message] = None
        self.update_button_states()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This interactive menu does not belong to you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.stop()
        if self.message:
            with suppress(discord.HTTPException):
                await self.message.edit(view=None)

    def update_button_states(self) -> None:
        self.first_page.disabled = self.current_page == 0
        self.prev_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page >= self.max_page
        self.last_page.disabled = self.current_page >= self.max_page
        self.invite_button.disabled = self.current_page == 0
        self.leave_button.disabled = self.current_page == 0

    def build_summary_embed(self) -> discord.Embed:
        total_servers = len(self.guilds)
        total_members = sum(g.member_count or 0 for g in self.guilds)

        oldest_guild = min(
            self.guilds,
            key=lambda g: g.me.joined_at if (g.me and g.me.joined_at) else g.created_at
        ) if self.guilds else None
        
        largest_guild = max(self.guilds, key=lambda g: g.member_count or 0) if self.guilds else None

        embed = discord.Embed(
            title="🌐 Global Bot Footprint Summary",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Total Servers", value=f"`{total_servers:,}`", inline=True)
        embed.add_field(name="Total Member Reach", value=f"`{total_members:,}`", inline=True)
        
        if oldest_guild and oldest_guild.me and oldest_guild.me.joined_at:
            join_timestamp = int(oldest_guild.me.joined_at.timestamp())
            embed.add_field(
                name="Oldest Bot Join",
                value=f"**{oldest_guild.name}**\n(<t:{join_timestamp}:R>)",
                inline=False,
            )
        elif oldest_guild:
            embed.add_field(
                name="Oldest Bot Join",
                value=f"**{oldest_guild.name}**\n(<t:{int(oldest_guild.created_at.timestamp())}:R>)",
                inline=False,
            )

        if largest_guild:
            embed.add_field(
                name="Largest Server",
                value=f"**{largest_guild.name}**\n(`{(largest_guild.member_count or 0):,}` members)",
                inline=False,
            )

        embed.set_footer(text=f"Page 0 / {self.max_page} • Bot Owner Dashboard")
        return embed

    def build_guild_embed(self, guild: discord.Guild) -> discord.Embed:
        created_timestamp = int(guild.created_at.timestamp())
        age_relative = f"<t:{created_timestamp}:R>"

        total_members = guild.member_count or len(guild.members)
        if guild.chunked or len(guild.members) >= total_members:
            human_count = sum(1 for m in guild.members if not m.bot)
            bot_count = total_members - human_count
            member_value = (
                f"**Total:** `{total_members:,}`\n"
                f"**Humans:** `{human_count:,}`\n"
                f"**Bots:** `{bot_count:,}`"
            )
        else:
            member_value = (
                f"**Total:** `{total_members:,}`\n"
                f"*(Breakdown requires member chunking)*"
            )

        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        stage_channels = len(guild.stage_channels)
        forum_channels = len(getattr(guild, "forum_channels", []))
        thread_count = len(guild.threads) if hasattr(guild, "threads") else 0
        role_count = len(guild.roles)

        static_emojis = sum(1 for e in guild.emojis if not e.animated)
        animated_emojis = sum(1 for e in guild.emojis if e.animated)
        sticker_count = len(guild.stickers)
        soundboard_count = len(getattr(guild, "soundboard_sounds", []))
        event_count = len(guild.scheduled_events)

        afk_info = f"{guild.afk_channel.mention} (`{guild.afk_timeout // 60}m`)" if guild.afk_channel else "Not set"
        locale = str(guild.preferred_locale)
        mfa = "Required" if guild.mfa_level == discord.MFALevel.require else "Optional"

        formatted_features = [
            FEATURE_MAPPING.get(f, f.replace("_", " ").title())
            for f in guild.features
        ]
        features_str = ", ".join(formatted_features[:15]) if formatted_features else "None"
        if len(formatted_features) > 15:
            features_str += f" *...and {len(formatted_features) - 15} more*"

        owner = guild.owner or self.bot.get_user(guild.owner_id)
        owner_str = f"{owner.mention} (`{owner}`)" if owner else f"<@{guild.owner_id}> (`ID: {guild.owner_id}`)"
        verification = VERIFICATION_LEVELS.get(guild.verification_level, "Unknown")

        embed = discord.Embed(
            title=guild.name,
            description=guild.description or "*No server description provided.*",
            color=discord.Color.dark_embed(),
            timestamp=datetime.now(timezone.utc),
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.add_field(
            name="📌 Utility Info",
            value=f"**ID:** `{guild.id}`\n"
                  f"**Owner:** {owner_str}\n"
                  f"**Verification:** `{verification}`\n"
                  f"**Staff 2FA:** `{mfa}`\n"
                  f"**Locale:** `{locale}`\n"
                  f"**Created:** {age_relative}",
            inline=True,
        )
        embed.add_field(
            name="👥 Members & Roles",
            value=f"{member_value}\n**Roles:** `{role_count:,}`",
            inline=True,
        )
        embed.add_field(
            name="🚀 Nitro Boost",
            value=f"**Tier:** `{guild.premium_tier}`\n"
                  f"**Boosts:** `{guild.premium_subscription_count}`",
            inline=True,
        )
        embed.add_field(
            name="📁 Channels & Audio",
            value=f"**Text:** `{text_channels}` | **Voice:** `{voice_channels}`\n"
                  f"**Stages:** `{stage_channels}` | **Forums:** `{forum_channels}`\n"
                  f"**Active Threads:** `{thread_count}`\n"
                  f"**AFK Voice:** {afk_info}",
            inline=False,
        )
        embed.add_field(
            name="🎨 Expressions & Events",
            value=f"**Emojis:** `{static_emojis} static, {animated_emojis} animated`\n"
                  f"**Stickers:** `{sticker_count}` | **Soundboard:** `{soundboard_count}`\n"
                  f"**Scheduled Events:** `{event_count}`",
            inline=False,
        )
        embed.add_field(
            name="✨ Features",
            value=features_str,
            inline=False,
        )

        embed.set_footer(text=f"Page {self.current_page} / {self.max_page} • Server ID: {guild.id}")
        return embed

    async def get_current_embed(self) -> discord.Embed:
        if self.current_page == 0:
            return self.build_summary_embed()
        return self.build_guild_embed(self.guilds[self.current_page - 1])

    async def _safe_edit(self, interaction: discord.Interaction) -> None:
        try:
            embed = await self.get_current_embed()
            self.update_button_states()
            if interaction.response.is_done():
                await interaction.edit_message(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            log.exception("Error updating ServerManagerView page")
            with suppress(discord.HTTPException):
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: `{e}`", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: `{e}`", ephemeral=True)

    @discord.ui.button(emoji="⏪", style=discord.ButtonStyle.primary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = 0
        await self._safe_edit(interaction)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page > 0:
            self.current_page -= 1
        await self._safe_edit(interaction)

    @discord.ui.button(emoji="✖️", style=discord.ButtonStyle.danger, row=0)
    async def close_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await interaction.response.defer()
            self.stop()
            if self.message:
                with suppress(discord.HTTPException):
                    await self.message.delete()
        except Exception:
            log.exception("Error closing menu")

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page < self.max_page:
            self.current_page += 1
        await self._safe_edit(interaction)

    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.primary, row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = self.max_page
        await self._safe_edit(interaction)

    @discord.ui.button(label="Invite", emoji="🔗", style=discord.ButtonStyle.success, row=1)
    async def invite_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page == 0:
            await interaction.response.send_message("Cannot generate an invite for the global summary page.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=True)
            guild = self.guilds[self.current_page - 1]
            invite_url = None

            if guild.vanity_url:
                invite_url = guild.vanity_url
            else:
                try:
                    invites = await guild.invites()
                    if invites:
                        invite_url = invites[0].url
                except (discord.Forbidden, discord.HTTPException):
                    pass

            if not invite_url:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).create_instant_invite:
                        try:
                            invite = await channel.create_invite(max_age=300, max_uses=1, reason="Owner requested server invite lookup.")
                            invite_url = invite.url
                            break
                        except (discord.Forbidden, discord.HTTPException):
                            continue

            if invite_url:
                await interaction.followup.send(f"🔗 Invite link for **{guild.name}**: {invite_url}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Unable to retrieve an invite link for **{guild.name}** (Missing permissions).", ephemeral=True)
        except Exception as e:
            log.exception("Error in invite button")
            with suppress(discord.HTTPException):
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: `{e}`", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: `{e}`", ephemeral=True)

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.danger, row=1)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page == 0:
            return

        try:
            guild = self.guilds[self.current_page - 1]
            confirm_view = LeaveConfirmationView(guild, self.author_id)
            await interaction.response.send_message(
                f"⚠️ **Are you sure you want the bot to leave `{guild.name}` (`{guild.id}`)?**\nThis action cannot be undone.",
                view=confirm_view,
                ephemeral=True,
            )
            confirm_view.message = await interaction.original_response()
        except Exception as e:
            log.exception("Error in leave button")
            with suppress(discord.HTTPException):
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: `{e}`", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: `{e}`", ephemeral=True)


class ServerManager(commands.Cog):
    """Owner-only interactive server management suite."""

    __version__ = "1.0.5"
    __author__ = ["SinOfStrife"]
    __red_end_user_data_statement__ = "This cog does not persistently store any personal or user data."

    def __init__(self, bot: Red):
        self.bot = bot

    async def red_get_data_for_user(self, *, user_id: int) -> dict:
        return {}

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        return

    @commands.is_owner()
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    @commands.command(name="slist", aliases=["sl", "s"])
    async def server_list(self, ctx: commands.Context) -> None:
        """Browse an interactive paginated dashboard of all servers the bot is in."""
        guilds = list(self.bot.guilds)
        if not guilds:
            await ctx.send("❌ The bot is currently not in any servers.")
            return

        view = ServerManagerView(self.bot, guilds, ctx.author.id)
        embed = await view.get_current_embed()
        view.message = await ctx.send(embed=embed, view=view)

    @commands.is_owner()
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    @commands.command(name="sinfo", aliases=["si", "lookup"])
    async def server_lookup(self, ctx: commands.Context, guild_id: int) -> None:
        """Inspect a single server embed directly by its ID."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await ctx.send(f"❌ I am not in a server with ID `{guild_id}`, or that ID is invalid.")
            return

        view = ServerManagerView(self.bot, [guild], ctx.author.id)
        view.current_page = 1
        view.max_page = 1
        view.update_button_states()

        embed = view.build_guild_embed(guild)
        view.message = await ctx.send(embed=embed, view=view)
