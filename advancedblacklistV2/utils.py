# Copyright (c) 2021-2026 Jojo#7791, SinOfStrife and Contributors
# Licensed under MIT

from __future__ import annotations

import datetime
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Callable, Dict, Final, Iterable, List, Literal, Optional, Union

import discord
from discord.ui.button import button as button_dec
from redbot.core import Config, commands
from redbot.core.bot import Red

if TYPE_CHECKING:
    from .core import AdvancedBlacklistV2

_WhiteBlacklist = Literal["whitelist", "blacklist"]
UserOrRole = Union[discord.Member, discord.User, discord.Role, int]
UsersOrRoles = Iterable[UserOrRole]

default_format: Final[Dict[str, str]] = {
    "title": "{allow_deny_list}",
    "user_or_role": "{index}. {user_or_role}: {reason}",
    "footer": "{bot_name} running AdvancedBlacklist {version_info}",
}

config_structure: Final[Dict[str, Dict[str, Any]]] = {
    "global": {
        "blacklist": {},
        "whitelist": {},
        "schema_v1": 1,
        "log_channel": None,
        "format": default_format,
    },
    "guild": {
        "blacklist": {},
        "whitelist": {},
        "log_channel": None,
    },
}


def _timestamp() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _str_timestamp(ts: datetime.datetime) -> str:
    return f"<t:{int(ts.timestamp())}:f>"


class Cache:
    """In-memory cache for O(1) blacklist and whitelist lookups."""
    def __init__(self):
        self.__bl_internal = {"global": {}, "guild": {}}
        self.__wl_internal = {"global": {}, "guild": {}}

    def get_whitelist(self, guild: Optional[discord.Guild]) -> dict:
        if guild:
            return self.__wl_internal["guild"].get(guild.id, {})
        return self.__wl_internal["global"]

    def get_blacklist(self, guild: Optional[discord.Guild]) -> dict:
        if guild:
            return self.__bl_internal["guild"].get(guild.id, {})
        return self.__bl_internal["global"]

    def update_whitelist(self, guild: Optional[discord.Guild], data: dict) -> None:
        if guild:
            self.__wl_internal["guild"].setdefault(guild.id, {}).update(data)
            return
        self.__wl_internal["global"].update(data)

    def update_blacklist(self, guild: Optional[discord.Guild], data: dict) -> None:
        if guild:
            self.__bl_internal["guild"].setdefault(guild.id, {}).update(data)
            return
        self.__bl_internal["global"].update(data)

    def clear_whitelist(self, guild: Optional[discord.Guild]) -> None:
        if guild:
            self.__wl_internal["guild"][guild.id] = {}
            return
        self.__wl_internal["global"] = {}

    def clear_blacklist(self, guild: Optional[discord.Guild]) -> None:
        if guild:
            self.__bl_internal["guild"][guild.id] = {}
            return
        self.__bl_internal["global"] = {}


# --- Interactive Modals ---

