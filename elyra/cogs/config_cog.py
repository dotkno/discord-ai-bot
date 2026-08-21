"""
cogs/config_cog.py — Native Discord Dashboard & Configuration UI
Provides the /settings slash command with persistent button toggles and
channel-mapping select menus — a full in-Discord control panel.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

log = logging.getLogger("bot.config")

# ─── Persistent State ────────────────────────────────────────────────────────────
CONFIG_FILE = Path("data/server_config.json")
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: dict[str, Any] = {
    "ai_enabled": True,
    "automod_enabled": True,
    "welcome_enabled": True,
    "logging_enabled": True,
    "automod_sensitivity": "medium",   # low | medium | high
    "welcome_channel_id": None,
    "log_channel_id": None,
    "ai_channel_id": None,
}


def load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open() as f:
            data = json.load(f)
        # Merge with defaults to pick up any new keys
        return {**DEFAULT_CONFIG, **data}
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict[str, Any]) -> None:
    with CONFIG_FILE.open("w") as f:
        json.dump(cfg, f, indent=2)


# ─── Helper: Status Indicator ────────────────────────────────────────────────────
def _status(enabled: bool) -> str:
    return "🟢 **Online**" if enabled else "🔴 **Offline**"


def _sensitivity_badge(level: str) -> str:
    icons = {"low": "🔵 Low", "medium": "🟡 Medium", "high": "🔴 High"}
    return icons.get(level, "❓ Unknown")


# ─── Settings Embed Builder ──────────────────────────────────────────────────────
def build_settings_embed(cfg: dict[str, Any]) -> discord.Embed:
    """Constructs the rich dark-theme embed shown by /settings."""
    embed = discord.Embed(
        title="<:gear:> Server Control Panel",
        description=(
            "Manage all bot modules from this dashboard. "
            "Use the buttons and menus below to configure your server.\n"
            "─────────────────────────────"
        ),
        color=discord.Color.from_str("#5865F2"),
    )

    # ── Module Status Column ─────────────────────────────────────────────────────
    embed.add_field(
        name="🤖  AI Assistant",
        value=_status(cfg["ai_enabled"]),
        inline=True,
    )
    embed.add_field(
        name="🛡️  Auto-Moderation",
        value=_status(cfg["automod_enabled"]),
        inline=True,
    )
    embed.add_field(
        name="👋  Welcome Cards",
        value=_status(cfg["welcome_enabled"]),
        inline=True,
    )
    embed.add_field(
        name="📋  Audit Logging",
        value=_status(cfg["logging_enabled"]),
        inline=True,
    )
    embed.add_field(
        name="⚠️  AutoMod Sensitivity",
        value=_sensitivity_badge(cfg["automod_sensitivity"]),
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer

    # ── Channel Assignments ──────────────────────────────────────────────────────
    def _ch(channel_id: Optional[int]) -> str:
        return f"<#{channel_id}>" if channel_id else "`Not Set`"

    embed.add_field(
        name="📌  Channel Assignments",
        value=(
            f"**Welcome →** {_ch(cfg['welcome_channel_id'])}\n"
            f"**Logging  →** {_ch(cfg['log_channel_id'])}\n"
            f"**AI Chat  →** {_ch(cfg['ai_channel_id'])}"
        ),
        inline=False,
    )

    embed.set_footer(text="Changes apply instantly • Admin only")
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/emojis/1234567890.png"  # Replace with your server icon
    )
    return embed


# ─── Sensitivity Select Menu ─────────────────────────────────────────────────────
class SensitivitySelect(ui.Select):
    """Dropdown to adjust AutoMod sensitivity level."""

    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(
                label="Low",
                value="low",
                emoji="🔵",
                description="Filters only severe violations",
                default=(current == "low"),
            ),
            discord.SelectOption(
                label="Medium",
                value="medium",
                emoji="🟡",
                description="Balanced moderation (recommended)",
                default=(current == "medium"),
            ),
            discord.SelectOption(
                label="High",
                value="high",
                emoji="🔴",
                description="Aggressive — flags borderline content",
                default=(current == "high"),
            ),
        ]
        super().__init__(
            placeholder="⚙️  AutoMod Sensitivity…",
            options=options,
            custom_id="settings:sensitivity",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cfg = load_config()
        cfg["automod_sensitivity"] = self.values[0]
        save_config(cfg)

        await interaction.response.edit_message(
            embed=build_settings_embed(cfg),
            view=SettingsView(cfg),
        )
        log.info("AutoMod sensitivity → %s  (by %s)", self.values[0], interaction.user)


# ─── Toggle Button ───────────────────────────────────────────────────────────────
class ToggleButton(ui.Button):
    """
    A reusable toggle button that flips a boolean config key and re-renders
    the settings embed with updated state.
    """

    def __init__(
        self,
        *,
        label: str,
        config_key: str,
        current_value: bool,
        emoji: str,
        row: int = 0,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.green if current_value else discord.ButtonStyle.red,
            custom_id=f"settings:toggle:{config_key}",
            row=row,
        )
        self.config_key = config_key

    async def callback(self, interaction: discord.Interaction) -> None:
        cfg = load_config()
        cfg[self.config_key] = not cfg[self.config_key]
        save_config(cfg)

        new_val = cfg[self.config_key]
        self.style = discord.ButtonStyle.green if new_val else discord.ButtonStyle.red

        log.info(
            "%s toggled → %s  (by %s)",
            self.config_key,
            "ON" if new_val else "OFF",
            interaction.user,
        )

        await interaction.response.edit_message(
            embed=build_settings_embed(cfg),
            view=SettingsView(cfg),
        )


# ─── Channel Assignment Modal ─────────────────────────────────────────────────────
class ChannelAssignModal(ui.Modal, title="Assign Channels"):
    """
    A Modal (pop-up form) that lets admins paste channel IDs directly.
    Opens when the admin clicks the "Assign Channels" button.
    """

    welcome = ui.TextInput(
        label="Welcome Channel ID",
        placeholder="Right-click a channel → Copy ID",
        required=False,
        max_length=20,
    )
    logging_ch = ui.TextInput(
        label="Logging Channel ID",
        placeholder="Right-click a channel → Copy ID",
        required=False,
        max_length=20,
    )
    ai_chat = ui.TextInput(
        label="AI Chat Channel ID",
        placeholder="Right-click a channel → Copy ID",
        required=False,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cfg = load_config()

        def _parse(field: ui.TextInput) -> Optional[int]:
            val = field.value.strip()
            return int(val) if val.isdigit() else None

        if (wid := _parse(self.welcome)) is not None:
            cfg["welcome_channel_id"] = wid
        if (lid := _parse(self.logging_ch)) is not None:
            cfg["log_channel_id"] = lid
        if (aid := _parse(self.ai_chat)) is not None:
            cfg["ai_channel_id"] = aid

        save_config(cfg)
        log.info("Channel assignments updated by %s", interaction.user)

        await interaction.response.edit_message(
            embed=build_settings_embed(cfg),
            view=SettingsView(cfg),
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("ChannelAssignModal error: %s", error)
        await interaction.response.send_message(
            "❌ Invalid input — make sure you entered numeric channel IDs.",
            ephemeral=True,
        )


# ─── Channel Assign Button ────────────────────────────────────────────────────────
class AssignChannelsButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Assign Channels",
            emoji="📌",
            style=discord.ButtonStyle.blurple,
            custom_id="settings:assign_channels",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ChannelAssignModal())


# ─── Settings View (Full Layout) ─────────────────────────────────────────────────
class SettingsView(ui.View):
    """
    Assembles all toggle buttons, the sensitivity select, and the channel
    assign button into a single cohesive View.  timeout=None keeps it alive
    forever (persistent across bot restarts when custom_ids are registered).
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(timeout=None)

        # Row 0 — Module toggles
        self.add_item(ToggleButton(
            label="AI Assistant",
            config_key="ai_enabled",
            current_value=cfg["ai_enabled"],
            emoji="🤖",
            row=0,
        ))
        self.add_item(ToggleButton(
            label="Auto-Mod",
            config_key="automod_enabled",
            current_value=cfg["automod_enabled"],
            emoji="🛡️",
            row=0,
        ))
        self.add_item(ToggleButton(
            label="Welcome Cards",
            config_key="welcome_enabled",
            current_value=cfg["welcome_enabled"],
            emoji="👋",
            row=0,
        ))
        self.add_item(ToggleButton(
            label="Audit Log",
            config_key="logging_enabled",
            current_value=cfg["logging_enabled"],
            emoji="📋",
            row=0,
        ))

        # Row 1 — Sensitivity dropdown
        self.add_item(SensitivitySelect(current=cfg["automod_sensitivity"]))

        # Row 2 — Channel assignment button
        self.add_item(AssignChannelsButton())


