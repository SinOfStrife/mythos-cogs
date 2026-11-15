# Copyright (c) 2026 SinOfStrife and Contributors
# Licensed under MIT

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.http import Route
from redbot.core import app_commands, commands
from redbot.core.bot import Red

log = logging.getLogger("red.strifecogs.epoch")

TIMEZONE_ALIASES = {
    "jst": "Asia/Tokyo",
    "est": "America/New_York",
    "edt": "America/New_York",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "utc": "UTC",
    "gmt": "Etc/GMT",
    "aest": "Australia/Sydney",
    "aedt": "Australia/Sydney",
    "acst": "Australia/Adelaide",
    "acdt": "Australia/Adelaide",
    "awst": "Australia/Perth",
    "brisbane": "Australia/Brisbane",
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
}


def _apply_app_contexts(func_or_cmd: Any) -> Any:
    if hasattr(app_commands, "allowed_installs") and hasattr(app_commands, "allowed_contexts"):
        func_or_cmd = app_commands.allowed_installs(guilds=True, users=True)(func_or_cmd)
        func_or_cmd = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)(func_or_cmd)
    return func_or_cmd


class Epoch(commands.Cog):
    """Dynamic streaming timestamp generator with Components V2 layout, user-app support, and full timezone/DST handling."""

    __version__ = "1.1.2"
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

    def parse_flexible_datetime(self, time_str: str) -> datetime:
        clean_str = time_str.strip()

        formats_to_try = [
            "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
            "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
            "%m-%d-%Y %H:%M", "%m/%d/%Y %H:%M",
            "%Y-%m-%d %I:%M %p", "%Y/%m/%d %I:%M %p",
            "%d-%m-%Y %I:%M %p", "%d/%m/%Y %I:%M %p",
            "%m-%d-%Y %I:%M %p", "%m/%d/%Y %I:%M %p",
        ]

        for fmt in formats_to_try:
            try:
                return datetime.strptime(clean_str, fmt)
            except ValueError:
                continue

        raise ValueError("Unrecognized date/time format.")

    async def _send_v2_response(
        self, interaction: discord.Interaction, payload: dict, ephemeral: bool = True
    ) -> None:
        app_id = interaction.application_id or getattr(self.bot, "application_id", None) or self.bot.user.id
        route = Route(
            "PATCH",
            "/webhooks/{application_id}/{interaction_token}/messages/@original",
            application_id=app_id,
            interaction_token=interaction.token,
        )
        data = dict(payload)
        if ephemeral:
            data["flags"] = data.get("flags", 32768) | 64
        await self.bot.http.request(route, json=data)

    def _build_timestamp_payload(self, epoch_time: int, time_str: str, timezone_input: str, resolved_zone: str) -> dict:
        long_preview = f"<t:{epoch_time}:F>"
        rel_preview = f"<t:{epoch_time}:R>"

        content_block = (
            f"### 🕒 Streaming Timestamp Generator\n"
            f"Input: **{time_str}** (`{timezone_input.upper()}` → `{resolved_zone}`)\n\n"
            f"• **Long Preview:** {long_preview}\n"
            f"• **Countdown Preview:** {rel_preview}"
        )

        row_0_buttons = [
            {"type": 2, "style": 2, "label": "Long Date & Time", "custom_id": f"ts_{epoch_time}_F"},
            {"type": 2, "style": 3, "label": "Countdown", "custom_id": f"ts_{epoch_time}_R"},
            {"type": 2, "style": 2, "label": "Short Date & Time", "custom_id": f"ts_{epoch_time}_f"},
        ]

        row_1_buttons = [
            {"type": 2, "style": 2, "label": "Long Date", "custom_id": f"ts_{epoch_time}_D"},
            {"type": 2, "style": 2, "label": "Short Date", "custom_id": f"ts_{epoch_time}_d"},
            {"type": 2, "style": 2, "label": "Short Time", "custom_id": f"ts_{epoch_time}_t"},
            {"type": 2, "style": 2, "label": "Long Time", "custom_id": f"ts_{epoch_time}_T"},
        ]

        container_components = [
            {"type": 10, "content": content_block},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 1, "components": row_0_buttons},
            {"type": 1, "components": row_1_buttons},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": "-# Click any button above to instantly copy that timestamp format!"},
        ]

        container_component = {
            "type": 17,
            "accent_color": 5793266,
            "spoiler": False,
            "components": container_components,
        }

        return {
            "components": [container_component],
            "flags": 32832,
        }

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("ts_"):
            return

        parts = custom_id.split("_", 2)
        if len(parts) != 3:
            return

        try:
            _, epoch_str, format_style = parts
            timestamp_code = f"<t:{epoch_str}:{format_style}>"
            await interaction.response.send_message(f"`{timestamp_code}`", ephemeral=True)
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error generating code block: `{e}`", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error generating code block: `{e}`", ephemeral=True)

    @app_commands.command(
        name="timestamp",
        description="Convert schedule times into interactive Discord timestamps using Components V2.",
        extras={"red_force_enable": True},
    )
    @_apply_app_contexts
    @app_commands.describe(
        timezone="Timezone shortcut or IANA name (e.g., JST, EST, AEST)",
        time_str="Date and time (e.g., 20/11/2026 7:30 PM, or 2026-11-20 19:30)",
        style="Optional: Choose a specific format to get it instantly without buttons.",
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="Long Date/Time (:F) [Recommended]", value="F"),
        app_commands.Choice(name="Relative / Countdown (:R)", value="R"),
        app_commands.Choice(name="Short Date/Time (:f)", value="f"),
        app_commands.Choice(name="Long Date (:D)", value="D"),
        app_commands.Choice(name="Short Time (:t)", value="t"),
        app_commands.Choice(name="Short Date (:d)", value="d"),
        app_commands.Choice(name="Long Time (:T)", value="T"),
    ])
    async def timestamp(
        self,
        interaction: discord.Interaction,
        timezone: str,
        time_str: str,
        style: Optional[str] = None,
    ) -> None:
        # Defer immediately on line 1 to defeat Discord's 3-second timeout window
        await interaction.response.defer(ephemeral=True)

        cleaned_tz_input = timezone.strip().lower()
        resolved_tz_name = TIMEZONE_ALIASES.get(cleaned_tz_input, timezone.strip())

        try:
            src_zone = ZoneInfo(resolved_tz_name)
            dt_naive = self.parse_flexible_datetime(time_str)
            dt_source = dt_naive.replace(tzinfo=src_zone)
            epoch_time = int(dt_source.timestamp())

            if style:
                await interaction.followup.send(f"<t:{epoch_time}:{style}>", ephemeral=True)
                return

            payload = self._build_timestamp_payload(epoch_time, time_str, timezone, resolved_tz_name)
            await self._send_v2_response(interaction, payload, ephemeral=True)

        except ValueError:
            await interaction.followup.send(
                "❌ **Format Error:** Please use a valid layout like `DD/MM/YYYY HH:MM` or `DD/MM/YYYY I:M PM` (e.g., `20/11/2026 7:30 PM`).",
                ephemeral=True,
            )
        except ZoneInfoNotFoundError:
            await interaction.followup.send(
                f"❌ **Timezone Error:** Unknown zone `{timezone}`. Try standard shortcuts like `JST`, `EST`, `AEST`.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred: `{str(e)}`", ephemeral=True)