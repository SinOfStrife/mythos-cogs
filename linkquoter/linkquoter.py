from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Optional
from urllib.parse import urlparse

import aiohttp
import discord
from redbot.core import app_commands, commands
from redbot.core.bot import Red

PK_API = "https://api.pluralkit.me/v2"
_TITLE_LIMIT = 256
_DESC_LIMIT = 4096
_FIELD_LIMIT = 1024
_MAX_CACHE_ITEMS = 500


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…" if limit > 1 else text[:limit]


def _member_label(member: dict[str, Any]) -> str:
    return member.get("display_name") or member.get("name") or "Unknown"


def _embed_color(color_hex: Optional[str]) -> discord.Color:
    if not color_hex:
        return discord.Color.default()
    try:
        return discord.Color(int(str(color_hex).removeprefix("#"), 16))
    except ValueError:
        return discord.Color.default()


def _is_valid_url(url: Any) -> bool:
    if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
        return False
    parsed = urlparse(url)
    return bool(parsed.netloc and parsed.scheme in {"http", "https"} and " " not in url)


class PKAddAppView(discord.ui.View):
    """Single link button letting users choose between personal User App or Server install."""

    def __init__(self, oauth_url: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Add PKLens",
                emoji="➕",
                style=discord.ButtonStyle.link,
                url=oauth_url,
            )
        )


