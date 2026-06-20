from __future__ import annotations

import io
import logging

import discord
from discord import ui

logger = logging.getLogger(__name__)


class LiveLinkView(ui.View):
    def __init__(self, twitch_url: str):
        super().__init__(timeout=None)
        self.add_item(ui.Button(
            style=discord.ButtonStyle.link,
            label="  Watch",
            url=twitch_url,
            emoji=discord.PartialEmoji(name="Twitch", id=1478792581742723183)
        ))


class PaginatorView(ui.View):
    def __init__(self, pages, current=0, image_bytes: list[bytes] | None = None):
        super().__init__(timeout=300)
        self.pages = pages
        self.current = current
        self.message = None
        self.image_bytes = image_bytes  # set when local_dev=True
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1
        self.counter.label = f"{self.current + 1}/{len(self.pages)}"

    async def on_timeout(self):
        if self.message:
            try:
                if self.current != 0:
                    await self.message.edit(embed=self.pages[0], view=None)
                else:
                    await self.message.edit(view=None)
            except Exception:
                logger.debug("Paginator konnte beim Timeout nicht aktualisiert werden", exc_info=True)

    async def _edit(self, interaction):
        if self.image_bytes:
            file = discord.File(io.BytesIO(self.image_bytes[self.current]), filename="leaderboard.png")
            await interaction.response.edit_message(
                embed=self.pages[self.current],
                attachments=[file],
                view=self,
            )
        else:
            await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @ui.button(label="◂", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction, button):
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await self._edit(interaction)

    @ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction, button):
        pass

    @ui.button(label="▸", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._update_buttons()
        await self._edit(interaction)
