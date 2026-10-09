# Copyright (c) 2021-2026 Jojo#7791, SinOfStrife and Contributors
# Licensed under MIT

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Dict, Final, List, Literal, Optional, Tuple, Union

import discord
from discord.http import Route
from redbot.core import Config, commands
from redbot.core.bot import Red

from .patching import Patch
from .utils import (
    Cache,
    ConfirmView,
    FormatView,
    UserOrRole,
    UsersOrRoles,
    _WhiteBlacklist,
    config_structure,
)

log = logging.getLogger("red.strifecogs.advancedblacklistv2")

_CORE_NAMES: Final[Tuple[str, ...]] = (
    "blacklist", "blocklist", "whitelist", "allowlist",
    "localblacklist", "localblocklist", "localwhitelist", "localallowlist"
)


def _format_str(string: str, replace: Dict[str, str]) -> str:
    for key, value in replace.items():
        string = string.replace(key, value)
    return string


class V2ListMenu(discord.ui.View):
    """Hybrid menu using discord.py View buttons to paginate raw V2 Container Cards."""

    def __init__(self, cog: AdvancedBlacklistV2, ctx: commands.Context, pages: List[dict]):
        super().__init__(timeout=120.0)
        self.cog = cog
        self.ctx = ctx
        self.pages = pages
        self.current_page = 0
        self.v2_msg_id: Optional[int] = None
        self.btn_msg: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.clear_items()
        prev_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="◀", disabled=len(self.pages) <= 1)
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        stop_btn = discord.ui.Button(style=discord.ButtonStyle.danger, label="✕")
        stop_btn.callback = self._on_stop
        self.add_item(stop_btn)

        next_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="▶", disabled=len(self.pages) <= 1)
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.current_page = (self.current_page - 1) % len(self.pages)
        await self._update_v2_card()

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.current_page = (self.current_page + 1) % len(self.pages)
        await self._update_v2_card()

    async def _on_stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        if self.v2_msg_id:
            route = Route("DELETE", "/channels/{channel_id}/messages/{message_id}", channel_id=self.ctx.channel.id, message_id=self.v2_msg_id)
            with contextlib.suppress(discord.HTTPException):
                await self.cog.bot.http.request(route)
        if self.btn_msg:
            with contextlib.suppress(discord.HTTPException):
                await self.btn_msg.delete()

    async def _update_v2_card(self) -> None:
        if not self.v2_msg_id:
            return
        route = Route("PATCH", "/channels/{channel_id}/messages/{message_id}", channel_id=self.ctx.channel.id, message_id=self.v2_msg_id)
        payload = self.pages[self.current_page]
        with contextlib.suppress(discord.HTTPException):
            await self.cog.bot.http.request(route, json=payload)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You are not authorized to control this menu.", ephemeral=True)
            return False
        return True


