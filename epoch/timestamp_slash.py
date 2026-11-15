from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import discord
from discord.http import Route
from redbot.core import commands
from discord import app_commands

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

class Epoch(commands.Cog):
    """A tool for generating Discord timestamps using Components V2 layout structures."""

    def __init__(self, bot):
        self.bot = bot

    def parse_flexible_datetime(self, time_str: str) -> datetime:
        """Robust multi-format parser supporting 24-hour, AM/PM, hyphens, and slashes."""
        clean_str = time_str.strip()
        
        formats_to_try = [
            # 24-hour variants
            "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
            "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
            "%m-%d-%Y %H:%M", "%m/%d/%Y %H:%M",
            # 12-hour AM/PM variants
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
        """Sends or updates an interaction response with a Components V2 payload via REST."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
            
        app_id = interaction.application_id or self.bot.application_id
        route = Route(
            "PATCH",
            "/webhooks/{application_id}/{interaction_token}/messages/@original",
            application_id=app_id,
            interaction_token=interaction.token,
        )
        data = dict(payload)
        if ephemeral:
            data["flags"] = data.get("flags", 0) | 64
        await self.bot.http.request(route, json=data)

    def _build_timestamp_payload(self, epoch_time: int, time_str: str, timezone_input: str, resolved_zone: str) -> dict:
        """Builds a Components V2 container payload matching advanced invite layout structures."""
        long_preview = f"<t:{epoch_time}:F>"
        rel_preview = f"<t:{epoch_time}:R>"

        content_block = (
            f"### 🕒 Streaming Timestamp Generator\n"
            f"Input: **{time_str}** (`{timezone_input.upper()}` → `{resolved_zone}`)\n\n"
            f"• **Long Preview:** {long_preview}\n"
            f"• **Countdown Preview:** {rel_preview}"
        )

        # Row 0: Primary formats with clean human-readable names
        row_0_buttons = [
            {"type": 2, "style": 2, "label": "Long Date & Time", "custom_id": f"ts_{epoch_time}_F"},
            {"type": 2, "style": 3, "label": "Countdown", "custom_id": f"ts_{epoch_time}_R"},
            {"type": 2, "style": 2, "label": "Short Date & Time", "custom_id": f"ts_{epoch_time}_f"},
        ]

        # Row 1: Additional formats with clean text labels
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
            {"type": 10, "content": "-# Click any button above to instantly copy that timestamp format!"}
        ]

        container_component = {
            "type": 17,
            "accent_color": 5793266,  # Blurple accent matching v2 design
            "spoiler": False,
            "components": container_components,
        }

        return {
            "components": [container_component],
            "flags": 64,  # Fully ephemeral response
        }

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handles Component V2 button clicks for timestamp copy actions."""
        if interaction.type != discord.InteractionType.component:
            return
        
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("ts_"):
            return

        try:
            _, epoch_str, format_style = custom_id.split("_", 2)
            timestamp_code = f"<t:{epoch_str}:{format_style}>"
            
            # Send the copyable code snippet privately to the user
            await interaction.response.send_message(f"`{timestamp_code}`", ephemeral=True)
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error generating code block: `{e}`", ephemeral=True)

    @app_commands.command(name="timestamp", description="Convert schedule times into interactive Discord timestamps using Components V2.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        timezone="Timezone shortcut or IANA name (e.g., JST, EST, AEST)",
        time_str="Date and time (e.g., 20/11/2026 7:30 PM, or 2026-11-20 19:30)",
        style="Optional: Choose a specific format to get it instantly without buttons."
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
    async def timestamp(self, interaction: discord.Interaction, timezone: str, time_str: str, style: str = None):
        cleaned_tz_input = timezone.strip().lower()
        resolved_tz_name = TIMEZONE_ALIASES.get(cleaned_tz_input, timezone.strip())

        try:
            src_zone = ZoneInfo(resolved_tz_name)
            dt_naive = self.parse_flexible_datetime(time_str)
            dt_source = dt_naive.replace(tzinfo=src_zone)
            epoch_time = int(dt_source.timestamp())

            if style:
                await interaction.response.send_message(f"<t:{epoch_time}:{style}>", ephemeral=True)
                return

            payload = self._build_timestamp_payload(epoch_time, time_str, timezone, resolved_tz_name)
            await self._send_v2_response(interaction, payload, ephemeral=True)

        except ValueError:
            await interaction.response.send_message(
                "❌ **Format Error:** Please use a valid layout like `DD/MM/YYYY HH:MM` or `DD/MM/YYYY I:M PM` (e.g., `20/11/2026 7:30 PM`).",
                ephemeral=True
            )
        except ZoneInfoNotFoundError:
            await interaction.response.send_message(
                f"❌ **Timezone Error:** Unknown zone `{timezone}`. Try standard shortcuts like `JST`, `EST`, `AEST`.",
                ephemeral=True
            )
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ An unexpected error occurred: `{str(e)}`", ephemeral=True)
      
