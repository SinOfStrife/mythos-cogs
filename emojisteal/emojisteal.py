import asyncio
import io
import logging
import re
import zipfile
from itertools import zip_longest
from typing import List, Optional, Sequence, Union

import aiohttp
import discord
from redbot.core import app_commands, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

try:
    from redbot.core.app_commands import AppCommandContext, AppInstallationType
except ImportError:
    from discord.app_commands import AppCommandContext, AppInstallationType

log = logging.getLogger("red.emojisteal")

EMOJI_KB = 256
STICKER_KB = 512
STICKER_DIM = 320
STICKER_TIME = 5
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

MISSING_EMOJIS = "Can't find emojis or stickers in that message."
MISSING_REFERENCE = "Reply to a message using this command to steal an emote."
MESSAGE_FAIL = "I can't find that message, sorry."
MESSAGE_FORBIDDEN = "❌ I don't have permission to read message history in this channel."
GUILD_ONLY = "This command can only be used in a server."
UPLOADED_BY = "Uploaded by"
STICKER_DESC = "Stolen sticker"
STICKER_EMOJI = "😶"
STICKER_FAIL = "❌ Failed to upload sticker"
STICKER_LOTTIE = "❌ Lottie stickers cannot be uploaded to servers."
STICKER_NAME_TOO_SHORT = "Sticker names must be at least 2 characters long."
STICKER_SUCCESS = "✅ Uploaded sticker"
STICKER_SLOTS = "⚠️ This server doesn't have any more space for stickers!"
ZIP_INVALID = "❌ Invalid, corrupted, or password-protected ZIP file."
EMOJI_FAIL = "❌ Failed to upload"
EMOJI_TOO_BIG = f"Image exceeds Discord's {EMOJI_KB} KB emoji limit."
EMOJI_SLOTS = "⚠️ This server doesn't have any more space for emojis!"
INVALID_EMOJI = "Invalid emoji or emoji ID."
STICKER_TOO_BIG = (
    f"Stickers may only be up to {STICKER_KB} KB and {STICKER_DIM}x{STICKER_DIM} "
    f"pixels and last up to {STICKER_TIME} seconds."
)
STICKER_ATTACHMENT = (
    ">>> For a non-moving sticker, simply use this command and attach a PNG image.\n"
    "For a moving sticker, Discord limitations make it very annoying. Follow these steps:\n"
    "1. Scale down and optimize your video/gif in <https://ezgif.com>\n"
    "2. Convert it to APNG in that same website.\n"
    "3. Download it and put it inside a zip file.\n"
    "4. Use this command and attach that zip file.\n"
    "This prevents Discord from replacing your animated PNG with a static PNG.\n\n"
    "**Important:** " + STICKER_TOO_BIG
)


class ServerUploadSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "EmojiSteal",
        guilds: Sequence[discord.Guild],
        items: Sequence[Union[discord.StickerItem, discord.PartialEmoji]],
        owner_id: int,
    ) -> None:
        self.cog = cog
        self.items = items
        self.owner_id = owner_id
        options = [
            discord.SelectOption(
                label=guild.name[:100],
                value=str(guild.id),
                description=f"ID: {guild.id}"[:100],
            )
            for guild in guilds[:25]
        ]
        super().__init__(
            placeholder="Upload to one of your servers...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ This menu does not belong to you.", ephemeral=True
            )
            return

        guild = self.cog.bot.get_guild(int(self.values[0]))
        if guild is None or guild.unavailable:
            await interaction.response.send_message(
                "❌ That server is currently unavailable or I am no longer in it.",
                ephemeral=True,
            )
            return

        if not guild.me.guild_permissions.manage_emojis:
            await interaction.response.send_message(
                f"❌ I need the **Manage Expressions** permission in **{guild.name}** to do that.",
                ephemeral=True,
            )
            return

        member = guild.get_member(interaction.user.id)
        has_perm = (
            member.guild_permissions.manage_emojis
            if member is not None
            else guild.owner_id == interaction.user.id
        )
        if not has_perm:
            await interaction.response.send_message(
                f"❌ You need the **Manage Expressions** permission in **{guild.name}** to do that.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.upload_items_to_guild(guild, self.items)
        await self.cog._send_interaction_paginated(interaction, result, ephemeral=True)


class ServerUploadView(discord.ui.View):
    def __init__(
        self,
        cog: "EmojiSteal",
        guilds: Sequence[discord.Guild],
        items: Sequence[Union[discord.StickerItem, discord.PartialEmoji]],
        owner_id: int,
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(ServerUploadSelect(cog, guilds, items, owner_id))

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        log.exception("Unhandled error in ServerUploadView", exc_info=error)
        await EmojiSteal._safe_send_interaction(
            interaction,
            "❌ Something went wrong while uploading to that server. Please try again.",
            ephemeral=True,
        )


class EmojiSteal(commands.Cog):
    """Steals emojis and stickers sent by other people and optionally uploads them to your own server."""

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = bot
        self.steal_context_menu = app_commands.ContextMenu(
            name="Steal Emotes",
            callback=self.steal_app_command,
            allowed_contexts=AppCommandContext(
                guild=True,
                dm_channel=True,
                private_channel=True,
            ),
            allowed_installs=AppInstallationType(
                guild=True,
                user=True,
            ),
        )
        self.steal_upload_context_menu = app_commands.ContextMenu(
            name="Steal+Upload Emotes",
            callback=self.steal_upload_app_command,
            allowed_contexts=AppCommandContext(guild=True),
            allowed_installs=AppInstallationType(guild=True),
        )
        self.steal_context_menu.error(self.on_context_menu_error)
        self.steal_upload_context_menu.error(self.on_context_menu_error)

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.steal_context_menu)
        self.bot.tree.add_command(self.steal_upload_context_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(
            self.steal_context_menu.name,
            type=self.steal_context_menu.type,
        )
        self.bot.tree.remove_command(
            self.steal_upload_context_menu.name,
            type=self.steal_upload_context_menu.type,
        )

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        return

    @staticmethod
    def format_permissions(perms: Sequence[str]) -> str:
        friendly: List[str] = []
        for perm in perms:
            if perm in ("manage_emojis", "manage_emojis_and_stickers", "manage_expressions"):
                label = "Manage Expressions"
            else:
                label = perm.replace("_", " ").title()
            formatted = f"**{label}**"
            if formatted not in friendly:
                friendly.append(formatted)
        return ", ".join(friendly) or "**Required Permissions**"

    @staticmethod
    def _format_upload_error(error: Exception, *, is_sticker: bool = False) -> str:
        if isinstance(error, asyncio.TimeoutError):
            return "Download timed out while fetching from Discord."
        if isinstance(error, discord.Forbidden):
            return "Missing permissions (ensure I have **Manage Expressions**)."
        if isinstance(error, discord.HTTPException):
            text = str(error).lower()
            if "exceed" in text or "512" in text or "320" in text:
                return STICKER_TOO_BIG if is_sticker else EMOJI_TOO_BIG
            if "256" in text and not is_sticker:
                return EMOJI_TOO_BIG
            if "maximum number" in text:
                return STICKER_SLOTS if is_sticker else EMOJI_SLOTS
        return f"{type(error).__name__}: {error}"

    @staticmethod
    async def _safe_send_ctx(ctx: commands.Context, message: str) -> None:
        try:
            await ctx.send(message)
        except discord.Forbidden:
            try:
                await ctx.author.send(
                    f"{message}\n*(I also lack permission to send messages in {ctx.channel.mention}.)*"
                )
            except discord.HTTPException:
                pass
        except discord.HTTPException:
            pass

    @staticmethod
    async def _safe_send_interaction(
        interaction: discord.Interaction, message: str, *, ephemeral: bool = True
    ) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(message, ephemeral=ephemeral)
        except discord.HTTPException:
            pass

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingPermissions):
            perms = self.format_permissions(error.missing_permissions)
            await self._safe_send_ctx(
                ctx, f"❌ You need the {perms} permission to use this command."
            )
        elif isinstance(error, commands.BotMissingPermissions):
            perms = self.format_permissions(error.missing_permissions)
            await self._safe_send_ctx(
                ctx, f"❌ I need the {perms} permission to do that."
            )
        elif isinstance(error, commands.NoPrivateMessage):
            await self._safe_send_ctx(ctx, GUILD_ONLY)
        else:
            await self.bot.on_command_error(ctx, error, unhandled_by_cog=True)

    async def on_context_menu_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            perms = self.format_permissions(error.missing_permissions)
            msg = f"❌ You need the {perms} permission to use this command."
        elif isinstance(error, app_commands.BotMissingPermissions):
            perms = self.format_permissions(error.missing_permissions)
            msg = f"❌ I need the {perms} permission to do that."
        elif isinstance(error, app_commands.NoPrivateMessage):
            msg = GUILD_ONLY
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Please wait {error.retry_after:.1f}s before using this command again."
        elif type(error).__name__ == "UserFeedbackCheckFailure" and str(error):
            msg = f"❌ {error}"
        elif isinstance(error, app_commands.CheckFailure):
            msg = "❌ You don't have permission to use this command here."
        else:
            log.exception("Unhandled error in EmojiSteal context menu command", exc_info=error)
            msg = "❌ An unexpected error occurred while processing that message."

        await self._safe_send_interaction(interaction, msg, ephemeral=True)

    @staticmethod
    def get_emojis(content: str) -> List[discord.PartialEmoji]:
        results = re.findall(r"(<(a?):(\w+):(\d{10,20})>)", content)
        return [discord.PartialEmoji.from_str(result[0]) for result in results]

    @staticmethod
    def sanitize_emoji_name(name: str, fallback: str = "stolen_emoji") -> str:
        cleaned = re.sub(r"\W+", "", name)[:32]
        return cleaned if len(cleaned) >= 2 else fallback

    def extract_emotes(
        self, message: discord.Message
    ) -> Union[List[discord.StickerItem], List[discord.PartialEmoji]]:
        stickers: List[discord.StickerItem] = list(message.stickers)
        for snapshot in getattr(message, "message_snapshots", ()):
            if snapshot.stickers:
                stickers.extend(snapshot.stickers)
        if stickers:
            return stickers

        emojis: List[discord.PartialEmoji] = self.get_emojis(message.content)
        for snapshot in getattr(message, "message_snapshots", ()):
            if snapshot.content:
                emojis.extend(self.get_emojis(snapshot.content))
        return emojis

    @staticmethod
    def available_emoji_slots(guild: discord.Guild, animated: bool) -> int:
        current_emojis = sum(1 for em in guild.emojis if em.animated == animated)
        return guild.emoji_limit - current_emojis

    @staticmethod
    def sticker_filename(sticker: discord.StickerItem) -> str:
        ext = "gif" if sticker.format == discord.StickerFormatType.gif else "png"
        return f"sticker.{ext}"

    def get_manageable_guilds(self, user_id: int) -> List[discord.Guild]:
        guilds: List[discord.Guild] = []
        for guild in self.bot.guilds:
            if guild.unavailable or not guild.me.guild_permissions.manage_emojis:
                continue
            member = guild.get_member(user_id)
            has_perm = (
                member.guild_permissions.manage_emojis
                if member is not None
                else guild.owner_id == user_id
            )
            if has_perm:
                guilds.append(guild)
        return guilds[:25]

    @staticmethod
    async def _send_interaction_paginated(
        interaction: discord.Interaction, text: str, *, ephemeral: bool = False
    ) -> None:
        pages = list(pagify(text, delims=["\n", " "])) or ["Done."]
        try:
            await interaction.edit_original_response(content=pages[0])
            for page in pages[1:]:
                await interaction.followup.send(content=page, ephemeral=ephemeral)
        except discord.HTTPException:
            pass

    async def upload_items_to_guild(
        self,
        guild: discord.Guild,
        items: Sequence[Union[discord.StickerItem, discord.PartialEmoji]],
    ) -> str:
        if not guild.me.guild_permissions.manage_emojis:
            return "❌ I need the **Manage Expressions** permission in that server."

        if isinstance(items[0], discord.StickerItem):
            results: List[str] = []
            for item in items:
                if not isinstance(item, discord.StickerItem):
                    continue
                if len(guild.stickers) >= guild.sticker_limit:
                    results.append(STICKER_SLOTS)
                    break
                if item.format == discord.StickerFormatType.lottie:
                    results.append(STICKER_LOTTIE)
                    continue

                sticker_name = re.sub(r"\s+", " ", item.name).strip()[:30]
                if len(sticker_name) < 2:
                    sticker_name = "stolen_sticker"

                fp = io.BytesIO()
                try:
                    await item.save(fp)
                    fp.seek(0)
                    created = await guild.create_sticker(
                        name=sticker_name,
                        description=STICKER_DESC,
                        emoji=STICKER_EMOJI,
                        file=discord.File(fp, filename=self.sticker_filename(item)),
                    )
                    results.append(f"{STICKER_SUCCESS}: {created.name}")
                except (asyncio.TimeoutError, aiohttp.ClientError, discord.DiscordException) as error:
                    detail = self._format_upload_error(error, is_sticker=True)
                    results.append(f"{STICKER_FAIL}, {detail}")
                    break

            return "\n".join(results) or STICKER_FAIL

        added_emojis: List[discord.Emoji] = []
        emojis: List[discord.PartialEmoji] = list(dict.fromkeys(items))  # type: ignore
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            for emoji in emojis:
                if self.available_emoji_slots(guild, emoji.animated) <= 0:
                    response = EMOJI_SLOTS
                    if added_emojis:
                        response = " ".join(str(e) for e in added_emojis) + "\n" + response
                    return response

                try:
                    async with session.get(emoji.url) as resp:
                        resp.raise_for_status()
                        image = await resp.read()
                    if len(image) > EMOJI_KB * 1024:
                        response = f"{EMOJI_FAIL} {emoji.name}: {EMOJI_TOO_BIG}"
                        if added_emojis:
                            response = " ".join(str(e) for e in added_emojis) + "\n" + response
                        return response
                    added = await guild.create_custom_emoji(
                        name=self.sanitize_emoji_name(emoji.name),
                        image=image,
                    )
                except (asyncio.TimeoutError, aiohttp.ClientError, discord.DiscordException) as error:
                    detail = self._format_upload_error(error, is_sticker=False)
                    response = f"{EMOJI_FAIL} {emoji.name}, {detail}"
                    if added_emojis:
                        response = " ".join(str(e) for e in added_emojis) + "\n" + response
                    return response

                added_emojis.append(added)

        return " ".join(str(e) for e in added_emojis)

    async def steal_ctx(
        self, ctx: commands.Context
    ) -> Optional[Union[List[discord.PartialEmoji], List[discord.StickerItem]]]:
        reference = ctx.message.reference
        if not reference or not reference.message_id:
            await ctx.send(MISSING_REFERENCE)
            return None

        if isinstance(reference.resolved, discord.DeletedReferencedMessage):
            await ctx.send(MESSAGE_FAIL)
            return None

        if isinstance(reference.resolved, discord.Message):
            message = reference.resolved
        else:
            try:
                message = await ctx.channel.fetch_message(reference.message_id)
            except discord.Forbidden:
                await ctx.send(MESSAGE_FORBIDDEN)
                return None
            except (discord.NotFound, discord.HTTPException):
                await ctx.send(MESSAGE_FAIL)
                return None

        if not (items := self.extract_emotes(message)):
            await ctx.send(MISSING_EMOJIS)
            return None
        return items  # type: ignore

    @commands.group(name="steal", aliases=["emojisteal"], invoke_without_command=True)
    async def steal_command(self, ctx: commands.Context) -> None:
        """Steals the emojis and stickers of the message you reply to (including forwarded messages)."""
        if not (emojis := await self.steal_ctx(ctx)):
            return
        response = "\n".join(emoji.url for emoji in emojis)
        for page in pagify(response):
            await ctx.send(page)

    # Context menu added in cog_load
    async def steal_app_command(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        if not (emojis := self.extract_emotes(message)):
            await interaction.response.send_message(MISSING_EMOJIS, ephemeral=True)
            return

        response = "\n".join(emoji.url for emoji in emojis)
        pages = list(pagify(response))

        view: Optional[discord.ui.View] = None
        if interaction.guild is None:
            manageable_guilds = self.get_manageable_guilds(interaction.user.id)
            if manageable_guilds:
                view = ServerUploadView(self, manageable_guilds, emojis, interaction.user.id)

        if view is not None:
            await interaction.response.send_message(content=pages[0], ephemeral=True, view=view)
        else:
            await interaction.response.send_message(content=pages[0], ephemeral=True)

        for page in pages[1:]:
            await interaction.followup.send(content=page, ephemeral=True)

    @steal_command.command(name="upload")
    @commands.guild_only()
    @commands.has_permissions(manage_emojis=True)
    @commands.bot_has_permissions(manage_emojis=True)
    async def steal_upload_command(self, ctx: commands.Context, *names: str) -> None:
        """Steals emojis and stickers you reply to and uploads them to this server."""
        if ctx.guild is None:
            await ctx.send(GUILD_ONLY)
            return

        if not (emojis_or_stickers := await self.steal_ctx(ctx)):
            return

        if isinstance(emojis_or_stickers[0], discord.StickerItem):
            if len(ctx.guild.stickers) >= ctx.guild.sticker_limit:
                await ctx.send(STICKER_SLOTS)
                return

            sticker = emojis_or_stickers[0]
            if sticker.format == discord.StickerFormatType.lottie:
                await ctx.send(STICKER_LOTTIE)
                return

            sticker_name = re.sub(r"\s+", " ", sticker.name).strip()[:30] or "stolen_sticker"
            if names:
                joined_name = re.sub(r"\s+", " ", " ".join(names)).strip()[:30]
                if len(joined_name) < 2:
                    await ctx.send(STICKER_NAME_TOO_SHORT)
                    return
                sticker_name = joined_name

            fp = io.BytesIO()
            try:
                await sticker.save(fp)
                fp.seek(0)
                created = await ctx.guild.create_sticker(
                    name=sticker_name,
                    description=STICKER_DESC,
                    emoji=STICKER_EMOJI,
                    file=discord.File(fp, filename=self.sticker_filename(sticker)),
                )
            except (asyncio.TimeoutError, aiohttp.ClientError, discord.DiscordException) as error:
                detail = self._format_upload_error(error, is_sticker=True)
                await ctx.send(f"{STICKER_FAIL}, {detail}")
                return

            await ctx.send(f"{STICKER_SUCCESS}: {created.name}")
            return

        final_names = ["".join(re.findall(r"\w+", name))[:32] for name in names]
        final_names = [name if len(name) >= 2 else None for name in final_names]
        emojis: List[discord.PartialEmoji] = list(dict.fromkeys(emojis_or_stickers))  # type: ignore

        added_emojis: List[discord.Emoji] = []
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            for emoji, name in zip_longest(emojis, final_names):
                if emoji is None:
                    break
                if self.available_emoji_slots(ctx.guild, emoji.animated) <= 0:
                    await ctx.send(EMOJI_SLOTS)
                    break

                try:
                    async with session.get(emoji.url) as resp:
                        resp.raise_for_status()
                        image = await resp.read()
                    if len(image) > EMOJI_KB * 1024:
                        await ctx.send(f"{EMOJI_FAIL} {emoji.name}: {EMOJI_TOO_BIG}")
                        break
                    added = await ctx.guild.create_custom_emoji(
                        name=name or self.sanitize_emoji_name(emoji.name),
                        image=image,
                    )
                except (asyncio.TimeoutError, aiohttp.ClientError, discord.DiscordException) as error:
                    detail = self._format_upload_error(error, is_sticker=False)
                    await ctx.send(f"{EMOJI_FAIL} {emoji.name}, {detail}")
                    break

                added_emojis.append(added)
                try:
                    await ctx.message.add_reaction(added)
                except discord.DiscordException:
                    pass

        if added_emojis:
            summary = " ".join(str(e) for e in added_emojis)
            for page in pagify(summary, delims=[" "]):
                await ctx.send(page)

    # Context menu added in cog_load
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_emojis=True)
    @app_commands.checks.has_permissions(manage_emojis=True)
    @app_commands.checks.bot_has_permissions(manage_emojis=True)
    async def steal_upload_app_command(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(GUILD_ONLY, ephemeral=True)
            return

        if not (emojis_or_stickers := self.extract_emotes(message)):
            await interaction.response.send_message(MISSING_EMOJIS, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        result = await self.upload_items_to_guild(interaction.guild, emojis_or_stickers)
        await self._send_interaction_paginated(interaction, result)

    @commands.command()
    async def getemoji(self, ctx: commands.Context, *, emoji: str) -> None:
        """Get the image link for custom emojis or an emoji ID."""
        emoji = emoji.strip()

        if re.fullmatch(r"\d{10,22}", emoji):
            emojis = [
                discord.PartialEmoji(name="e", animated=b, id=int(emoji))
                for b in (False, True)
            ]
        elif not (emojis := self.get_emojis(emoji)):
            await ctx.send(INVALID_EMOJI)
            return

        response = "\n".join(em.url for em in emojis)
        for page in pagify(response):
            await ctx.send(page)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(manage_emojis=True)
    @commands.bot_has_permissions(manage_emojis=True)
    async def uploadsticker(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        """Uploads a sticker to the server, useful for mobile."""
        if ctx.guild is None:
            await ctx.send(GUILD_ONLY)
            return

        if len(ctx.guild.stickers) >= ctx.guild.sticker_limit:
            await ctx.send(STICKER_SLOTS)
            return

        if (
            not ctx.message.attachments
            or not ctx.message.attachments[0].filename.lower().endswith((".png", ".zip"))
        ):
            await ctx.send(STICKER_ATTACHMENT)
            return

        attachment = ctx.message.attachments[0]
        if (
            attachment.size > STICKER_KB * 1024
            or (attachment.width is not None and attachment.width > STICKER_DIM)
            or (attachment.height is not None and attachment.height > STICKER_DIM)
        ):
            await ctx.send(STICKER_TOO_BIG)
            return

        raw_name = name if name is not None else attachment.filename.rsplit(".", 1)[0]
        sticker_name = re.sub(r"\s+", " ", raw_name).strip()[:30]
        if len(sticker_name) < 2:
            await ctx.send(STICKER_NAME_TOO_SHORT)
            return

        async with ctx.typing():
            fp = io.BytesIO()
            try:
                await attachment.save(fp)
                fp.seek(0)

                if attachment.filename.lower().endswith(".zip"):
                    with zipfile.ZipFile(fp) as z:
                        files = z.namelist()
                        file = next(
                            (
                                f
                                for f in files
                                if f.lower().endswith(".png") and not f.startswith("__MACOSX/")
                            ),
                            None,
                        )
                        if not file:
                            await ctx.send(STICKER_ATTACHMENT)
                            return
                        if z.getinfo(file).file_size > STICKER_KB * 1024:
                            await ctx.send(STICKER_TOO_BIG)
                            return
                        data = z.read(file)
                        if len(data) > STICKER_KB * 1024:
                            await ctx.send(STICKER_TOO_BIG)
                            return
                        fp = io.BytesIO(data)

                sticker = await ctx.guild.create_sticker(
                    name=sticker_name,
                    description=f"{UPLOADED_BY} {ctx.author}",
                    emoji=STICKER_EMOJI,
                    file=discord.File(fp, filename="sticker.png"),
                )
            except (zipfile.BadZipFile, RuntimeError, EOFError):
                await ctx.send(ZIP_INVALID)
                return
            except (asyncio.TimeoutError, aiohttp.ClientError, discord.DiscordException) as error:
                detail = self._format_upload_error(error, is_sticker=True)
                await ctx.send(f"{STICKER_FAIL}, {detail}")
                return

        await ctx.send(f"{STICKER_SUCCESS}: {sticker.name}")