class AdvancedBlacklistV2(commands.Cog):
    """An advanced extension of core blocklisting and allowlisting commands."""

    __author__: Final[List[str]] = ["Jojo#7791", "SinOfStrife"]
    __version__: Final[str] = "4.1.1"
    __red_end_user_data_statement__: Final[str] = (
        "This cog stores Discord user IDs, role IDs, and guild IDs strictly for moderation, "
        "blocklisting, and allowlisting purposes."
    )

    def __init__(self, bot: Red):
        self.bot = bot
        self._patch = Patch(self.bot)
        self.config = Config.get_conf(self, 544974305445019651, True)
        for config_type, data in config_structure.items():
            getattr(self.config, f"register_{config_type}")(**data)

        self._original_coms: List[commands.Command] = []
        for name in _CORE_NAMES:
            cmd = self.bot.remove_command(name)
            if cmd and cmd not in self._original_coms:
                self._original_coms.append(cmd)

        self._cache = Cache()

    async def cog_load(self) -> None:
        await self._patch.startup()
        await self._sync_preexisting_data()

    async def cog_unload(self) -> None:
        await self._patch.destroy()
        for com in self._original_coms:
            self.bot.remove_command(com.name)
            self.bot.add_command(com)
        log.info("AdvancedBlacklistV2 unloaded: Restored native Red core blacklist commands.")

    async def _sync_preexisting_data(self) -> None:
        try:
            core_bl = await self.bot.get_blacklist()
            async with self.config.blacklist() as bl:
                for uid in core_bl:
                    uid_str = str(uid)
                    if uid_str not in bl:
                        bl[uid_str] = "Pre-existing entry / No reason provided."
                        self._cache.update_blacklist(None, {uid_str: bl[uid_str]})

            core_wl = await self.bot.get_whitelist()
            async with self.config.whitelist() as wl:
                for uid in core_wl:
                    uid_str = str(uid)
                    if uid_str not in wl:
                        wl[uid_str] = "Pre-existing entry / No reason provided."
                        self._cache.update_whitelist(None, {uid_str: wl[uid_str]})
        except Exception as e:
            log.warning("Could not sync pre-existing blacklist data: %s", e)

    # GDPR Data Handlers
    async def red_get_data_for_user(self, *, user_id: int) -> dict:
        actual = str(user_id)
        bl = await self.config.blacklist()
        wl = await self.config.whitelist()
        guilds = await self.config.all_guilds()
        local_bl = [str(gid) for gid, data in guilds.items() if actual in data.get("blacklist", {})]
        local_wl = [str(gid) for gid, data in guilds.items() if actual in data.get("whitelist", {})]
        return {
            "globally_blocklisted": actual in bl,
            "globally_allowlisted": actual in wl,
            "locally_blocklisted_guilds": local_bl,
            "locally_allowlisted_guilds": local_wl,
        }

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        if requester not in ("discord_deleted_user", "owner"):
            return

        actual = str(user_id)
        async with self.config.blacklist() as bl:
            bl.pop(actual, None)
        async with self.config.whitelist() as wl:
            wl.pop(actual, None)

        self._cache.clear_blacklist(None)
        self._cache.clear_whitelist(None)

        guilds = await self.config.all_guilds()
        for guild_id in guilds:
            async with self.config.guild_from_id(guild_id).blacklist() as gbl:
                gbl.pop(actual, None)
            async with self.config.guild_from_id(guild_id).whitelist() as gwl:
                gwl.pop(actual, None)
            g_obj = self.bot.get_guild(guild_id)
            if g_obj:
                self._cache.clear_blacklist(g_obj)
                self._cache.clear_whitelist(g_obj)

    # REST Helpers with Auto-Delete Support
    async def _send_raw_v2_payload(
        self, channel_id: int, payload: dict, delete_after: Optional[float] = None
    ) -> dict:
        route = Route("POST", "/channels/{channel_id}/messages", channel_id=channel_id)
        data = await self.bot.http.request(route, json=payload)
        if delete_after and "id" in data:
            msg_id = int(data["id"])
            asyncio.create_task(self._delayed_delete(channel_id, msg_id, delete_after))
        return data

    async def _delayed_delete(self, channel_id: int, message_id: int, delay: float) -> None:
        await asyncio.sleep(delay)
        route = Route("DELETE", "/channels/{channel_id}/messages/{message_id}", channel_id=channel_id, message_id=message_id)
        with contextlib.suppress(discord.HTTPException):
            await self.bot.http.request(route)

    async def _send_action_mini_card(
        self, ctx: commands.Context, title: str, description: str, color_int: int, auto_delete: bool = True
    ) -> None:
        payload = {
            "flags": 32768,
            "components": [
                {
                    "type": 17,
                    "accent_color": color_int,
                    "components": [
                        {"type": 10, "content": f"### {title}"},
                        {"type": 14, "spacing": 1, "divider": True},
                        {"type": 10, "content": description}
                    ]
                }
            ]
        }
        with contextlib.suppress(discord.HTTPException):
            await self._send_raw_v2_payload(ctx.channel.id, payload, delete_after=10.0 if auto_delete else None)

    async def _log_action(
        self,
        action: str,
        target: UserOrRole,
        white_black_list: _WhiteBlacklist,
        reason: str,
        author: discord.User,
        guild: Optional[discord.Guild] = None,
    ) -> None:
        channel_id = (
            await self.config.guild(guild).log_channel()
            if guild
            else await self.config.log_channel()
        )
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel or not channel.permissions_for(channel.guild.me).send_messages:
            return

        target_name = getattr(target, "name", str(target))
        target_id = getattr(target, "id", target)
        list_type = "Allowlist" if white_black_list == "whitelist" else "Blocklist"
        scope = f"Local ({guild.name})" if guild else "Global"
        color_int = 0xED4245 if "Removed" not in action else 0x57F287

        payload = {
            "flags": 32768,
            "components": [
                {
                    "type": 17,
                    "accent_color": color_int,
                    "components": [
                        {"type": 10, "content": f"### 🛡️ {scope} {list_type} Update"},
                        {"type": 14, "spacing": 1, "divider": True},
                        {"type": 10, "content": f"**Action:** {action}\n**Target:** {target_name} (`{target_id}`)\n**Moderator:** {author.mention} (`{author.id}`)\n**Reason:** `{reason}`"}
                    ]
                }
            ]
        }
        with contextlib.suppress(discord.HTTPException):
            await self._send_raw_v2_payload(channel.id, payload)

    def _build_v2_check_payload(
        self,
        target: UserOrRole,
        white_black_list: _WhiteBlacklist,
        guild: Optional[discord.Guild],
        is_listed: bool,
        reason: str,
    ) -> dict:
        target_name = getattr(target, "name", str(target))
        target_id = getattr(target, "id", target)
        list_name = "Allowlist" if white_black_list == "whitelist" else "Blocklist"
        scope = f"Local ({guild.name})" if guild else "Global"
        color_int = 0xED4245 if is_listed else 0x57F287
        status_text = "Listed 🔴" if is_listed else "Not Listed 🟢"

        container_components = [
            {"type": 10, "content": f"### 🔍 {scope} {list_name} Check"},
            {"type": 14, "spacing": 1, "divider": True},
            {"type": 10, "content": f"**Target:** {target_name} (`{target_id}`)\n**Status:** {status_text}"}
        ]
        if is_listed:
            container_components.append({"type": 10, "content": f"**Reason:** `{reason}`"})

        return {
            "flags": 32768,
            "components": [
                {
                    "type": 17,
                    "accent_color": color_int,
                    "components": container_components
                }
            ]
        }

    # Core Logic
    async def _filter_self_harm(
        self, ctx: commands.Context, targets: UsersOrRoles, white_black_list: _WhiteBlacklist, guild: Optional[discord.Guild]
    ) -> Tuple[bool, List[UserOrRole]]:
        clean: List[UserOrRole] = []
        for target in targets:
            tid = getattr(target, "id", target)
            if tid == self.bot.user.id:
                await ctx.send("❌ You cannot add the bot to a blocklist or allowlist.")
                return False, []
            if white_black_list == "blacklist":
                if tid == ctx.author.id:
                    await ctx.send("❌ You cannot add yourself to a blocklist.")
                    return False, []
                if guild and tid == guild.owner_id and not await self.bot.is_owner(ctx.author):
                    await ctx.send("❌ You cannot add the server owner to the local blocklist.")
                    return False, []
            clean.append(target)
        return True, clean

    async def add_to_list(
        self,
        users_or_roles: UsersOrRoles,
        *,
        white_black_list: _WhiteBlacklist,
        reason: str,
        guild: Optional[discord.Guild] = None,
        override: bool = False,
    ) -> None:
        config = getattr((self.config.guild(guild) if guild else self.config), white_black_list)
        async with config() as target_list:
            for item in users_or_roles:
                actual = str(getattr(item, "id", item))
                target_list[actual] = reason
                if white_black_list == "whitelist":
                    self._cache.update_whitelist(guild, target_list)
                else:
                    self._cache.update_blacklist(guild, target_list)

        if not override:
            coro = getattr(self.bot, f"add_to_{white_black_list}")
            await coro(users_or_roles, guild=guild, adv_bl=True)

    async def remove_from_list(
        self,
        users_or_roles: UsersOrRoles,
        *,
        white_black_list: _WhiteBlacklist,
        guild: Optional[discord.Guild] = None,
        override: bool = False,
    ) -> None:
        config = getattr((self.config.guild(guild) if guild else self.config), white_black_list)
        async with config() as target_list:
            for item in users_or_roles:
                actual = str(getattr(item, "id", item))
                target_list.pop(actual, None)
                if white_black_list == "whitelist":
                    self._cache.update_whitelist(guild, target_list)
                else:
                    self._cache.update_blacklist(guild, target_list)

        if not override:
            coro = getattr(self.bot, f"remove_from_{white_black_list}")
            await coro(users_or_roles, guild=guild, adv_bl=True)

    async def clear_list(
        self,
        *,
        white_black_list: _WhiteBlacklist,
        guild: Optional[discord.Guild] = None,
        override: bool = False,
    ) -> None:
        if white_black_list == "whitelist":
            self._cache.clear_whitelist(guild)
        else:
            self._cache.clear_blacklist(guild)

        if guild:
            await getattr(self.config.guild(guild), white_black_list).clear()
        else:
            await getattr(self.config, white_black_list).clear()

        if not override:
            await getattr(self.bot, f"clear_{white_black_list}")(guild=guild, adv_bl=True)

    async def get_list(
        self, *, white_black_list: _WhiteBlacklist, guild: Optional[discord.Guild] = None
    ) -> Dict[str, str]:
        if white_black_list == "whitelist":
            cached = self._cache.get_whitelist(guild)
        else:
            cached = self._cache.get_blacklist(guild)
        if cached:
            return cached

        config = getattr((self.config.guild(guild) if guild else self.config), white_black_list)
        data = await config()
        if data:
            return data

        bot_list = await getattr(self.bot, f"get_{white_black_list}")(guild)
        if not bot_list:
            return {}

        imported = {str(uid): "Pre-existing entry / No reason provided." for uid in bot_list}
        await config.set(imported)
        if white_black_list == "whitelist":
            self._cache.update_whitelist(guild, imported)
        else:
            self._cache.update_blacklist(guild, imported)
        return imported

    async def edit_reason(
        self,
        user_or_role: UserOrRole,
        *,
        white_black_list: _WhiteBlacklist,
        reason: str,
        guild: Optional[discord.Guild] = None,
    ) -> None:
        config = getattr((self.config.guild(guild) if guild else self.config), white_black_list)
        async with config() as target_list:
            actual = str(getattr(user_or_role, "id", user_or_role))
            target_list[actual] = reason
            if white_black_list == "whitelist":
                self._cache.update_whitelist(guild, target_list)
            else:
                self._cache.update_blacklist(guild, target_list)

    # Listeners
    @commands.Cog.listener()
    async def on_add_to_blacklist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl: return
        await self.add_to_list(users, white_black_list="blacklist", reason="No reason provided.", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_remove_from_blacklist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl: return
        await self.remove_from_list(users, white_black_list="blacklist", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_blacklist_clear(self, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl: return
        await self.clear_list(white_black_list="blacklist", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_add_to_whitelist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl: return
        await self.add_to_list(users, white_black_list="whitelist", reason="No reason provided.", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_remove_from_whitelist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl: return
        await self.remove_from_list(users, white_black_list="whitelist", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_whitelist_clear(self, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl: return
        await self.clear_list(white_black_list="whitelist", guild=guild, override=True)

    # Global Blocklist Commands
    @commands.group(name="blocklist", aliases=["denylist", "blacklist"], invoke_without_command=True)
    @commands.is_owner()
    async def blocklist(self, ctx: commands.Context) -> None:
        """Manage the bot's global blocklist."""
        await ctx.send_help()

    @blocklist.command(name="status", aliases=["info"])
    async def blocklist_status(self, ctx: commands.Context) -> None:
        """Display an all-in-one V2 System Dashboard."""
        gl_bl = len(await self.get_list(white_black_list="blacklist", guild=None))
        gl_wl = len(await self.get_list(white_black_list="whitelist", guild=None))
        log_ch = await self.config.log_channel()
        log_str = f"<#{log_ch}>" if log_ch else "`Disabled`"

        payload = {
            "flags": 32768,
            "components": [
                {
                    "type": 17,
                    "accent_color": 0x5865F2,
                    "components": [
                        {"type": 10, "content": "### 📊 Global Blacklist System Dashboard"},
                        {"type": 14, "spacing": 1, "divider": True},
                        {"type": 10, "content": f"**Global Blocklist Count:** `{gl_bl}` users\n**Global Allowlist Count:** `{gl_wl}` users\n**Log Channel:** {log_str}\n**Version:** `{self.__version__}`"}
                    ]
                }
            ]
        }
        await self._send_raw_v2_payload(ctx.channel.id, payload)

    @blocklist.command(name="setchannel")
    async def blocklist_setchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Set the private channel for global blacklist logs."""
        if channel:
            await self.config.log_channel.set(channel.id)
            await ctx.send(f"Global blocklist actions will now be logged to {channel.mention}.")
        else:
            await self.config.log_channel.set(None)
            await ctx.send("Global blocklist logging has been disabled.")

    @blocklist.command(name="check")
    async def blocklist_check(self, ctx: commands.Context, user_or_role: discord.User) -> None:
        """Inspect a user's status on the global blocklist."""
        data = await self.get_list(white_black_list="blacklist", guild=None)
        actual = str(user_or_role.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user_or_role, "blacklist", None, is_listed, reason)
        try:
            await self._send_raw_v2_payload(ctx.channel.id, payload)
        except discord.HTTPException as error:
            log.exception("Failed to send V2 blocklist check payload for user %s", user_or_role.id)
            await ctx.send(f"Failed to deliver check layout: `{error}`")

    @blocklist.command(name="add")
    async def blocklist_add(self, ctx: commands.Context, users: commands.Greedy[discord.User], *, reason: Optional[str] = None) -> None:
        """Add users to the global blocklist."""
        if not users:
            await ctx.send_help()
            return
        valid, clean_users = await self._filter_self_harm(ctx, users, "blacklist", None)
        if not valid or not clean_users:
            return

        reason = reason or "No reason provided."
        await self.add_to_list(clean_users, white_black_list="blacklist", reason=reason, guild=None)
        for u in clean_users:
            await self._log_action("Added", u, "blacklist", reason, ctx.author, guild=None)

        desc = f"**Added:** {len(clean_users)} user(s)\n**Reason:** `{reason}`"
        await self._send_action_mini_card(ctx, "🔴 Added to Global Blocklist", desc, 0xED4245, auto_delete=True)

    @blocklist.command(name="remove", aliases=["del", "delete"])
    async def blocklist_remove(self, ctx: commands.Context, users: commands.Greedy[discord.User]) -> None:
        """Remove users from the global blocklist."""
        if not users:
            await ctx.send_help()
            return
        await self.remove_from_list(users, white_black_list="blacklist", guild=None)
        for u in users:
            await self._log_action("Removed", u, "blacklist", "Removed by owner", ctx.author, guild=None)

        desc = f"**Removed:** {len(users)} user(s) from Global Blocklist."
        await self._send_action_mini_card(ctx, "🟢 Removed from Global Blocklist", desc, 0x57F287, auto_delete=True)

    @blocklist.command(name="edit")
    async def blocklist_edit(self, ctx: commands.Context, user: discord.User, *, reason: str) -> None:
        """Edit the reason for a globally blocklisted user."""
        data = await self.get_list(white_black_list="blacklist", guild=None)
        if str(user.id) not in data:
            await ctx.send("That user is not on the global blocklist.")
            return
        await self.edit_reason(user, white_black_list="blacklist", reason=reason, guild=None)
        await self._log_action("Edited Reason", user, "blacklist", reason, ctx.author, guild=None)

        desc = f"**Target:** {user.mention}\n**New Reason:** `{reason}`"
        await self._send_action_mini_card(ctx, "✏️ Updated Blocklist Reason", desc, 0x5865F2, auto_delete=True)

    @blocklist.command(name="list")
    async def blocklist_list(self, ctx: commands.Context) -> None:
        """List all users on the global blocklist using V2 Hybrid Cards."""
        await self.send_v2_list(ctx, white_black_list="blacklist", guild=None)

    @blocklist.command(name="clear")
    async def blocklist_clear(self, ctx: commands.Context, confirm: bool = False) -> None:
        """Clear all users from the global blocklist."""
        if not confirm:
            data = await self.get_list(white_black_list="blacklist", guild=None)
            warn_payload = {
                "flags": 32768,
                "components": [
                    {
                        "type": 17,
                        "accent_color": 0xED4245,
                        "components": [
                            {"type": 10, "content": "### ⚠️ DESTRUCTIVE ACTION WARNING"},
                            {"type": 14, "spacing": 1, "divider": True},
                            {"type": 10, "content": f"Are you sure you want to permanently clear **{len(data)}** entry/entries from the global blocklist?"}
                        ]
                    }
                ]
            }
            with contextlib.suppress(discord.HTTPException):
                await self._send_raw_v2_payload(ctx.channel.id, warn_payload)

            view = ConfirmView(ctx)
            msg = await ctx.send("Confirm wiping the list below:", view=view)
            await view.wait()
            with contextlib.suppress(discord.HTTPException):
                await msg.delete()
            if not view.value:
                await ctx.send("Action canceled.")
                return

        await self.clear_list(white_black_list="blacklist", guild=None)
        await self._log_action("Cleared List", "All Users", "blacklist", "Wiped by owner", ctx.author, guild=None)
        await ctx.send("Cleared all users from the global blocklist.")

    @blocklist.command(name="format")
    async def blocklist_format(self, ctx: commands.Context) -> None:
        """Edit format template with a Live V2 Preview Card."""
        current = await self.config.format()

        preview_payload = {
            "flags": 32768,
            "components": [
                {
                    "type": 17,
                    "accent_color": 0x5865F2,
                    "components": [
                        {"type": 10, "content": f"### 👁️ Live Format Preview: {current['title']}"},
                        {"type": 14, "spacing": 1, "divider": True},
                        {"type": 10, "content": f"1. SampleUser: Spamming DMs\n2. SampleBot: Raiding"},
                        {"type": 14, "spacing": 1, "divider": True},
                        {"type": 10, "content": f"-# {current['footer']}"}
                    ]
                }
            ]
        }
        with contextlib.suppress(discord.HTTPException):
            await self._send_raw_v2_payload(ctx.channel.id, preview_payload)

        await ctx.send(
            "Click below to edit or reset the list display template:",
            view=FormatView(self.bot, ctx, self.config, current)
        )

    # Local Blocklist Commands
    @commands.group(name="localblocklist", aliases=["localblacklist", "localdenylist"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def local_blocklist(self, ctx: commands.Context) -> None:
        """Manage the server's local blocklist."""
        await ctx.send_help()

    @local_blocklist.command(name="check")
    async def local_blocklist_check(self, ctx: commands.Context, user_or_role: Union[discord.Member, discord.Role]) -> None:
        """Inspect a member/role on the local blocklist."""
        data = await self.get_list(white_black_list="blacklist", guild=ctx.guild)
        actual = str(user_or_role.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user_or_role, "blacklist", ctx.guild, is_listed, reason)
        try:
            await self._send_raw_v2_payload(ctx.channel.id, payload)
        except discord.HTTPException as error:
            log.exception("Failed to send V2 local blocklist check payload for ID %s", user_or_role.id)
            await ctx.send(f"Failed to deliver check layout: `{error}`")

    @local_blocklist.command(name="add")
    async def local_blocklist_add(self, ctx: commands.Context, users_or_roles: commands.Greedy[Union[discord.Member, discord.Role]], *, reason: Optional[str] = None) -> None:
        """Add users/roles to local blocklist."""
        if not users_or_roles:
            await ctx.send_help()
            return
        valid, clean_items = await self._filter_self_harm(ctx, users_or_roles, "blacklist", ctx.guild)
        if not valid or not clean_items: return

        reason = reason or "No reason provided."
        await self.add_to_list(clean_items, white_black_list="blacklist", reason=reason, guild=ctx.guild)
        for item in clean_items:
            await self._log_action("Added", item, "blacklist", reason, ctx.author, guild=ctx.guild)

        desc = f"**Added:** {len(clean_items)} item(s) to Local Blocklist.\n**Reason:** `{reason}`"
        await self._send_action_mini_card(ctx, "🔴 Added to Local Blocklist", desc, 0xED4245, auto_delete=True)

    @local_blocklist.command(name="remove", aliases=["del", "delete"])
    async def local_blocklist_remove(self, ctx: commands.Context, users_or_roles: commands.Greedy[Union[discord.Member, discord.Role]]) -> None:
        """Remove users/roles from local blocklist."""
        if not users_or_roles:
            await ctx.send_help()
            return
        await self.remove_from_list(users_or_roles, white_black_list="blacklist", guild=ctx.guild)
        for item in users_or_roles:
            await self._log_action("Removed", item, "blacklist", "Removed by admin", ctx.author, guild=ctx.guild)

        desc = f"**Removed:** {len(users_or_roles)} item(s) from Local Blocklist."
        await self._send_action_mini_card(ctx, "🟢 Removed from Local Blocklist", desc, 0x57F287, auto_delete=True)

    @local_blocklist.command(name="list")
    async def local_blocklist_list(self, ctx: commands.Context) -> None:
        """List local blocklist using V2 Hybrid Cards."""
        await self.send_v2_list(ctx, white_black_list="blacklist", guild=ctx.guild)

    # Global Allowlist Commands
    @commands.group(name="allowlist", aliases=["whitelist"], invoke_without_command=True)
    @commands.is_owner()
    async def allowlist(self, ctx: commands.Context) -> None:
        """Manage the bot's global allowlist."""
        await ctx.send_help()

    @allowlist.command(name="check")
    async def allowlist_check(self, ctx: commands.Context, user: discord.User) -> None:
        """Inspect a user's status on global allowlist."""
        data = await self.get_list(white_black_list="whitelist", guild=None)
        actual = str(user.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user, "whitelist", None, is_listed, reason)
        try:
            await self._send_raw_v2_payload(ctx.channel.id, payload)
        except discord.HTTPException as error:
            log.exception("Failed to send V2 allowlist check payload for user %s", user.id)
            await ctx.send(f"Failed to deliver check layout: `{error}`")

    @allowlist.command(name="list")
    async def allowlist_list(self, ctx: commands.Context) -> None:
        """List global allowlist using V2 Hybrid Cards."""
        await self.send_v2_list(ctx, white_black_list="whitelist", guild=None)

    # Local Allowlist Commands
    @commands.group(name="localallowlist", aliases=["localwhitelist"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def local_allowlist(self, ctx: commands.Context) -> None:
        """Manage the server's local allowlist."""
        await ctx.send_help()

    @local_allowlist.command(name="check")
    async def local_allowlist_check(self, ctx: commands.Context, user_or_role: Union[discord.Member, discord.Role]) -> None:
        """Inspect local allowlist status."""
        data = await self.get_list(white_black_list="whitelist", guild=ctx.guild)
        actual = str(user_or_role.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user_or_role, "whitelist", ctx.guild, is_listed, reason)
        try:
            await self._send_raw_v2_payload(ctx.channel.id, payload)
        except discord.HTTPException as error:
            log.exception("Failed to send V2 local allowlist check payload for ID %s", user_or_role.id)
            await ctx.send(f"Failed to deliver check layout: `{error}`")

    @local_allowlist.command(name="list")
    async def local_allowlist_list(self, ctx: commands.Context) -> None:
        """List local allowlist using V2 Hybrid Cards."""
        await self.send_v2_list(ctx, white_black_list="whitelist", guild=ctx.guild)

    # Hybrid V2 List Pagination Generator
    async def send_v2_list(
        self,
        ctx: commands.Context,
        *,
        white_black_list: _WhiteBlacklist,
        guild: Optional[discord.Guild],
    ) -> None:
        allow_deny = "allowlist" if white_black_list == "whitelist" else "blocklist"
        local = f"Local ({guild.name}) " if guild else "Global "

        target_list = await self.get_list(white_black_list=white_black_list, guild=guild)
        if not target_list:
            await ctx.send(f"There are no users or roles on the {local}{allow_deny}.")
            return

        items = list(target_list.items())
        chunk_size = 8
        chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
        total_pages = len(chunks)

        v2_pages: List[dict] = []
        for page_idx, chunk in enumerate(chunks, 1):
            container_components = [
                {"type": 10, "content": f"### 📜 {local}{allow_deny.capitalize()} (Page {page_idx}/{total_pages})"}
            ]
            for item, reason in chunk:
                name = None
                if item.isdigit():
                    uid = int(item)
                    user = self.bot.get_user(uid)
                    if user: name = user.name
                    elif guild:
                        role = guild.get_role(uid)
                        if role: name = role.name
                name = name or item

                container_components.append({"type": 14, "spacing": 1, "divider": True})
                container_components.append({"type": 10, "content": f"**{name}** (`{item}`)\nReason: `{reason}`"})

            v2_pages.append({
                "flags": 32768,
                "components": [{"type": 17, "accent_color": 0x5865F2, "components": container_components}]
            })

        response = await self._send_raw_v2_payload(ctx.channel.id, v2_pages[0])
        menu = V2ListMenu(self, ctx, v2_pages)
        menu.v2_msg_id = int(response["id"])
        menu.btn_msg = await ctx.send(content="-# **Navigate list pages:**", view=menu)
