# Copyright (c) 2020-2026 PhenoM4n4n
# Copyright (c) 2023-2026 AAA3A
# Copyright (c) 2026 SinOfStrife and Contributors
# Licensed under MIT

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.strifecogs.linkquoter")

LINK_REGEX = re.compile(
    r"(?<!\]\()https?:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/channels\/"
    r"(?P<guild_id>@me|[0-9]{15,21})\/(?P<channel_id>[0-9]{15,21})\/(?P<message_id>[0-9]{15,21})\/?",
    re.IGNORECASE,
)


def _clip(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…" if limit > 1 else text[:limit]


def _is_nsfw(channel: Union[discord.abc.GuildChannel, discord.Thread]) -> bool:
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        return bool(parent and getattr(parent, "is_nsfw", lambda: False)())
    if hasattr(channel, "is_nsfw") and callable(channel.is_nsfw):
        return channel.is_nsfw()
    return bool(getattr(channel, "nsfw", False))


class QuoteDeleteView(discord.ui.View):
    def __init__(self, invoker_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.invoker_id = invoker_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.invoker_id is not None and interaction.user.id == self.invoker_id:
            return True

        channel = interaction.channel or getattr(interaction.user, "channel", None)
        is_mod = False
        if channel and hasattr(channel, "permissions_for"):
            is_mod = channel.permissions_for(interaction.user).manage_messages
        else:
            is_mod = getattr(interaction.user.guild_permissions, "manage_messages", False)

        is_owner = await interaction.client.is_owner(interaction.user)

        if not (is_mod or is_owner):
            await interaction.response.send_message(
                "❌ Only the person who requested this quote or a moderator with Manage Messages can delete it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(emoji="🗑️", style=discord.ButtonStyle.secondary, custom_id="strifecogs:linkquoter:delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        target_msg = self.message or interaction.message
        if target_msg:
            try:
                await target_msg.delete()
            except discord.HTTPException:
                pass


class LinkQuoter(commands.Cog):
    """Quotes Discord message links with rich previews, thread support, and flexible cross-server security."""

    __author__ = ["SinOfStrife"]
    __version__ = "3.1.7"
    __red_end_user_data_statement__ = "This cog does not persistently store personal data about users."

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=839217482910, force_registration=True)

        default_guild = {
            "enabled": False,
            "delete_original": False,
            "cross_guild_mode": "off",
            "cross_guild_whitelist": [],
            "ignored_channels": [],
            "ignored_roles": [],
            "allowed_roles": [],
        }
        self.config.register_guild(**default_guild)

        self._cache: Dict[int, Dict[str, Any]] = {}
        self._cooldown = commands.CooldownMapping.from_cooldown(3, 10, commands.BucketType.channel)
        self.persistent_view_added = False

    def _get_cache(self, guild_id: int) -> Dict[str, Any]:
        return self._cache.setdefault(guild_id, {
            "enabled": False,
            "delete_original": False,
            "cross_guild_mode": "off",
            "cross_guild_whitelist": set(),
            "ignored_channels": set(),
            "ignored_roles": set(),
            "allowed_roles": set(),
        })

    async def cog_load(self) -> None:
        await self._build_cache()
        if not self.persistent_view_added:
            self.bot.add_view(QuoteDeleteView())
            self.persistent_view_added = True

    async def _build_cache(self) -> None:
        guilds_data = await self.config.all_guilds()
        for guild_id, data in guilds_data.items():
            self._cache[guild_id] = {
                "enabled": data.get("enabled", False),
                "delete_original": data.get("delete_original", False),
                "cross_guild_mode": data.get("cross_guild_mode", "off"),
                "cross_guild_whitelist": set(data.get("cross_guild_whitelist", [])),
                "ignored_channels": set(data.get("ignored_channels", [])),
                "ignored_roles": set(data.get("ignored_roles", [])),
                "allowed_roles": set(data.get("allowed_roles", [])),
            }

    async def red_get_data_for_user(self, *, user_id: int) -> dict:
        return {}

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        return

    async def _send_error(self, ctx: commands.Context, title: str, description: str) -> None:
        can_embed = ctx.channel.permissions_for(ctx.me).embed_links
        if can_embed:
            embed = discord.Embed(title=f"🛑 {title}", description=description, color=discord.Color.red())
            await ctx.send(embed=embed, delete_after=12)
        else:
            await ctx.send(f"**🛑 {title}**\n{description}", delete_after=12)

    async def _fetch_and_validate(
        self,
        requester: discord.Member,
        dest_channel: Union[discord.abc.GuildChannel, discord.Thread],
        guild_id_str: str,
        channel_id: int,
        message_id: int,
        *,
        is_manual: bool = False,
    ) -> Tuple[Optional[discord.Message], Optional[str]]:
        if guild_id_str.lower() == "@me":
            return None, "I can only quote messages from servers, not direct messages."

        guild_id = int(guild_id_str)
        target_guild = self.bot.get_guild(guild_id)
        if not target_guild:
            return None, "That message link belongs to a server I am not in."

        if target_guild.id != dest_channel.guild.id:
            dest_cache = self._get_cache(dest_channel.guild.id)
            source_cache = self._get_cache(target_guild.id)

            dest_mode = dest_cache.get("cross_guild_mode", "off")
            source_mode = source_cache.get("cross_guild_mode", "off")

            if dest_mode == "off" or source_mode == "off":
                return None, "Cross-server quoting is disabled for one or both servers."

            if dest_mode == "whitelist":
                if target_guild.id not in dest_cache.get("cross_guild_whitelist", set()):
                    return None, "This server has not whitelisted the source server."
            
            if source_mode == "whitelist":
                if dest_channel.guild.id not in source_cache.get("cross_guild_whitelist", set()):
                    return None, "The source server requires explicit whitelist approval to quote its messages."

        target_channel = target_guild.get_channel_or_thread(channel_id)
        if not target_channel:
            try:
                target_channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None, "I could not find the channel or thread that message was sent in."

        if getattr(target_channel, "guild", None) is None or target_channel.guild.id != target_guild.id:
            return None, "The channel in that link does not belong to the specified server."

        if _is_nsfw(target_channel) and not _is_nsfw(dest_channel):
            return None, "Messages from NSFW channels cannot be quoted in non-NSFW channels."

        bot_perms = target_channel.permissions_for(target_guild.me)
        if not (bot_perms.read_messages and bot_perms.read_message_history):
            return None, "I do not have permission to read messages in that channel."

        if target_guild.id == dest_channel.guild.id:
            author_member = requester
            author_perms = target_channel.permissions_for(author_member)
            if not (author_perms.read_messages and author_perms.read_message_history):
                return None, "You do not have permission to view the channel that message was sent in."

        message = discord.utils.get(self.bot.cached_messages, id=message_id)
        if not message:
            try:
                message = await target_channel.fetch_message(message_id)
            except discord.NotFound:
                return None, "I couldn't find that message. It may have been deleted."
            except discord.Forbidden:
                return None, "I do not have access to read that message."
            except discord.HTTPException:
                return None, "Discord encountered an error while retrieving that message."

        return message, None

    def _build_quote_embed(
        self,
        message: discord.Message,
        commentary: Optional[str] = None,
        commenter: Optional[discord.Member | discord.User] = None,
        color: Optional[discord.Color] = None,
    ) -> discord.Embed:
        embed_color = color or (message.author.color if getattr(message.author.color, "value", 0) != 0 else discord.Color.blurple())
        embed = discord.Embed(color=embed_color, timestamp=message.created_at)

        channel = message.channel
        if isinstance(channel, discord.Thread):
            parent_name = channel.parent.name if channel.parent else "Unknown"
            footer_text = f"#{parent_name} ➔ 🧵 {channel.name}"
        else:
            footer_text = f"#{channel.name}"

        if message.guild:
            embed.set_footer(text=f"{footer_text} • {message.guild.name}", icon_url=message.guild.icon.url if message.guild.icon else None)
        else:
            embed.set_footer(text=footer_text)

        embed.set_author(name=f"{message.author.display_name} said...", icon_url=message.author.display_avatar.url, url=message.jump_url)

        description_parts = []
        if commentary:
            name_tag = f"**{commenter.display_name}:** " if commenter else ""
            description_parts.append(f"💬 {name_tag}*\"{_clip(commentary, 400)}\"*")

        if hasattr(message, "poll") and message.poll:
            poll_question = getattr(getattr(message.poll, "question", None), "text", None)
            if poll_question:
                description_parts.append(f"📊 **Poll:** *\"{_clip(poll_question, 300)}\"*")

        body_text = message.content or getattr(message, "system_content", "")
        if body_text:
            description_parts.append(f">>> {_clip(body_text, 3500)}")

        embed.description = "\n\n".join(description_parts) if description_parts else "*[Click link below to view contents]*"

        image_url = None
        if message.attachments:
            for att in message.attachments:
                if att.filename.lower().endswith(("png", "jpg", "jpeg", "gif", "webp")) and not att.is_spoiler():
                    image_url = att.proxy_url or att.url
                    break

            shown_links = []
            curr_len = 0
            for a in message.attachments:
                tag = f"|| [{a.filename}]({a.url}) ||" if a.is_spoiler() else f"[{a.filename}]({a.url})"
                if curr_len + len(tag) + 2 > 950:
                    break
                shown_links.append(tag)
                curr_len += len(tag) + 2

            rem = len(message.attachments) - len(shown_links)
            val_text = ", ".join(shown_links) + (f" ... and {rem} more" if rem > 0 else "")
            embed.add_field(name="Attachments", value=val_text, inline=False)

        if not image_url and message.stickers:
            for sticker in message.stickers:
                if sticker.url:
                    image_url = sticker.url
                    embed.add_field(name="Sticker", value=f"[{sticker.name}]({sticker.url})", inline=False)
                    break

        if not image_url and message.embeds:
            for e in message.embeds:
                if e.image and e.image.url:
                    image_url = e.image.url
                    break
                elif e.thumbnail and e.thumbnail.url:
                    image_url = e.thumbnail.url
                    break

        if image_url:
            embed.set_image(url=image_url)

        embed.add_field(name="Source", value=f"[Jump to Original Message]({message.jump_url})", inline=False)
        return embed

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot or message.webhook_id:
            return

        guild_cache = self._get_cache(message.guild.id)
        if not guild_cache.get("enabled", False):
            return

        allowed_roles = guild_cache.get("allowed_roles", set())
        if allowed_roles and not any(role.id in allowed_roles for role in getattr(message.author, "roles", [])):
            return

        channel_id_to_check = getattr(message.channel, "parent_id", message.channel.id) or message.channel.id
        if channel_id_to_check in guild_cache.get("ignored_channels", set()) or message.channel.id in guild_cache.get("ignored_channels", set()):
            return

        if any(role.id in guild_cache.get("ignored_roles", set()) for role in getattr(message.author, "roles", [])):
            return

        if "no quote" in message.content.lower():
            return

        dest_perms = message.channel.permissions_for(message.guild.me)
        if not (dest_perms.send_messages and dest_perms.embed_links):
            return

        target_match = None
        for match in LINK_REGEX.finditer(message.content):
            start, end = match.span()
            if start > 0 and end < len(message.content) and message.content[start - 1] == "<" and message.content[end] == ">":
                continue
            target_match = match
            break

        if not target_match:
            return

        guild_id_str = target_match.group("guild_id")
        channel_id = int(target_match.group("channel_id"))
        message_id = int(target_match.group("message_id"))

        target_message, _ = await self._fetch_and_validate(
            message.author, message.channel, guild_id_str, channel_id, message_id, is_manual=False
        )
        if not target_message:
            return

        bucket = self._cooldown.get_bucket(message)
        if bucket and bucket.update_rate_limit():
            return

        raw_commentary = (message.content[:target_match.start()] + message.content[target_match.end():]).strip()
        commentary = raw_commentary if raw_commentary else None

        embed = self._build_quote_embed(target_message, commentary=commentary, commenter=message.author)
        view = QuoteDeleteView(invoker_id=message.author.id)

        try:
            view.message = await message.channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return

        if guild_cache.get("delete_original", False) and dest_perms.manage_messages:
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

    @commands.guild_only()
    @commands.cooldown(3, 10, commands.BucketType.channel)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    @commands.command(name="linkquote", aliases=["lq", "quote"])
    async def linkquote(self, ctx: commands.Context, *, link_and_commentary: Optional[str] = None) -> None:
        """Quote a Discord message link or replied message.

        You can either provide a direct message link, or reply to a message and run this command. You can also add your own commentary text alongside the link.

        **Arguments:**
        • `[link_and_commentary]`: A Discord message link, optionally followed by your own commentary text. If omitted, you must reply to a message.

        **Examples:**
        • `[p]linkquote https://discord.com/channels/...`
        • `[p]linkquote https://discord.com/channels/... check this out!`
        • Reply to any message with: `[p]linkquote`
        """
        guild_cache = self._get_cache(ctx.guild.id)
        allowed_roles = guild_cache.get("allowed_roles", set())
        if allowed_roles and not any(role.id in allowed_roles for role in getattr(ctx.author, "roles", [])):
            if not await ctx.bot.is_owner(ctx.author):
                await self._send_error(ctx, "Permission Denied", "You do not have the required role to use LinkQuoter.")
                return

        target_text = link_and_commentary or ""
        match = LINK_REGEX.search(target_text)
        commentary = None

        if not match:
            if ctx.message.reference:
                if isinstance(ctx.message.reference.resolved, discord.Message):
                    target_link = ctx.message.reference.resolved.jump_url
                    match = LINK_REGEX.search(target_link)
                elif ctx.message.reference.message_id:
                    ref = ctx.message.reference
                    target_link = f"https://discord.com/channels/{ctx.guild.id}/{ref.channel_id}/{ref.message_id}"
                    match = LINK_REGEX.search(target_link)

            if match and target_text:
                commentary = target_text.strip()
        else:
            raw_commentary = (target_text[:match.start()] + target_text[match.end():]).strip()
            commentary = raw_commentary if raw_commentary else None

        if not match:
            await self._send_error(ctx, "Missing Link", "Please provide a valid message link or reply to a message.")
            return

        guild_id_str = match.group("guild_id")
        channel_id = int(match.group("channel_id"))
        message_id = int(match.group("message_id"))

        target_message, error_msg = await self._fetch_and_validate(
            ctx.author, ctx.channel, guild_id_str, channel_id, message_id, is_manual=True
        )
        if not target_message:
            await self._send_error(ctx, "Quote Failed", error_msg or "Could not retrieve that message.")
            return

        embed = self._build_quote_embed(target_message, commentary=commentary, commenter=ctx.author)
        view = QuoteDeleteView(invoker_id=message.author.id)
        view.message = await ctx.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True)
    @commands.group(name="linkquoteset", aliases=("lqset",))
    async def linkquoteset(self, ctx: commands.Context) -> None:
        """Manage LinkQuoter settings for this server."""
        pass

    @linkquoteset.command(name="toggle")
    async def linkquoteset_toggle(self, ctx: commands.Context) -> None:
        """Toggle automatic message link quoting in this server."""
        guild_cache = self._get_cache(ctx.guild.id)
        current = guild_cache.get("enabled", False)
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        guild_cache["enabled"] = new_state
        await ctx.send(f"Automatic link quoting is now **{'enabled' if new_state else 'disabled'}**.")

    @linkquoteset.command(name="delete")
    async def linkquoteset_delete(self, ctx: commands.Context) -> None:
        """Toggle deleting the user's original message after quoting."""
        guild_cache = self._get_cache(ctx.guild.id)
        current = guild_cache.get("delete_original", False)
        new_state = not current
        await self.config.guild(ctx.guild).delete_original.set(new_state)
        guild_cache["delete_original"] = new_state
        await ctx.send(f"Deleting the original message after quoting is now **{'enabled' if new_state else 'disabled'}**.")

    @linkquoteset.command(name="mode")
    async def linkquoteset_mode(self, ctx: commands.Context, mode: Literal["off", "whitelist", "public"]) -> None:
        """Set cross-server quoting mode: off, whitelist, or public."""
        guild_cache = self._get_cache(ctx.guild.id)
        await self.config.guild(ctx.guild).cross_guild_mode.set(mode)
        guild_cache["cross_guild_mode"] = mode
        
        if mode == "public":
            await ctx.send(
                "⚠️ **Cross-server mode set to PUBLIC.**\n"
                "Any other public server can now quote messages from this guild. "
                "Ensure private channels are hidden from the bot or ignored using `[p]linkquoteset ignorechannel`."
            )
        else:
            await ctx.send(f"Cross-server quoting mode has been set to **{mode}**.")

    @linkquoteset.command(name="whitelist")
    async def linkquoteset_whitelist(self, ctx: commands.Context, server_id: int) -> None:
        """Add or remove a companion server ID for mutual cross-server quoting."""
        if server_id == ctx.guild.id:
            await ctx.send("❌ This server is already permitted to quote its own messages by default.")
            return

        guild_cache = self._get_cache(ctx.guild.id)
        allowed: Set[int] = guild_cache.setdefault("cross_guild_whitelist", set())

        if server_id in allowed:
            allowed.discard(server_id)
            async with self.config.guild(ctx.guild).cross_guild_whitelist() as w:
                if server_id in w:
                    w.remove(server_id)
            await ctx.send(f"Removed server ID `{server_id}` from whitelist.")
        else:
            allowed.add(server_id)
            async with self.config.guild(ctx.guild).cross_guild_whitelist() as w:
                if server_id not in w:
                    w.append(server_id)
            await ctx.send(f"Added server ID `{server_id}` to whitelist.")

    @linkquoteset.command(name="ignorechannel")
    async def linkquoteset_ignorechannel(
        self, ctx: commands.Context, channel: Union[discord.abc.GuildChannel, discord.Thread]
    ) -> None:
        """Ignore or un-ignore a channel/thread from automatic link quoting."""
        guild_cache = self._get_cache(ctx.guild.id)
        ignored = guild_cache.setdefault("ignored_channels", set())

        if channel.id in ignored:
            ignored.discard(channel.id)
            async with self.config.guild(ctx.guild).ignored_channels() as chs:
                if channel.id in chs:
                    chs.remove(channel.id)
            await ctx.send(f"Auto-quoting will now process links in {channel.mention}.")
        else:
            ignored.add(channel.id)
            async with self.config.guild(ctx.guild).ignored_channels() as chs:
                if channel.id not in chs:
                    chs.append(channel.id)
            await ctx.send(f"Auto-quoting will now ignore links in {channel.mention} (and any threads inside it).")

    @linkquoteset.command(name="ignorerole")
    async def linkquoteset_ignorerole(self, ctx: commands.Context, role: discord.Role) -> None:
        """Ignore or un-ignore a role from automatic link quoting."""
        guild_cache = self._get_cache(ctx.guild.id)
        ignored = guild_cache.setdefault("ignored_roles", set())

        if role.id in ignored:
            ignored.discard(role.id)
            async with self.config.guild(ctx.guild).ignored_roles() as rls:
                if role.id in rls:
                    rls.remove(role.id)
            await ctx.send(f"Auto-quoting will now process links from roles named **{role.name}**.")
        else:
            ignored.add(role.id)
            async with self.config.guild(ctx.guild).ignored_roles() as rls:
                if role.id not in rls:
                    rls.append(role.id)
            await ctx.send(f"Auto-quoting will now ignore links from roles named **{role.name}**.")

    @linkquoteset.command(name="allowrole")
    async def linkquoteset_allowrole(self, ctx: commands.Context, role: discord.Role) -> None:
        """Allow or un-allow a role to use LinkQuoter (restricts quoting to these roles)."""
        guild_cache = self._get_cache(ctx.guild.id)
        allowed = guild_cache.setdefault("allowed_roles", set())

        if role.id in allowed:
            allowed.discard(role.id)
            async with self.config.guild(ctx.guild).allowed_roles() as rls:
                if role.id in rls:
                    rls.remove(role.id)
            await ctx.send(f"Removed **{role.name}** from the allowed roles list.")
        else:
            allowed.add(role.id)
            async with self.config.guild(ctx.guild).allowed_roles() as rls:
                if role.id not in rls:
                    rls.append(role.id)
            await ctx.send(
                f"Added **{role.name}** to the allowed roles list.\n"
                f"*(Only members with allowed roles can quote links when this list is not empty).*"
            )

    @linkquoteset.command(name="settings", aliases=["showsettings"])
    async def linkquoteset_settings(self, ctx: commands.Context) -> None:
        """Display active LinkQuoter settings for this server."""
        data = self._get_cache(ctx.guild.id)
        enabled = data.get("enabled", False)
        delete_orig = data.get("delete_original", False)
        mode = data.get("cross_guild_mode", "off")
        whitelisted = data.get("cross_guild_whitelist", set())
        ignored_chs = data.get("ignored_channels", set())
        ignored_roles = data.get("ignored_roles", set())
        allowed_roles = data.get("allowed_roles", set())

        ch_text = ", ".join(f"<#{cid}>" for cid in ignored_chs) if ignored_chs else "None"
        ignored_role_text = ", ".join(f"<@&{rid}>" for rid in ignored_roles) if ignored_roles else "None"
        allowed_role_text = ", ".join(f"<@&{rid}>" for rid in allowed_roles) if allowed_roles else "All Roles (Open)"
        wl_text = ", ".join(f"`{gid}`" for gid in whitelisted) if whitelisted else "None"

        can_embed = ctx.channel.permissions_for(ctx.me).embed_links
        if can_embed:
            embed = discord.Embed(title=f"LinkQuoter Settings — {ctx.guild.name}", color=await ctx.embed_color())
            embed.add_field(name="Auto-Quoting", value="Enabled" if enabled else "Disabled", inline=True)
            embed.add_field(name="Delete Original Link", value="Enabled" if delete_orig else "Disabled", inline=True)
            embed.add_field(name="Cross-Server Mode", value=mode.capitalize(), inline=True)
            embed.add_field(name="Server Whitelist", value=_clip(wl_text, 1024), inline=False)
            embed.add_field(name="Allowed Roles", value=_clip(allowed_role_text, 1024), inline=False)
            embed.add_field(name="Ignored Channels", value=_clip(ch_text, 1024), inline=False)
            embed.add_field(name="Ignored Roles", value=_clip(ignored_role_text, 1024), inline=False)
            await ctx.send(embed=embed)
        else:
            msg = (
                f"**LinkQuoter Settings — {ctx.guild.name}**\n"
                f"• Auto-Quoting: {'Enabled' if enabled else 'Disabled'}\n"
                f"• Delete Original Link: {'Enabled' if delete_orig else 'Disabled'}\n"
                f"• Cross-Server Mode: {mode.capitalize()}\n"
                f"• Server Whitelist: {_clip(wl_text, 250)}\n"
                f"• Allowed Roles: {_clip(allowed_role_text, 250)}\n"
                f"• Ignored Channels: {_clip(ch_text, 250)}\n"
                f"• Ignored Roles: {_clip(ignored_role_text, 250)}"
            )
            await ctx.send(_clip(msg, 1900))