class ReasonModal(discord.ui.Modal):
    def __init__(self, current_reason: str, on_submit_coro: Callable[[str], Any]):
        super().__init__(title="Edit Reason")
        self.on_submit_coro = on_submit_coro
        self.reason_input = discord.ui.TextInput(
            label="New Reason",
            style=discord.TextStyle.paragraph,
            default=current_reason if current_reason != "No reason provided." else "",
            placeholder="Provide a reason...",
            required=True,
            max_length=500,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.on_submit_coro(self.reason_input.value.strip())


class BlacklistModal(discord.ui.Modal):
    def __init__(self, target_name: str, on_submit_coro: Callable[[str], Any]):
        super().__init__(title=f"Add {target_name[:30]}")
        self.on_submit_coro = on_submit_coro
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Why is this user being added?",
            required=False,
            max_length=500,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        reason = self.reason_input.value.strip() or "No reason provided."
        await self.on_submit_coro(reason)


# --- Dynamic Check Card View (Owner / Admin Only) ---

class CheckUserView(discord.ui.View):
    def __init__(
        self,
        cog: AdvancedBlacklistV2,
        ctx: commands.Context,
        target: UserOrRole,
        white_black_list: _WhiteBlacklist,
        guild: Optional[discord.Guild],
        is_listed: bool,
        reason: str,
    ):
        super().__init__(timeout=120.0)
        self.cog = cog
        self.ctx = ctx
        self.target = target
        self.white_black_list = white_black_list
        self.guild = guild
        self.is_listed = is_listed
        self.reason = reason
        self.msg: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.clear_items()
        action_name = "Allowlist" if self.white_black_list == "whitelist" else "Blocklist"

        if self.is_listed:
            edit_btn = discord.ui.Button(label="Edit Reason", style=discord.ButtonStyle.green, custom_id="edit_reason")
            edit_btn.callback = self._on_edit_reason
            self.add_item(edit_btn)

            remove_btn = discord.ui.Button(label=f"Remove from {action_name}", style=discord.ButtonStyle.red, custom_id="remove_list")
            remove_btn.callback = self._on_remove
            self.add_item(remove_btn)
        else:
            add_btn = discord.ui.Button(label=f"Add to {action_name}", style=discord.ButtonStyle.red, custom_id="add_list")
            add_btn.callback = self._on_add
            self.add_item(add_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.guild:
            if not getattr(interaction.user.guild_permissions, "administrator", False):
                await interaction.response.send_message("You are not authorized to use these buttons.", ephemeral=True)
                return False
        else:
            if not await self.cog.bot.is_owner(interaction.user):
                await interaction.response.send_message("You are not authorized to use these buttons.", ephemeral=True)
                return False
        return True

    async def _on_edit_reason(self, interaction: discord.Interaction) -> None:
        async def submit_callback(new_reason: str):
            await self.cog.edit_reason(self.target, white_black_list=self.white_black_list, reason=new_reason, guild=self.guild)
            await self.cog._log_action("Edited Reason", self.target, self.white_black_list, new_reason, interaction.user, self.guild)
            self.reason = new_reason
            await self._refresh_card(interaction)

        modal = ReasonModal(self.reason, submit_callback)
        await interaction.response.send_modal(modal)

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.cog.remove_from_list({self.target}, white_black_list=self.white_black_list, guild=self.guild)
        await self.cog._log_action("Removed (Button)", self.target, self.white_black_list, "Removed via check card", interaction.user, self.guild)
        self.is_listed = False
        self.reason = "None"
        self._update_buttons()
        await self._refresh_card(interaction)

    async def _on_add(self, interaction: discord.Interaction) -> None:
        target_name = getattr(self.target, "name", str(self.target))

        async def submit_callback(reason: str):
            await self.cog.add_to_list({self.target}, white_black_list=self.white_black_list, reason=reason, guild=self.guild)
            await self.cog._log_action("Added (Button)", self.target, self.white_black_list, reason, interaction.user, self.guild)
            self.is_listed = True
            self.reason = reason
            self._update_buttons()
            await self._refresh_card(interaction)

        modal = BlacklistModal(target_name, submit_callback)
        await interaction.response.send_modal(modal)

    async def _refresh_card(self, interaction: Optional[discord.Interaction] = None) -> None:
        embed = self.cog._build_check_embed(self.target, self.white_black_list, self.guild, self.is_listed, self.reason)
        target_msg = self.msg or getattr(interaction, "message", None)
        if target_msg:
            with suppress(discord.HTTPException):
                await target_msg.edit(embed=embed, view=self)

    async def on_timeout(self) -> None:
        self.stop()
        if self.msg:
            with suppress(discord.HTTPException):
                await self.msg.edit(view=None)


# --- Confirmation & Pagination Views ---

class ConfirmView(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=30.0)
        self.ctx = ctx
        self.value: Optional[bool] = None

    @button_dec(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.value = True
        self.stop()

    @button_dec(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.value = False
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You cannot confirm this action.", ephemeral=True)
            return False
        return True


class Page:
    def __init__(self, ctx: commands.Context, data: List[str], *, title: str, footer: str):
        self.ctx = ctx
        self.data = data
        self.title = title
        self.footer = footer
        self.max_len = len(self.data)

    async def format_page(self, page: str) -> dict:
        if await self.ctx.embed_requested():
            embed = discord.Embed(
                title=self.title,
                description=page,
                colour=await self.ctx.embed_colour(),
                timestamp=_timestamp(),
            )
            embed.set_footer(text=self.footer)
            return {"embed": embed}
        return {"content": f"# {self.title}\n\n{page}\n-# {self.footer}"}

    def __len__(self) -> int:
        return len(self.data)


class Menu(discord.ui.View):
    def __init__(self, source: Page, bot: Red, ctx: commands.Context):
        super().__init__(timeout=120.0)
        self.source = source
        self.bot = bot
        self.ctx = ctx
        self.msg: Optional[discord.Message] = None
        self.current_page: int = 0
        self._add_buttons()

    def _add_buttons(self) -> None:
        prev_btn = discord.ui.Button(style=discord.ButtonStyle.grey, label="◀", disabled=len(self.source) <= 1)
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        stop_btn = discord.ui.Button(style=discord.ButtonStyle.red, label="✕")
        stop_btn.callback = self._on_stop
        self.add_item(stop_btn)

        next_btn = discord.ui.Button(style=discord.ButtonStyle.grey, label="▶", disabled=len(self.source) <= 1)
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.current_page = (self.current_page - 1) % self.source.max_len
        await self._show_page()

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.current_page = (self.current_page + 1) % self.source.max_len
        await self._show_page()

    async def _on_stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        if self.msg:
            with suppress(discord.HTTPException):
                await self.msg.delete()

    async def _show_page(self) -> None:
        page_content = self.source.data[self.current_page]
        kwargs = await self.source.format_page(page_content)
        if self.msg:
            await self.msg.edit(view=self, **kwargs)

    @classmethod
    async def start(cls, source: Page, ctx: commands.Context) -> None:
        self = cls(source, ctx.bot, ctx)
        kwargs = await source.format_page(source.data[0])
        self.msg = await ctx.send(view=self, **kwargs)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return False
        return True


class FormatModal(discord.ui.Modal):
    def __init__(self, current_settings: Dict[str, str], on_save: Callable[[Dict[str, str]], Any]):
        super().__init__(title="Change List Format")
        self.on_save = on_save
        self.input_title = discord.ui.TextInput(
            label="Title",
            style=discord.TextStyle.short,
            required=False,
            default=current_settings.get("title", ""),
        )
        self.input_user_or_role = discord.ui.TextInput(
            label="User or Role format",
            style=discord.TextStyle.paragraph,
            required=False,
            default=current_settings.get("user_or_role", ""),
        )
        self.input_footer = discord.ui.TextInput(
            label="Footer",
            style=discord.TextStyle.short,
            required=False,
            default=current_settings.get("footer", ""),
        )
        self.add_item(self.input_title)
        self.add_item(self.input_user_or_role)
        self.add_item(self.input_footer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        new_settings = {
            "title": self.input_title.value.strip() or default_format["title"],
            "user_or_role": self.input_user_or_role.value.strip() or default_format["user_or_role"],
            "footer": self.input_footer.value.strip() or default_format["footer"],
        }
        await self.on_save(new_settings)


class FormatView(discord.ui.View):
    def __init__(self, bot: Red, ctx: commands.Context, config: Config, current_format: Dict[str, str]):
        super().__init__(timeout=60.0)
        self.bot = bot
        self.ctx = ctx
        self.config = config
        self.current_format = current_format
        self.msg: Optional[discord.Message] = None

    @button_dec(label="Edit Format", style=discord.ButtonStyle.green)
    async def edit_format(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def save_callback(settings: Dict[str, str]):
            await self.config.format.set(settings)
            self.current_format = settings
            await self.ctx.send("List format successfully updated!")

        await interaction.response.send_modal(FormatModal(self.current_format, save_callback))

    @button_dec(label="Reset Default", style=discord.ButtonStyle.red)
    async def reset_format(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self.config.format.set(default_format)
        self.current_format = default_format
        await self.ctx.send("List format has been reset to default.")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can configure this.", ephemeral=True)
            return False
        return True