class PKSwitchView(discord.ui.View):
    """Interactive view allowing 1-click switching between Fronters and System Profile."""

    def __init__(
        self,
        cog: PKLens,
        invoker_id: int,
        target_user: discord.User | discord.Member,
        current_view: Literal["fronters", "profile"],
    ):
        super().__init__(timeout=120.0)
        self.cog = cog
        self.invoker_id = invoker_id
        self.target_user = target_user
        self.current_view = current_view
        self._update_button()

    def _update_button(self) -> None:
        self.clear_items()
        if self.current_view == "fronters":
            btn = discord.ui.Button(
                label="View System Profile",
                emoji="👤",
                style=discord.ButtonStyle.secondary,
                custom_id="pk_switch_profile",
            )
            btn.callback = self._on_switch_profile
            self.add_item(btn)
        else:
            btn = discord.ui.Button(
                label="View Current Fronters",
                emoji="🟢",
                style=discord.ButtonStyle.secondary,
                custom_id="pk_switch_fronters",
            )
            btn.callback = self._on_switch_fronters
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "❌ Only the person who ran this lookup can use this button.",
                ephemeral=True,
            )
            return False
        return True

    async def _on_switch_profile(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed, error_kind = await self.cog._build_profile_embed(self.target_user)
        if embed:
            self.current_view = "profile"
            self._update_button()
            await self.cog._safe_send_embed(interaction, embed, view=self, is_edit=True)
        else:
            await interaction.followup.send(
                f"Could not load system profile ({error_kind or 'not found'}).", ephemeral=True
            )

    async def _on_switch_fronters(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed, error_kind = await self.cog._build_fronters_embed(self.target_user)
        if embed:
            self.current_view = "fronters"
            self._update_button()
            await self.cog._safe_send_embed(interaction, embed, view=self, is_edit=True)
        else:
            await interaction.followup.send(
                f"Could not load fronters ({error_kind or 'not found'}).", ephemeral=True
            )

    async def on_timeout(self) -> None:
        self.stop()


class PKLens(commands.Cog):
    """Silently check PluralKit system profiles and current fronters server-wide and via User Apps."""

    __version__ = "1.0.6"
    __author__ = ["SinOfStrife"]
    __red_end_user_data_statement__ = (
        "This cog does not store data. Looking up a user sends their "
        "Discord user ID to the PluralKit API at api.pluralkit.me."
    )

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {"User-Agent": "PKLens/1.0.6 (https://github.com/SinOfStrife/strife-cogs)"}

        # Cache: dict[endpoint, (timestamp, payload)]
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_ttl = 60.0

        # Context Menus (Server & User App Contexts) with capitalized display names
        self.check_fronters_menu = app_commands.ContextMenu(
            name="Fronter",
            callback=self.check_fronter_callback,
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            extras={"red_force_enable": True},
        )
        self.view_profile_menu = app_commands.ContextMenu(
            name="Profile",
            callback=self.view_profile_callback,
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            extras={"red_force_enable": True},
        )

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=20),
        )
        try:
            self.bot.tree.add_command(self.check_fronters_menu)
            self.bot.tree.add_command(self.view_profile_menu)
        except Exception:
            self._drop_menus()
            await self.session.close()
            self.session = None
            raise

    async def cog_unload(self) -> None:
        self._drop_menus()
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _drop_menus(self) -> None:
        for menu in (self.check_fronters_menu, self.view_profile_menu):
            if self.bot.tree.get_command(menu.name, type=menu.type) is not None:
                self.bot.tree.remove_command(menu.name, type=menu.type)

    async def red_get_data_for_user(self, *, user_id: int) -> dict[str, Any]:
        return {}

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        return

    def _save_to_cache(self, endpoint: str, payload: dict[str, Any], now: float) -> None:
        """Saves data to cache and evicts oldest entry if cache exceeds limit."""
        if len(self._cache) >= _MAX_CACHE_ITEMS:
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)
        self._cache[endpoint] = (now, payload)

    async def fetch_pk_data(self, endpoint: str) -> dict[str, Any]:
        now = time.monotonic()
        if endpoint in self._cache:
            timestamp, cached_data = self._cache[endpoint]
            if now - timestamp < self._cache_ttl:
                return cached_data

        if self.session is None or self.session.closed:
            return {"error": "api_error"}

        try:
            async with self.session.get(f"{PK_API}{endpoint}") as resp:
                if resp.status == 404:
                    return {"error": "not_found"}
                if resp.status == 403:
                    return {"error": "private"}
                if resp.status == 429:
                    return {"error": "rate_limited"}
                if resp.status != 200:
                    return {"error": "api_error"}
                payload = await resp.json(content_type=None)
        except Exception:
            return {"error": "api_error"}

        if not isinstance(payload, dict):
            return {"error": "api_error"}

        self._save_to_cache(endpoint, payload, now)
        return payload

    async def _safe_send_embed(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        view: Optional[discord.ui.View] = None,
        is_edit: bool = False,
    ) -> None:
        """Sends or edits an embed, cleanly stripping broken thumbnail URLs if Discord rejects them."""
        try:
            if is_edit:
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except discord.HTTPException as e:
            if e.code == 50035 and embed.thumbnail:  # Invalid Form Body (bad image URL)
                embed.set_thumbnail(url=None)
                if is_edit:
                    await interaction.edit_original_response(embed=embed, view=view)
                else:
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                raise

    async def _send_pk_error(
        self,
        interaction: discord.Interaction,
        user: discord.User | discord.Member,
        data: dict[str, Any],
        kind: str,
    ) -> None:
        err = data.get("error", "api_error")
        if err == "not_found":
            msg = f"**{user.name}** is not registered with PluralKit or has a private profile."
        elif err == "private":
            msg = f"**{user.name}** has set their {kind} to **private**."
        elif err == "rate_limited":
            msg = "PluralKit is currently rate-limited. Please try again in a moment."
        else:
            msg = "An error occurred while contacting the PluralKit API."

        embed = discord.Embed(title="⚠️ Error", description=msg, color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

    # --- Embed Builders ---

    async def _build_fronters_embed(
        self, user: discord.User | discord.Member
    ) -> tuple[Optional[discord.Embed], Optional[str]]:
        data = await self.fetch_pk_data(f"/systems/{user.id}/fronters")
        if "error" in data:
            return None, data.get("error")

        fronters = data.get("members") or []
        if not isinstance(fronters, list):
            return None, "api_error"

        if not fronters:
            embed = discord.Embed(
                title=_clip(f"🟢 Current Fronters: {user.name}", _TITLE_LIMIT),
                description="No system members are currently fronting.",
                color=discord.Color.dark_grey(),
            )
            return embed, None

        lines = []
        for member in fronters:
            if isinstance(member, dict):
                label = _member_label(member)
                pronouns = member.get("pronouns")
                if pronouns and str(pronouns).strip():
                    lines.append(f"• **{label}** *({str(pronouns).strip()})*")
                else:
                    lines.append(f"• **{label}**")

        embed = discord.Embed(
            title=_clip(f"🟢 Current Fronters: {user.name}", _TITLE_LIMIT),
            description=_clip("\n".join(lines) if lines else "Unknown", _DESC_LIMIT),
            color=discord.Color.green(),
        )

        for member in fronters:
            if isinstance(member, dict):
                avatar = member.get("avatar_url")
                if _is_valid_url(avatar):
                    embed.set_thumbnail(url=avatar)
                    break

        return embed, None

    async def _build_profile_embed(
        self, user: discord.User | discord.Member
    ) -> tuple[Optional[discord.Embed], Optional[str]]:
        data = await self.fetch_pk_data(f"/systems/{user.id}")
        if "error" in data:
            return None, data.get("error")

        system_name = data.get("name") or user.name
        tag = data.get("tag")
        title = f"{system_name} [{tag}]" if tag else str(system_name)

        embed = discord.Embed(
            title=_clip(title, _TITLE_LIMIT),
            description=_clip(str(data.get("description") or "No description provided."), _DESC_LIMIT),
            color=_embed_color(data.get("color")),
        )
        embed.add_field(
            name="Pronouns",
            value=_clip(str(data.get("pronouns") or "Not specified."), _FIELD_LIMIT),
            inline=True,
        )

        avatar = data.get("avatar_url")
        if _is_valid_url(avatar):
            embed.set_thumbnail(url=avatar)

        return embed, None

    # --- Core Response Handlers ---

    async def _reply_fronters(self, interaction: discord.Interaction, user: discord.User | discord.Member) -> None:
        embed, error = await self._build_fronters_embed(user)
        if embed:
            view = PKSwitchView(self, interaction.user.id, user, "fronters")
            await self._safe_send_embed(interaction, embed, view=view)
        else:
            await self._send_pk_error(interaction, user, {"error": error or "api_error"}, "fronters")

    async def _reply_profile(self, interaction: discord.Interaction, user: discord.User | discord.Member) -> None:
        embed, error = await self._build_profile_embed(user)
        if embed:
            view = PKSwitchView(self, interaction.user.id, user, "profile")
            await self._safe_send_embed(interaction, embed, view=view)
        else:
            await self._send_pk_error(interaction, user, {"error": error or "api_error"}, "system profile")

    # --- Commands ---

    @app_commands.command(
        name="pklens",
        description="View info about PKLens and how to use it.",
        extras={"red_force_enable": True},
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(public="Set to True to share info with the channel (default: False)")
    async def pklens_help(self, interaction: discord.Interaction, public: bool = False) -> None:
        can_embed = True
        if public and interaction.guild and interaction.channel:
            can_embed = interaction.channel.permissions_for(interaction.guild.me).embed_links

        app_id = interaction.application_id or getattr(self.bot, "application_id", None) or self.bot.user.id
        oauth_url = f"https://discord.com/oauth2/authorize?client_id={app_id}"
        view = PKAddAppView(oauth_url)

        if can_embed:
            embed = discord.Embed(
                title="🔍 PKLens",
                description=(
                    "A lightweight, privacy-focused tool to inspect public PluralKit system profiles "
                    "and current fronters without chat spam.\n\n"
                    "**🌐 Server vs. User App:**\n"
                    "• **User App (Account-level):** Installs directly to your personal Discord profile. "
                    "Works **Discord-wide** in DMs, group chats, and external servers where the bot isn't invited!\n"
                    "• **Server App:** Added to a server so **all members in that server** can use it automatically."
                ),
                color=discord.Color.from_str("#6b2598"),
            )
            embed.add_field(
                name="How to use (on User Profiles or slash commands)",
                value=(
                    "• 🖥️ **Desktop:** Right-click user profile ➔ `Apps` (or use `/pkfronters` / `/pkprofile`).\n"
                    "• 📱 **Mobile:** Tap user profile ➔ scroll to `Apps` (or use `/pkfronters` / `/pkprofile`)."
                ),
                inline=False,
            )
            embed.set_footer(text="All lookups are 100% private and ephemeral by default.")
            await interaction.response.send_message(embed=embed, view=view, ephemeral=not public)
        else:
            fallback = (
                "**🔍 PKLens**\n"
                "A privacy-focused tool to view public PluralKit profiles silently.\n\n"
                "**🌐 Server vs. User App:**\n"
                "• **User App:** Installs to your personal account to use **Discord-wide** across DMs and external servers.\n"
                "• **Server:** Adds the bot to a server for all members to use.\n\n"
                "**How to use:**\n"
                "• 🖥️ Desktop: Right-click user profile ➔ `Apps` (or `/pkfronters` / `/pkprofile`).\n"
                "• 📱 Mobile: Tap user profile ➔ `Apps` (or `/pkfronters` / `/pkprofile`).\n\n"
                f"**Add PKLens:** <{oauth_url}>\n"
                "*(All lookups are private and ephemeral by default)*"
            )
            await interaction.response.send_message(fallback, view=view, ephemeral=not public)

    @app_commands.command(
        name="pkfronters",
        description="Silently check who is currently fronting in a PluralKit system.",
        extras={"red_force_enable": True},
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pkfronters_slash(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_fronters(interaction, user)

    @app_commands.command(
        name="pkprofile",
        description="Silently view a user's public PluralKit system profile.",
        extras={"red_force_enable": True},
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pkprofile_slash(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_profile(interaction, user)

    async def check_fronter_callback(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_fronters(interaction, user)

    async def view_profile_callback(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_profile(interaction, user)