# ─── Cog ─────────────────────────────────────────────────────────────────────────
class ConfigCog(commands.Cog, name="Configuration"):
    """Handles the /settings command and the live in-Discord dashboard."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Register persistent views so they survive bot restarts
        bot.add_view(SettingsView(load_config()))

    # ── /settings ────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="settings",
        description="Open the server control panel  (Admin only)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def settings(self, interaction: discord.Interaction) -> None:
        """Sends the live settings dashboard as an ephemeral embed."""
        cfg = load_config()
        embed = build_settings_embed(cfg)
        view = SettingsView(cfg)

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,   # Only the admin sees it — keeps channels clean
        )
        log.info("/settings opened by %s", interaction.user)

    # ── /config reset ────────────────────────────────────────────────────────────
    @app_commands.command(
        name="config-reset",
        description="Reset all bot settings to factory defaults  (Admin only)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def config_reset(self, interaction: discord.Interaction) -> None:
        """Wipes and re-creates the config file with default values."""
        save_config(DEFAULT_CONFIG.copy())
        embed = discord.Embed(
            title="✅ Configuration Reset",
            description="All settings have been restored to factory defaults.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.warning("Config RESET by %s", interaction.user)

    # ── /help ───────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="help",
        description="Show all available commands and bot information",
    )
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Displays a comprehensive help embed with all bot commands and features."""
        embed = discord.Embed(
            title="🤖 Discord AI Assistant Bot - Help",
            description=(
                "A multi-purpose Discord bot with AI capabilities, moderation tools, "
                "and server management features.\n\n"
                "**All commands are slash commands** - type `/` to see the auto-complete menu!"
            ),
            color=discord.Color.from_str("#5865F2"),
        )

        # Configuration Commands
        embed.add_field(
            name="⚙️ Configuration",
            value=(
                "**/settings** - Open the server control panel (Admin only)\n"
                "**/config-reset** - Reset all settings to defaults (Admin only)\n"
                "**/help** - Show this help message"
            ),
            inline=False,
        )

        # Moderation Commands
        embed.add_field(
            name="🛡️ Moderation",
            value=(
                "**/warn** - Warn a member\n"
                "**/mute** - Timeout a member\n"
                "**/unmute** - Remove timeout\n"
                "**/kick** - Kick a member\n"
                "**/ban** - Ban a member\n"
                "**/unban** - Unban a user by ID\n"
                "**/purge** - Delete messages in bulk\n"
                "**/userinfo** - Show user information"
            ),
            inline=False,
        )

        # AI Commands
        embed.add_field(
            name="🧠 AI",
            value=(
                "**/clear-memory** - Clear your AI conversation history\n"
                "• **Mention the bot** in any message to get an AI response\n"
                "• **AI Channel:** Set a dedicated channel via /settings for AI conversations"
            ),
            inline=False,
        )

        # Auto-Mod
        embed.add_field(
            name="⚡ Auto-Moderation",
            value=(
                "Automatically filters bad content with adjustable sensitivity:\n"
                "• 🔵 **Low** - Filters only severe violations\n"
                "• 🟡 **Medium** - Balanced moderation (recommended)\n"
                "• 🔴 **High** - Aggressive filtering\n\n"
                "Configure via /settings"
            ),
            inline=False,
        )

        # Other Features
        embed.add_field(
            name="👋 Welcome Cards",
            value=(
                "Automatically sends welcome messages to new members when they join. "
                "Configure the welcome channel via /settings."
            ),
            inline=False,
        )

        embed.add_field(
            name="📋 Audit Logging",
            value=(
                "All moderation actions are automatically logged to the configured log channel. "
                "Set up via /settings."
            ),
            inline=False,
        )

        embed.set_footer(text="Type / to see all available commands with auto-complete")
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url if interaction.client.user else None)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info("/help used by %s", interaction.user)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConfigCog(bot))