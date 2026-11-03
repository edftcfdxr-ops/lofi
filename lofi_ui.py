from __future__ import annotations

from collections.abc import Iterable


TELEGRAM_TEXT_LIMIT = 4096
CARD_TEXT_LIMIT = 3800
MENU_BODY_LIMIT = 3000

_STYLE = {
    "success": ("✧  COMPLETE  ✧", "୨୧ ━━━━━━━ 𐙚 ━━━━━━━ ୨୧"),
    "error": ("⟡  ACTION NEEDED  ⟡", "𓂃 ࣪˖ ִֶָ  LOFI GWD  𓆩♡𓆪"),
    "warning": ("𐙚  PLEASE NOTE 𐙚", "⋆｡°✩  LOFI GWD  ✩°｡⋆"),
    "loading": ("ᯓ★  IN PROGRESS  ★ᯓ", "୨୧ ─── ⋆⋅☆⋅⋆ ─── ୨୧"),
    "info": ("⊹  LOFI GWD  ⊹", "𐙚  made for a softer orbit  ୨୧"),
    "menu": ("𖹭  COMMAND CENTER  𖹭", "୨୧ ━━━━━━━ 𐙚 ━━━━━━━ ୨୧"),
}


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _truncate_utf16(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    result: list[str] = []
    length = 0
    for character in text:
        size = utf16_length(character)
        if length + size > limit:
            break
        result.append(character)
        length += size
    return "".join(result)


def message_kind(text: str, requested: str | None = None) -> str:
    if requested in _STYLE:
        return requested

    lowered = text.lower()
    if text.lstrip().startswith(("❌", "🚫")) or any(
        word in lowered for word in ("error", "failed", "could not", "not authorized")
    ):
        return "error"
    if text.lstrip().startswith(("⚠️", "⚠")) or any(
        word in lowered for word in ("warning", "already present", "please note")
    ):
        return "warning"
    if text.lstrip().startswith(("✅", "✔")) or any(
        word in lowered for word in ("successfully", "completed", "saved", "started.")
    ):
        return "success"
    if text.lstrip().startswith(("⏳", "🔄", "⚡")) or any(
        word in lowered for word in ("processing", "generating", "loading")
    ):
        return "loading"
    return "info"


def render_card(
    text: str,
    title: str | None = None,
    kind: str | None = None,
    limit: int = CARD_TEXT_LIMIT,
) -> str:
    body = str(text).strip()
    selected_kind = message_kind(body, kind)
    label, footer = _STYLE[selected_kind]
    heading = title.strip() if title else label
    prefix = f"୨୧  {heading}  𐙚\n╭─ ⋆⋅☆⋅⋆ ──────────\n│\n"
    suffix = f"\n│\n╰────────── ⋆｡°✩\n{footer}"
    available = max(0, limit - utf16_length(prefix) - utf16_length(suffix))
    if utf16_length(body) > available:
        marker = "\n… text shortened to fit Telegram."
        body_limit = max(0, available - utf16_length(marker))
        body = _truncate_utf16(body, body_limit).rstrip() + marker
    return f"{prefix}{body}{suffix}"


def render_cards(text: str, title: str | None = None) -> list[str]:
    body_limit = 3400
    raw_lines = str(text).splitlines() or [""]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for raw_line in raw_lines:
        line_parts = [raw_line]
        index = 0
        while index < len(line_parts):
            part = line_parts[index]
            if utf16_length(part) <= body_limit:
                index += 1
                continue
            cut = _truncate_utf16(part, body_limit)
            line_parts[index:index + 1] = [cut, part[len(cut):]]
        for line in line_parts:
            line_length = utf16_length(line)
            if current and current_length + line_length + 1 > body_limit:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            current.append(line)
            current_length += line_length + (1 if len(current) > 1 else 0)
    if current or not chunks:
        chunks.append("\n".join(current))

    page_count = len(chunks)
    return [
        render_card(
            chunk if page_count == 1 else f"{chunk}\n\nPage {index}/{page_count}",
            title=title,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def render_menu_pages(
    sections: Iterable[tuple[str, Iterable[str]]],
    enabled_count: int,
    disabled_count: int,
    title: str = "𝗟𝗢𝗙𝗜 𝗚𝗪𝗗",
) -> list[str]:
    heading = (
        f"{title}\n"
        "ᴘʀᴇᴍɪᴜᴍ ᴄᴏᴍᴍᴀɴᴅ ᴄᴇɴᴛᴇʀ\n"
        f"✧ Enabled: {enabled_count}  ·  Disabled: {disabled_count} ✧"
    )
    pages: list[str] = []
    lines: list[str] = []
    current_length = utf16_length(heading)

    for section, entries_iter in sections:
        entries = list(entries_iter)
        section_lines = [f"\n⟡ {section} · {len(entries)}"]
        section_lines.extend(entries)
        for line in section_lines:
            line_length = utf16_length(line) + 1
            if lines and current_length + line_length > MENU_BODY_LIMIT:
                pages.append("\n".join([heading, *lines]))
                lines = []
                current_length = utf16_length(heading)
            if current_length + line_length > MENU_BODY_LIMIT:
                line = _truncate_utf16(line, MENU_BODY_LIMIT - current_length - 1)
                line_length = utf16_length(line) + 1
            lines.append(line)
            current_length += line_length

    if lines or not pages:
        pages.append("\n".join([heading, *lines]))

    page_count = len(pages)
    return [
        render_card(
            page if page_count == 1 else f"{page}\n\nPage {index}/{page_count}",
            kind="menu",
        )
        for index, page in enumerate(pages, start=1)
    ]
