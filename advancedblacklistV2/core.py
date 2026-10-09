# Copyright (c) 2021-2026 Jojo#7791, SinOfStrife and Contributors
# Licensed under MIT

from __future__ import annotations

import contextlib
import logging
from typing import Dict, Final, List, Literal, Optional, Tuple, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

from .patching import Patch
from .utils import (
    Cache,
    CheckUserView,
    ConfirmView,
    FormatView,
    Menu,
    Page,
    UserOrRole,
    UsersOrRoles,
    _WhiteBlacklist,
    _str_timestamp,
    _timestamp,
    config_structure,
    default_format,
)

log = logging.getLogger("red.strifecogs.advancedblacklistv2")

_CORE_NAMES: Final[Tuple[str, ...]] = (
    "blacklist", "blocklist", "whitelist", "allowlist",
    "localblacklist", "localblocklist", "localwhitelist", "localallowlist"
)


def _format_pages(show: List[str]) -> List[str]:
    pages: List[str] = []
    current_page: str = ""
    for page in show:
        current_page += f"\n{page}"
        if len(current_page) > 1800:
            pages.append(current_page)
            current_page = ""
    if current_page:
        pages.append(current_page)
    return pages


def _format_str(string: str, replace: Dict[str, str]) -> str:
    for key, value in replace.items():
        string = string.replace(key, value)
    return string


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

        # Detach Red core commands BEFORE the cog registers its own commands
        self._original_coms: List[commands.Command] = []
        for name in _CORE_NAMES:
            cmd = self.bot.remove_command(name)
            if cmd and cmd not in self._original_coms:
                self._original_coms.append(cmd)

        self._cache = Cache()

    async def cog_load(self) -> None:
        """Startup lifecycle: patch methods and import pre-existing bans."""
        await self._patch.startup()
        await self._sync_preexisting_data()

    async def cog_unload(self) -> None:
        """Safely restore all original commands and remove monkey-patches."""
        await self._patch.destroy()
        for com in self._original_coms:
            self.bot.remove_command(com.name)
            self.bot.add_command(com)
        log.info("AdvancedBlacklistV2 unloaded: Restored native Red core blacklist commands.")

    async def _sync_preexisting_data(self) -> None:
        """Auto-imports core blacklisted and whitelisted IDs so lists never desync."""
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

    # --- GDPR / End-User Data Handlers ---

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

    # --- Isolated Logging Helper (Components V2) ---

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
            "flags": 32768,  # IS_COMPONENTS_V2
            "components": [
                {
                    "type": 17,  # Container
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
            await channel.send(**payload)

    # --- Core Helpers & Protections ---

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
            "flags": 32768,  # IS_COMPONENTS_V2
            "components": [
                {
                    "type": 17,
                    "accent_color": color_int,
                    "components": container_components
                }
            ]
        }

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

    # --- Listeners ---

    @commands.Cog.listener()
    async def on_add_to_blacklist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl:
            return
        await self.add_to_list(users, white_black_list="blacklist", reason="No reason provided.", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_remove_from_blacklist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl:
            return
        await self.remove_from_list(users, white_black_list="blacklist", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_blacklist_clear(self, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl:
            return
        await self.clear_list(white_black_list="blacklist", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_add_to_whitelist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl:
            return
        await self.add_to_list(users, white_black_list="whitelist", reason="No reason provided.", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_remove_from_whitelist(self, users: UsersOrRoles, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl:
            return
        await self.remove_from_list(users, white_black_list="whitelist", guild=guild, override=True)

    @commands.Cog.listener()
    async def on_whitelist_clear(self, guild: Optional[discord.Guild], adv_bl: bool = False) -> None:
        if adv_bl:
            return
        await self.clear_list(white_black_list="whitelist", guild=guild, override=True)

    # ==========================================
    # GLOBAL BLOCKLIST COMMANDS ([p]blocklist)
    # ==========================================
    @commands.group(name="blocklist", aliases=["denylist", "blacklist"], invoke_without_command=True)
    @commands.is_owner()
    async def blocklist(self, ctx: commands.Context) -> None:
        """Manage the bot's global blocklist."""
        await ctx.send_help()

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
        """Inspect a user's status with interactive action row buttons."""
        data = await self.get_list(white_black_list="blacklist", guild=None)
        actual = str(user_or_role.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user_or_role, "blacklist", None, is_listed, reason)
        view = CheckUserView(self, ctx, user_or_role, "blacklist", None, is_listed, reason)
        view.msg = await ctx.channel.send(**payload, view=view)

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
        await ctx.send(f"Added {len(clean_users)} user(s) to the global blocklist with reason: `{reason}`")

    @blocklist.command(name="remove", aliases=["del", "delete"])
    async def blocklist_remove(self, ctx: commands.Context, users: commands.Greedy[discord.User]) -> None:
        """Remove users from the global blocklist."""
        if not users:
            await ctx.send_help()
            return
        await self.remove_from_list(users, white_black_list="blacklist", guild=None)
        for u in users:
            await self._log_action("Removed", u, "blacklist", "Removed by owner", ctx.author, guild=None)
        await ctx.send(f"Removed {len(users)} user(s) from the global blocklist.")

    @blocklist.command(name="edit")
    async def blocklist_edit(self, ctx: commands.Context, user: discord.User, *, reason: str) -> None:
        """Edit the reason for a globally blocklisted user."""
        data = await self.get_list(white_black_list="blacklist", guild=None)
        if str(user.id) not in data:
            await ctx.send("That user is not on the global blocklist.")
            return
        await self.edit_reason(user, white_black_list="blacklist", reason=reason, guild=None)
        await self._log_action("Edited Reason", user, "blacklist", reason, ctx.author, guild=None)
        await ctx.send(f"Updated reason for **{user}** to: `{reason}`")

    @blocklist.command(name="list")
    async def blocklist_list(self, ctx: commands.Context) -> None:
        """List all users on the global blocklist."""
        await self.send_list(ctx, white_black_list="blacklist", guild=None)

    @blocklist.command(name="clear")
    async def blocklist_clear(self, ctx: commands.Context, confirm: bool = False) -> None:
        """Clear all users from the global blocklist."""
        if not confirm:
            view = ConfirmView(ctx)
            msg = await ctx.send("⚠️ Are you sure you want to completely clear the global blocklist?", view=view)
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
        """Edit the visual format template for lists."""
        current = await self.config.format()
        await ctx.send("Click below to edit or reset the list display template:", view=FormatView(self.bot, ctx, self.config, current))

    # ==========================================
    # LOCAL BLOCKLIST COMMANDS ([p]localblocklist)
    # ==========================================
    @commands.group(name="localblocklist", aliases=["localblacklist", "localdenylist"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def local_blocklist(self, ctx: commands.Context) -> None:
        """Manage the server's local blocklist."""
        await ctx.send_help()

    @local_blocklist.command(name="setchannel")
    async def local_blocklist_setchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Set the channel for local server blocklist logs."""
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Local blocklist actions will now be logged to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Local blocklist logging has been disabled.")

    @local_blocklist.command(name="check")
    async def local_blocklist_check(self, ctx: commands.Context, user_or_role: Union[discord.Member, discord.Role]) -> None:
        """Inspect a local member's status with interactive action row buttons."""
        data = await self.get_list(white_black_list="blacklist", guild=ctx.guild)
        actual = str(user_or_role.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user_or_role, "blacklist", ctx.guild, is_listed, reason)
        view = CheckUserView(self, ctx, user_or_role, "blacklist", ctx.guild, is_listed, reason)
        view.msg = await ctx.channel.send(**payload, view=view)

    @local_blocklist.command(name="add")
    async def local_blocklist_add(self, ctx: commands.Context, users_or_roles: commands.Greedy[Union[discord.Member, discord.Role]], *, reason: Optional[str] = None) -> None:
        """Add users or roles to the local server blocklist."""
        if not users_or_roles:
            await ctx.send_help()
            return
        valid, clean_items = await self._filter_self_harm(ctx, users_or_roles, "blacklist", ctx.guild)
        if not valid or not clean_items:
            return

        reason = reason or "No reason provided."
        await self.add_to_list(clean_items, white_black_list="blacklist", reason=reason, guild=ctx.guild)
        for item in clean_items:
            await self._log_action("Added", item, "blacklist", reason, ctx.author, guild=ctx.guild)
        await ctx.send(f"Added {len(clean_items)} item(s) to the local blocklist.")

    @local_blocklist.command(name="edit")
    async def local_blocklist_edit(self, ctx: commands.Context, user_or_role: Union[discord.Member, discord.Role], *, reason: str) -> None:
        """Edit the reason for a locally blocklisted user or role."""
        data = await self.get_list(white_black_list="blacklist", guild=ctx.guild)
        if str(user_or_role.id) not in data:
            await ctx.send("That user or role is not on the local blocklist.")
            return
        await self.edit_reason(user_or_role, white_black_list="blacklist", reason=reason, guild=ctx.guild)
        await self._log_action("Edited Reason", user_or_role, "blacklist", reason, ctx.author, guild=ctx.guild)
        await ctx.send(f"Updated reason for **{user_or_role}** to: `{reason}`")

    @local_blocklist.command(name="remove", aliases=["del", "delete"])
    async def local_blocklist_remove(self, ctx: commands.Context, users_or_roles: commands.Greedy[Union[discord.Member, discord.Role]]) -> None:
        """Remove users or roles from the local server blocklist."""
        if not users_or_roles:
            await ctx.send_help()
            return
        await self.remove_from_list(users_or_roles, white_black_list="blacklist", guild=ctx.guild)
        for item in users_or_roles:
            await self._log_action("Removed", item, "blacklist", "Removed by admin", ctx.author, guild=ctx.guild)
        await ctx.send(f"Removed {len(users_or_roles)} item(s) from the local blocklist.")

    @local_blocklist.command(name="list")
    async def local_blocklist_list(self, ctx: commands.Context) -> None:
        """List users/roles on the server's local blocklist."""
        await self.send_list(ctx, white_black_list="blacklist", guild=ctx.guild)

    @local_blocklist.command(name="clear")
    async def local_blocklist_clear(self, ctx: commands.Context, confirm: bool = False) -> None:
        """Clear all entries from the local server blocklist."""
        if not confirm:
            view = ConfirmView(ctx)
            msg = await ctx.send("⚠️ Are you sure you want to completely clear this server's local blocklist?", view=view)
            await view.wait()
            with contextlib.suppress(discord.HTTPException):
                await msg.delete()
            if not view.value:
                await ctx.send("Action canceled.")
                return

        await self.clear_list(white_black_list="blacklist", guild=ctx.guild)
        await self._log_action("Cleared List", "All Server Users", "blacklist", "Wiped by admin", ctx.author, guild=ctx.guild)
        await ctx.send("Cleared all entries from the local blocklist.")

    # ==========================================
    # GLOBAL ALLOWLIST COMMANDS ([p]allowlist)
    # ==========================================
    @commands.group(name="allowlist", aliases=["whitelist"], invoke_without_command=True)
    @commands.is_owner()
    async def allowlist(self, ctx: commands.Context) -> None:
        """Manage the bot's global allowlist (lockdown mode)."""
        await ctx.send_help()

    @allowlist.command(name="check")
    async def allowlist_check(self, ctx: commands.Context, user: discord.User) -> None:
        """Inspect a user's global allowlist status."""
        data = await self.get_list(white_black_list="whitelist", guild=None)
        actual = str(user.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user, "whitelist", None, is_listed, reason)
        view = CheckUserView(self, ctx, user, "whitelist", None, is_listed, reason)
        view.msg = await ctx.channel.send(**payload, view=view)

    @allowlist.command(name="add")
    async def allowlist_add(self, ctx: commands.Context, users: commands.Greedy[discord.User], *, reason: Optional[str] = None) -> None:
        """Add users to the global allowlist."""
        if not users:
            await ctx.send_help()
            return
        valid, clean_users = await self._filter_self_harm(ctx, users, "whitelist", None)
        if not valid or not clean_users:
            return

        reason = reason or "No reason provided."
        await self.add_to_list(clean_users, white_black_list="whitelist", reason=reason, guild=None)
        for u in clean_users:
            await self._log_action("Added", u, "whitelist", reason, ctx.author, guild=None)
        await ctx.send(f"Added {len(clean_users)} user(s) to the global allowlist.")

    @allowlist.command(name="edit")
    async def allowlist_edit(self, ctx: commands.Context, user: discord.User, *, reason: str) -> None:
        """Edit the reason for a globally allowlisted user."""
        data = await self.get_list(white_black_list="whitelist", guild=None)
        if str(user.id) not in data:
            await ctx.send("That user is not on the global allowlist.")
            return
        await self.edit_reason(user, white_black_list="whitelist", reason=reason, guild=None)
        await self._log_action("Edited Reason", user, "whitelist", reason, ctx.author, guild=None)
        await ctx.send(f"Updated reason for **{user}** to: `{reason}`")

    @allowlist.command(name="remove", aliases=["del", "delete"])
    async def allowlist_remove(self, ctx: commands.Context, users: commands.Greedy[discord.User]) -> None:
        """Remove users from the global allowlist."""
        if not users:
            await ctx.send_help()
            return
        await self.remove_from_list(users, white_black_list="whitelist", guild=None)
        for u in users:
            await self._log_action("Removed", u, "whitelist", "Removed by owner", ctx.author, guild=None)
        await ctx.send(f"Removed {len(users)} user(s) from the global allowlist.")

    @allowlist.command(name="list")
    async def allowlist_list(self, ctx: commands.Context) -> None:
        """List all users on the global allowlist."""
        await self.send_list(ctx, white_black_list="whitelist", guild=None)

    @allowlist.command(name="clear")
    async def allowlist_clear(self, ctx: commands.Context, confirm: bool = False) -> None:
        """Clear all users from the global allowlist."""
        if not confirm:
            view = ConfirmView(ctx)
            msg = await ctx.send("⚠️ Are you sure you want to completely clear the global allowlist?", view=view)
            await view.wait()
            with contextlib.suppress(discord.HTTPException):
                await msg.delete()
            if not view.value:
                await ctx.send("Action canceled.")
                return

        await self.clear_list(white_black_list="whitelist", guild=None)
        await self._log_action("Cleared List", "All Users", "whitelist", "Wiped by owner", ctx.author, guild=None)
        await ctx.send("Cleared all users from the global allowlist.")

    # ==========================================
    # LOCAL ALLOWLIST COMMANDS ([p]localallowlist)
    # ==========================================
    @commands.group(name="localallowlist", aliases=["localwhitelist"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def local_allowlist(self, ctx: commands.Context) -> None:
        """Manage the server's local allowlist."""
        await ctx.send_help()

    @local_allowlist.command(name="check")
    async def local_allowlist_check(self, ctx: commands.Context, user_or_role: Union[discord.Member, discord.Role]) -> None:
        """Inspect a member or role's local allowlist status."""
        data = await self.get_list(white_black_list="whitelist", guild=ctx.guild)
        actual = str(user_or_role.id)
        is_listed = actual in data
        reason = data.get(actual, "None")

        payload = self._build_v2_check_payload(user_or_role, "whitelist", ctx.guild, is_listed, reason)
        view = CheckUserView(self, ctx, user_or_role, "whitelist", ctx.guild, is_listed, reason)
        view.msg = await ctx.channel.send(**payload, view=view)

    @local_allowlist.command(name="add")
    async def local_allowlist_add(self, ctx: commands.Context, users_or_roles: commands.Greedy[Union[discord.Member, discord.Role]], *, reason: Optional[str] = None) -> None:
        """Add users or roles to the local server allowlist."""
        if not users_or_roles:
            await ctx.send_help()
            return
        valid, clean_items = await self._filter_self_harm(ctx, users_or_roles, "whitelist", ctx.guild)
        if not valid or not clean_items:
            return

        reason = reason or "No reason provided."
        await self.add_to_list(clean_items, white_black_list="whitelist", reason=reason, guild=ctx.guild)
        for item in clean_items:
            await self._log_action("Added", item, "whitelist", reason, ctx.author, guild=ctx.guild)
        await ctx.send(f"Added {len(clean_items)} item(s) to the local allowlist.")

    @local_allowlist.command(name="edit")
    async def local_allowlist_edit(self, ctx: commands.Context, user_or_role: Union[discord.Member, discord.Role], *, reason: str) -> None:
        """Edit the reason for a locally allowlisted user or role."""
        data = await self.get_list(white_black_list="whitelist", guild=ctx.guild)
        if str(user_or_role.id) not in data:
            await ctx.send("That user or role is not on the local allowlist.")
            return
        await self.edit_reason(user_or_role, white_black_list="whitelist", reason=reason, guild=ctx.guild)
        await self._log_action("Edited Reason", user_or_role, "whitelist", reason, ctx.author, guild=ctx.guild)
        await ctx.send(f"Updated reason for **{user_or_role}** to: `{reason}`")

    @local_allowlist.command(name="remove", aliases=["del", "delete"])
    async def local_allowlist_remove(self, ctx: commands.Context, users_or_roles: commands.Greedy[Union[discord.Member, discord.Role]]) -> None:
        """Remove users or roles from the local server allowlist."""
        if not users_or_roles:
            await ctx.send_help()
            return
        await self.remove_from_list(users_or_roles, white_black_list="whitelist", guild=ctx.guild)
        for item in users_or_roles:
            await self._log_action("Removed", item, "whitelist", "Removed by admin", ctx.author, guild=ctx.guild)
        await ctx.send(f"Removed {len(users_or_roles)} item(s) from the local allowlist.")

    @local_allowlist.command(name="list")
    async def local_allowlist_list(self, ctx: commands.Context) -> None:
        """List users/roles on the server's local allowlist."""
        await self.send_list(ctx, white_black_list="whitelist", guild=ctx.guild)

    @local_allowlist.command(name="clear")
    async def local_allowlist_clear(self, ctx: commands.Context, confirm: bool = False) -> None:
        """Clear all entries from the local server allowlist."""
        if not confirm:
            view = ConfirmView(ctx)
            msg = await ctx.send("⚠️ Are you sure you want to completely clear this server's local allowlist?", view=view)
            await view.wait()
            with contextlib.suppress(discord.HTTPException):
                await msg.delete()
            if not view.value:
                await ctx.send("Action canceled.")
                return

        await self.clear_list(white_black_list="whitelist", guild=ctx.guild)
        await self._log_action("Cleared List", "All Server Users", "whitelist", "Wiped by admin", ctx.author, guild=ctx.guild)
        await ctx.send("Cleared all entries from the local allowlist.")

    # --- Pagination Display Generator ---

    async def send_list(
        self,
        ctx: commands.Context,
        *,
        white_black_list: _WhiteBlacklist,
        guild: Optional[discord.Guild],
    ) -> None:
        allow_deny = "allowlist" if white_black_list == "whitelist" else "blocklist"
        local = f"Local ({guild.name}) " if guild else "Global "
        list_format: Dict[str, str] = await self.config.format()
        format_settings: Dict[str, str] = {
            "{reason}": "",
            "{bot_name}": ctx.me.name,
            "{version_info}": str(self.__version__),
            "{user_or_role}": "",
            "{ur_id}": "0",
            "{allow_deny_list}": f"{local}{allow_deny.capitalize()}",
            "{index}": "0",
        }
        title = _format_str(list_format["title"], format_settings)
        footer = _format_str(list_format["footer"], format_settings)
        user_or_role = list_format["user_or_role"]
        show: List[str] = []

        target_list = await self.get_list(white_black_list=white_black_list, guild=guild)
        if not target_list:
            await ctx.send(f"There are no users or roles on the {local}{allow_deny}.")
            return

        for index, (item, reason) in enumerate(target_list.items(), 1):
            name = None
            if item.isdigit():
                uid = int(item)
                user = self.bot.get_user(uid)
                if user:
                    name = user.name
                elif guild:
                    role = guild.get_role(uid)
                    if role:
                        name = role.name
            name = name or item

            format_settings.update({
                "{user_or_role}": name,
                "{reason}": reason,
                "{index}": str(index),
                "{ur_id}": str(item),
            })
            show.append(_format_str(user_or_role, format_settings))

        show = _format_pages(show)
        page = Page(ctx, show, title=title, footer=footer)
        await Menu.start(page, ctx)
