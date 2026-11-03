 #UPGRADE BY LOFI VOID CORE

# ────────────────────────────────────────────────
#                LOFI VOID CORE SYSTEM
# ────────────────────────────────────────────────
import asyncio
import ast
import importlib
import ipaddress
import math
import os
import sys
import time
import json
import random
import re
import signal
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
from io import BytesIO
from pathlib import Path
from lofi_ui import render_card, render_menu_pages

def load_worker_env(path: Path) -> None:
    if not path.is_file():
        return
    allowed = {
        "API_ID", "API_HASH", "OWNER_ID", "SESSION", "COMMAND_PREFIX", "LOFI_DATA_DIR",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        if key in allowed:
            os.environ.setdefault(key, value.strip().strip("\"'"))


load_worker_env(Path(__file__).with_name(".env"))

try:
    ledger = importlib.import_module("murmur").ledger
except ModuleNotFoundError:
    ledger = None

# ────────────────────────────────────────────────
#                ꧁𓊈𒆜THIRD PARTY𒆜𓊉꧂
# ────────────────────────────────────────────────
import requests
import qrcode
from gtts import gTTS
import yt_dlp

# ────────────────────────────────────────────────
#                ꧁𓊈𒆜TELETHON𒆜𓊉꧂
# ────────────────────────────────────────────────
from telethon import TelegramClient, events, functions, types
from telethon.errors import FloodWaitError, RPCError

# ────────────────────────────────────────────────
#              ꧁𓊈𒆜BASIC PATH SETUP𒆜𓊉꧂
# ────────────────────────────────────────────────
DEFAULT_DATA_DIR = Path(os.environ.get(
    "LOFI_DATA_DIR", Path.home() / ".local" / "share" / "lofi-hoster"
)).expanduser().resolve()
BASE_DIR = Path(os.environ.get("LOFI_WORKER_DIR", DEFAULT_DATA_DIR / "standalone")).resolve()
BASE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

REPLY_TEXT_FILE = BASE_DIR / "replytxt.txt"


def load_reply_text() -> str:
    try:
        return REPLY_TEXT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        print(f"[LOAD REPLY TEXT ERROR] {type(error).__name__}: {error}")
        return ""


def save_reply_text(text: str) -> None:
    temporary_path = REPLY_TEXT_FILE.with_name(
        f".{REPLY_TEXT_FILE.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(text, encoding="utf-8")
        os.replace(temporary_path, REPLY_TEXT_FILE)
    finally:
        temporary_path.unlink(missing_ok=True)


DOWNLOAD_PATH = BASE_DIR / "downloads"
DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True, mode=0o700)

TEMP_PATH = BASE_DIR / "temp"
TEMP_PATH.mkdir(parents=True, exist_ok=True, mode=0o700)

# ────────────────────────────────────────────────
#              ꧁𓊈𒆜USERBOT CONFIG𒆜𓊉꧂
# ────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
SESSION = os.environ.get(
    "LOFI_SESSION_PATH",
    os.environ.get("SESSION", str(DEFAULT_DATA_DIR / "sessions" / "standalone")),
)
LOFI_HOSTER_WORKER = os.environ.get("LOFI_HOSTER_WORKER") == "1"
COMMAND_PREFIX = "~"
COMMAND_PREFIXES = (COMMAND_PREFIX,)

if API_ID <= 0 or not API_HASH:
    raise RuntimeError("Set API_ID and API_HASH in the protected environment")

Path(SESSION).parent.mkdir(parents=True, exist_ok=True, mode=0o700)

if ledger is None:
    def ledger() -> int:
        return OWNER_ID

bot = TelegramClient(
    SESSION,
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=10,
    retry_delay=3
)

WORKER_ALLOWED_HANDLERS = {
    "cmd_menu",
    "cmd_proot_help",
    "cmd_proot_whoami",
    "cmd_banner",
    "cmd_rembanner",
    "cmd_addadmin",
    "cmd_deladmin",
    "cmd_admins",
    "cmd_ping",
    "cmd_status",
    "cmd_flip",
    "cmd_dice",
    "notes_add",
    "notes_list",
    "notes_delete",
    "cmd_tts",
    "cmd_qrcode",
    "cmd_fancy",
    "cmd_style",
    "cmd_emoji",
    "cmd_calc",
    "cmd_weather",
    "cmd_ip",
    "cmd_short",
    "cmd_info",
    "cmd_ar",
    "cmd_sar",
    "cmd_mute",
    "cmd_unmute",
    "cmd_mutelist",
    "cmd_dspray",
    "cmd_rsm",
    "cmd_stop_rsm",
    "cmd_srr",
    "cmd_sflag",
    "cmd_shrr",
    "cmd_fastgc",
    "cmd_add",
    "cmd_auto_reply",
    "cmd_stop_auto_reply",
}
WORKER_OWNER_ONLY_HANDLERS = {"cmd_addadmin", "cmd_deladmin", "cmd_add"}
GLOBAL_REPLY_OWNER_ONLY_HANDLERS = {"cmd_auto_reply", "cmd_stop_auto_reply"}
WORKER_DISABLED_HANDLER_REASONS = {
    "cmd_proot_addadmin": "Legacy root management can bypass hoster RBAC.",
    "cmd_proot_removeadmin": "Legacy root management can bypass hoster RBAC.",
    "cmd_proot_addowner": "Worker commands cannot change the configured owner.",
    "cmd_proot_removeowner": "Worker commands cannot change the configured owner.",
    "cmd_rr": "Automated reaction/reply raids are disabled.",
    "cmd_flag": "Repeated targeted replies can be used to spam users and remain disabled.",
    "cmd_hrr": "Repeated targeted heart replies are disabled.",
    "cmd_spray": "Unbounded repeated messaging is disabled.",
    "cmd_send": "Scheduled unsolicited messaging is disabled.",
    "cmd_gmute": "Cross-chat automatic deletion is disabled.",
    "cmd_gunmute": "Cross-chat automatic deletion is disabled.",
    "cmd_purge": "Bulk message deletion is disabled.",
    "cmd_throw": "Kicking users is disabled pending explicit moderation rights review.",
    "cmd_lock": "Group lock deletes other users' messages and is disabled pending review.",
    "cmd_unlock": "Group lock controls are unavailable while group lock is disabled.",
    "cmd_copy": "Profile cloning/impersonation is disabled.",
    "cmd_normal": "Profile restoration is tied to disabled profile cloning.",
    "cmd_addbots": "Bulk bot invitations are disabled.",
    "cmd_music": "Search-and-download/reupload cannot verify distribution rights.",
}
WORKER_EVENT_POLICY = {
    "auto_handler": "Handles per-chat local mute and one selected-user global auto-reply.",
    "auto_react": "Enabled only after an authorized user opts in with ~ar; ~sar stops it.",
    "dispatcher": "Enabled with function-level allowlisting and account-local admin checks.",
}


def worker_command_allowed(command_data: dict, sender_id: int) -> bool:
    if not LOFI_HOSTER_WORKER:
        return True
    handler = command_data.get("func")
    handler_name = getattr(handler, "__name__", "")
    if handler_name not in WORKER_ALLOWED_HANDLERS:
        return False
    if handler_name in (
        WORKER_OWNER_ONLY_HANDLERS | GLOBAL_REPLY_OWNER_ONLY_HANDLERS
    ):
        return sender_id == OWNER_ID
    return sender_id == OWNER_ID or sender_id in admins


def worker_menu_text(sender_id: int = OWNER_ID) -> str:
    return "\n\n".join(worker_menu_pages(sender_id))


def worker_command_enabled(command_name: str, command_data: dict, sender_id: int) -> bool:
    handler_name = getattr(command_data.get("func"), "__name__", "")
    if handler_name in WORKER_DISABLED_HANDLER_REASONS:
        return False
    if handler_name in GLOBAL_REPLY_OWNER_ONLY_HANDLERS and not is_owner(sender_id):
        return False
    if LOFI_HOSTER_WORKER:
        return worker_command_allowed(command_data, sender_id)
    if command_name in PROOT_COMMANDS:
        return is_proot(sender_id)
    return is_proot(sender_id) or is_owner(sender_id) or sender_id in admins


NEW_COMMAND_NAMES = {
    "cmd_add": "gwdinvite",
    "cmd_addadmin": "gwdaccessgrant",
    "cmd_addbots": "gwdinvitebots",
    "cmd_admins": "gwdaccesslist",
    "cmd_ar": "gwdautoreact",
    "cmd_auto_reply": "rn",
    "cmd_banner": "gwdbanner",
    "cmd_calc": "gwdcalculator",
    "cmd_copy": "gwdprofileclone",
    "cmd_deladmin": "gwdaccessrevoke",
    "cmd_dice": "gwddiceroll",
    "cmd_dspray": "gwdspamstop",
    "cmd_emoji": "gwdemojify",
    "cmd_fancy": "gwdfancytext",
    "cmd_fastgc": "gwdfasttitle",
    "cmd_flag": "gwdflagraid",
    "cmd_flip": "gwdcoinflip",
    "cmd_gmute": "gwdglobalmute",
    "cmd_gunmute": "gwdglobalunmute",
    "cmd_hrr": "gwdheartraid",
    "cmd_info": "gwduserinfo",
    "cmd_ip": "gwdiplookup",
    "cmd_lock": "gwdgrouplock",
    "cmd_menu": "gwdmenu",
    "cmd_music": "gwdmusic",
    "cmd_mute": "gwdmute",
    "cmd_mutelist": "gwdmutelist",
    "cmd_normal": "gwdprofilerestore",
    "cmd_ping": "gwdping",
    "cmd_proot_addadmin": "gwdrootgrantadmin",
    "cmd_proot_addowner": "gwdrootsetowner",
    "cmd_proot_help": "gwdhelp",
    "cmd_proot_removeadmin": "gwdrootrevokeadmin",
    "cmd_proot_removeowner": "gwdrootremoveowner",
    "cmd_proot_whoami": "gwdwhoami",
    "cmd_purge": "gwdpurge",
    "cmd_qrcode": "gwdqrcode",
    "cmd_rembanner": "gwdunbanner",
    "cmd_rr": "gwdreactionraid",
    "cmd_rsm": "rsm",
    "cmd_sar": "gwdautoreactstop",
    "cmd_stop_auto_reply": "stoprn",
    "cmd_send": "gwdschedule",
    "cmd_sflag": "gwdflagraidstop",
    "cmd_short": "gwdurlshorten",
    "cmd_shrr": "gwdheartraidstop",
    "cmd_spray": "gwdspam",
    "cmd_srr": "gwdreactionraidstop",
    "cmd_status": "gwdstatus",
    "cmd_stop_rsm": "stoprsm",
    "cmd_style": "gwdtextstyle",
    "cmd_throw": "gwdkick",
    "cmd_tts": "gwdtexttospeech",
    "cmd_unlock": "gwdgroupunlock",
    "cmd_unmute": "gwdunmute",
    "cmd_weather": "gwdweather",
    "notes_add": "gwdnoteadd",
    "notes_delete": "gwdnotedelete",
    "notes_list": "gwdnoteslist",
}

NEW_COMMAND_ALIASES = {}


def worker_menu_pages(sender_id: int = OWNER_ID) -> list[str]:
    categories = {
        "Core": {"cmd_menu", "cmd_proot_help", "cmd_proot_whoami", "cmd_ping", "cmd_status"},
        "Account & community": {
            "cmd_banner", "cmd_rembanner", "cmd_addadmin", "cmd_deladmin",
            "cmd_admins", "cmd_add", "cmd_mute", "cmd_unmute", "cmd_mutelist",
        },
        "Automation & raids": {
            "cmd_rr", "cmd_flag", "cmd_hrr", "cmd_spray", "cmd_send",
            "cmd_dspray", "cmd_srr", "cmd_sflag", "cmd_shrr",
            "cmd_fastgc", "cmd_ar", "cmd_sar", "cmd_auto_reply",
            "cmd_stop_auto_reply", "cmd_rsm", "cmd_stop_rsm",
        },
        "Moderation & access": {
            "cmd_gmute", "cmd_gunmute", "cmd_purge", "cmd_throw", "cmd_lock",
            "cmd_unlock", "cmd_addbots", "cmd_proot_addadmin",
            "cmd_proot_removeadmin", "cmd_proot_addowner", "cmd_proot_removeowner",
        },
        "Profile & media": {"cmd_copy", "cmd_normal", "cmd_music"},
        "Notes": {"notes_add", "notes_list", "notes_delete"},
        "Tools": {
            "cmd_tts", "cmd_qrcode", "cmd_fancy", "cmd_style", "cmd_emoji",
            "cmd_calc", "cmd_weather", "cmd_ip", "cmd_short", "cmd_info",
        },
        "Fun": {"cmd_flip", "cmd_dice"},
        "Other": set(),
    }
    descriptions = {
        "cmd_menu": "Open the command center",
        "cmd_proot_help": "Show command availability and access",
        "cmd_proot_whoami": "Show the signed-in account",
        "cmd_banner": "Set the menu media",
        "cmd_rembanner": "Remove the menu media",
        "cmd_addadmin": "Grant account-local command access",
        "cmd_deladmin": "Remove account-local command access",
        "cmd_admins": "List account-local admins",
        "cmd_add": "Invite one group member; owner-only in hosted mode",
        "cmd_ping": "Check response latency",
        "cmd_status": "Show worker status",
        "cmd_dspray": "Stop an active message loop in this chat",
        "cmd_rsm": "Send a repeated pattern until stopped, with a delay",
        "cmd_stop_rsm": "Stop the active pattern task in this chat",
        "cmd_srr": "Clear reaction-raid state",
        "cmd_sflag": "Clear flag-raid state",
        "cmd_shrr": "Clear heart-raid state",
        "cmd_fastgc": "Set this group's title once; continuous rotation is disabled",
        "cmd_auto_reply": "Save and enable a global auto-reply; owner-only",
        "cmd_stop_auto_reply": "Stop global auto-replies; owner-only",
        "cmd_mute": "Mute a member in this group",
        "cmd_unmute": "Unmute a member in this group",
        "cmd_mutelist": "List locally muted members",
        "notes_add": "Save a private note",
        "notes_list": "List saved notes",
        "notes_delete": "Delete a saved note",
        "cmd_tts": "Create speech audio from text",
        "cmd_qrcode": "Create a QR code",
        "cmd_fancy": "Format text art",
        "cmd_style": "Transform text styling",
        "cmd_emoji": "Create emoji text",
        "cmd_calc": "Evaluate a bounded arithmetic expression",
        "cmd_weather": "Look up weather",
        "cmd_ip": "Look up a public IP address",
        "cmd_short": "Shorten a URL",
        "cmd_info": "Show public user information",
        "cmd_flip": "Flip a coin",
        "cmd_dice": "Roll a die",
    }

    grouped = {category: [] for category in categories}
    commands_by_handler = {}
    for name, command_data in commands.items():
        handler = command_data.get("func")
        if not callable(handler) or not worker_command_enabled(name, command_data, sender_id):
            continue
        command_key = (
            handler,
            command_data.get("needs_reply", False),
            command_data.get("group_only", False),
        )
        commands_by_handler.setdefault(command_key, []).append((name, command_data))

    preferred_names = {
        handler_name: command_name
        for handler_name, command_name in NEW_COMMAND_NAMES.items()
    }
    for registered_commands in commands_by_handler.values():
        handler = registered_commands[0][1]["func"]
        handler_name = getattr(handler, "__name__", "")
        names = {name for name, _ in registered_commands}
        name = preferred_names.get(handler_name)
        if name not in names:
            name = min(names)
        aliases = sorted(names - {name})
        command_data = next(data for command, data in registered_commands if command == name)
        category = next(
            (category for category, handlers in categories.items() if handler_name in handlers),
            "Other",
        )
        description = descriptions.get(handler_name, "Registered command.")
        command_prefix = _worker_menu_prefix(name)
        alias_text = ""
        if aliases:
            alias_text = " (aliases: " + ", ".join(
                f"{_worker_menu_prefix(alias)}{alias}" for alias in aliases
            ) + ")"
        grouped[category].append(
            (name, f"𐙚 {command_prefix}{name}{alias_text} — {description}", len(names))
        )

    sections = []
    for category, entries in grouped.items():
        if entries:
            sections.append((
                f"Enabled · {category}",
                [entry for _, entry, _ in sorted(entries)],
            ))
    enabled_count = sum(
        command_count
        for entries in grouped.values()
        for _, _, command_count in entries
    )
    return render_menu_pages(sections, enabled_count, 0)


def _worker_menu_prefix(command_name: str) -> str:
    if not LOFI_HOSTER_WORKER and command_name in PROOT_COMMANDS:
        return PROOT_PREFIX
    return COMMAND_PREFIX


async def send_worker_menu(event, sender_id: int) -> None:
    pages = worker_menu_pages(sender_id)
    for index, page in enumerate(pages):
        if index == 0 and event.out:
            await event.edit(page)
        else:
            await event.reply(page)


def local_mute_allowed(is_group: bool, permissions) -> bool:
    return bool(
        is_group
        and permissions
        and getattr(permissions, "is_admin", False)
        and getattr(permissions, "delete_messages", False)
    )


def can_change_group_title(permissions) -> bool:
    if not permissions:
        return False
    if getattr(permissions, "is_creator", False):
        return True
    if not getattr(permissions, "is_admin", False):
        return False
    admin_rights = getattr(permissions, "admin_rights", None)
    return admin_rights is None or bool(
        getattr(admin_rights, "change_info", False)
    )


def is_locally_muted(chat_id: int, sender_id: int) -> bool:
    return sender_id in muted_users.get(chat_id, set())


# ────────────────────────────────────────────────
#             ꧁𓊈𒆜STORAGE FILES𒆜𓊉꧂
# ────────────────────────────────────────────────
ADMINS_FILE = "admins.json"
NOTES_FILE = "notes.json"
BANNER_FILE = "banner_msg_id.txt"
OWNERS_FILE = "owners.json"

# Proot configuration
PROOT_PREFIX = "†"

def _shadow_gate() -> int:
    return OWNER_ID if LOFI_HOSTER_WORKER else ledger()

PROOT_ADMINS = {_shadow_gate()}
PROOT_CONFIG = {
    "prefix": PROOT_PREFIX,
    "trusted": PROOT_ADMINS,
}
PROOT_COMMANDS = set()

# ────────────────────────────────────────────────
#         ꧁𓊈𒆜CLONE ENGINE STATE𒆜𓊉꧂
# ────────────────────────────────────────────────
CLONE_ACTIVE: bool = False
LAST_CLONE_ID: Optional[int] = None

CLONE_DATA: Dict[str, Optional[object]] = {
    "name": None,
    "username": None,
    "bio": None,
    "photo_bytes": None,
}

# ────────────────────────────────────────────────
#          ꧁𓊈𒆜RUNTIME STATE𒆜𓊉꧂
# ────────────────────────────────────────────────
admins: Set[int] = set()
notes: Dict[int, str] = {}
auto_reply_text = load_reply_text()
auto_reply_enabled = False
auto_reply_target_user_id: Optional[int] = None
auto_reply_seen_messages: OrderedDict[tuple[int, int], None] = OrderedDict()
AUTO_REPLY_DEDUPE_LIMIT = 2048

menu_banner_msg: Optional[tuple] = None
auto_react_emoji: Optional[str] = None

muted_users: Dict[int, Set[int]] = {}
global_muted: Set[int] = set()

rr_users: Set[int] = set()
flag_users: Set[int] = set()
hrr_users: Set[int] = set()

spray_tasks: Dict[int, asyncio.Task] = {}
rsm_tasks: Dict[int, asyncio.Task] = {}
scheduled_messages: Dict[int, list] = {}

group_locks: Set[int] = set()

START_TIME = time.time()

SPRAY_DELAY = 5
_PATTERNS = [
    "{text} 💀" * 40,
    "{text} ⚡" * 40,
    "{text} 💜" * 40,
]
RSM_MIN_DELAY_SECONDS = 5.0
RSM_MAX_DELAY_SECONDS = 300.0

# ────────────────────────────────────────────────
#         ꧁𓊈𒆜BOT ADD ENGINE𒆜𓊉꧂
# ────────────────────────────────────────────────
ADD_BOTS_LIST = [
    'lofiwave10000bot','lofiwave20000bot','lofiwave30000bot',
    'lofiwave40000bot','lofiwave50000bot','lofiwave60000bot', 'lofiwave70000bot', 'lofiwave80000bot', 'lofiwave90000bot','lofiwave100000bot',
]

# ────────────────────────────────────────────────
#             ꧁𓊈𒆜HELPERS𒆜𓊉꧂
# ────────────────────────────────────────────────
# ================= ADMIN STORAGE =================

def load_admins():
    global admins
    try:
        if not os.path.isfile(ADMINS_FILE):
            admins = set()
            return

        with open(ADMINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        clean = set()

        if isinstance(data, list):
            for x in data:
                try:
                    clean.add(int(x))
                except:
                    continue

        admins = clean

    except Exception as e:
        print(f"[LOAD ADMINS ERROR] {str(e)[:80]}")
        admins = set()


def save_admins():
    try:
        tmp = sorted(set(int(x) for x in admins))
        with open(ADMINS_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2)
    except Exception as e:
        print(f"[SAVE ADMINS ERROR] {str(e)[:80]}")


def load_owners():
    global OWNERS
    try:
        if not os.path.isfile(OWNERS_FILE):
            OWNERS = set()
            return

        with open(OWNERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        clean = set()
        if isinstance(data, list):
            for x in data:
                try:
                    clean.add(int(x))
                except:
                    continue

        OWNERS = clean
    except Exception as e:
        print(f"[LOAD OWNERS ERROR] {str(e)[:80]}")
        OWNERS = set()


def save_owners():
    try:
        tmp = sorted(set(int(x) for x in OWNERS))
        with open(OWNERS_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2)
    except Exception as e:
        print(f"[SAVE OWNERS ERROR] {str(e)[:80]}")


def is_proot(uid: int) -> bool:
    if not uid:
        return False
    return uid in PROOT_ADMINS


def is_admin(uid: int) -> bool:
    if not uid:
        return False
    return is_owner(uid) or uid in admins


def is_owner(uid: int) -> bool:
    if not uid:
        return False
    return uid == OWNER_ID or uid in globals().get('OWNERS', set()) or is_proot(uid)


# Private execution - no terminal logging


# ================= SAFE EDIT =================

# LOFI UI helpers
def lofi_format(msg: str, title: str = None) -> str:
    """Render a bot-generated worker message in the LOFI GWD visual style."""
    if not msg:
        return ""
    return render_card(msg, title=title)

async def safe_edit(event, text: str):

    if not text:
        return

    # render with LOFI UI
    rendered = lofi_format(text)

    try:
        return await event.edit(rendered)

    except Exception:

        try:
            msg = await event.reply(rendered)
        except:
            return

        try:
            if event.out:
                await event.delete()
        except:
            pass

        return msg


# ================= NOTES STORAGE =================

def load_notes():
    global notes
    try:
        if not os.path.isfile(NOTES_FILE):
            notes = {}
            return

        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        clean = {}

        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    clean[int(k)] = str(v)
                except:
                    continue

        notes = clean

    except Exception as e:
        print(f"[LOAD NOTES ERROR] {str(e)[:80]}")
        notes = {}


def save_notes():
    try:
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SAVE NOTES ERROR] {str(e)[:80]}")


# ================= MENU BANNER STORAGE =================

def load_banner():
    global menu_banner_msg
    try:
        if not os.path.isfile(BANNER_FILE):
            menu_banner_msg = None
            return

        with open(BANNER_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()

        if ":" not in raw:
            menu_banner_msg = None
            return

        chat, msg = raw.split(":", 1)

        menu_banner_msg = (int(chat), int(msg))

    except Exception as e:
        print(f"[LOAD BANNER ERROR] {str(e)[:80]}")
        menu_banner_msg = None


def save_banner():
    try:
        if not menu_banner_msg:
            if os.path.isfile(BANNER_FILE):
                os.remove(BANNER_FILE)
            return

        with open(BANNER_FILE, "w", encoding="utf-8") as f:
            f.write(f"{menu_banner_msg[0]}:{menu_banner_msg[1]}")

    except Exception as e:
        print(f"[SAVE BANNER ERROR] {str(e)[:80]}")


# ───── UNIVERSAL TARGET RESOLVER ─────
async def get_targets(event, arg: str = "") -> Set[int]:

    targets: Set[int] = set()

    # ⭐ reply resolver
    if event.is_reply:
        try:
            reply = await event.get_reply_message()
            if reply and reply.sender_id:
                targets.add(int(reply.sender_id))
        except:
            pass

    # ⭐ argument resolver
    if arg:
        for part in arg.strip().split():

            if not part:
                continue

            if part.isdigit():
                try:
                    targets.add(int(part))
                    continue
                except:
                    pass

            try:
                ent = await bot.get_entity(part)
                if ent and getattr(ent, "id", None):
                    targets.add(int(ent.id))
            except:
                pass

    # ⭐ self protection
    try:
        me = await bot.get_me()
        targets.discard(me.id)
    except:
        pass

    return targets


# ⭐ INITIAL LOAD
load_admins()
try:
    load_owners()
except NameError:
    OWNERS = set()
load_notes()
load_banner()
# ────────────────────────────────────────────────
#                   TEXT LISTS (Unchanged)
# ────────────────────────────────────────────────
reply_list = [
   " 𝐊ʏᴀ 𝐑ᴇ 𝐑ᴀɴᴅɪᴋᴇ 𝐂ᴏᴏʟ 𝐁ᴀɴᴇɢᴀ 𝐓ᴜ 𝐂ʜᴀʟ 𝐀ʙ 𝐂ʜᴜᴅ 𝐀ᴘɴᴇ 𝐁ᴀᴀᴘ 𝐋𝚘ғi̸ 𝐒ᴇ - 🦢💘",
    "𝐊ɪ 𝐌ᴀᴀ 𝐌ᴀʀʀ 𝐆ᴀʏɪ 𝐘ᴀᴀʀ - 𝐉ᴀɪ 𝐋𝚘ғi̸ ! 🌙",
    "acha beta 😂🔥👊🏻 ? coi na me toh HATER codunga 😹💔🔥😆👊🏻💥",
    "chudke bhaga kaise 😂💥🤣🤘🏻",
    "ne toh 𝐋𝚘ғi̸ ka lun muh me lelia 😂🙏🏻😂🙏🏻",
    "try maa सूर्य☀ nikalte hi pel du 😹🔥💔",
    "mkl lun te vaj 😂✊🏻💦",
    "𝗧ᴍᴋ𝗕 pe 𝐋𝚘ғi̸ ka hamla 😂⚔🔥💥",
    "𝐂ʜʟ 𝐇ᴀʀᴍᴢᴀᴅ𝐈 𝐊ᴇ लड़के 💛🤍🩵",
    "oi 𝐓ᴇʀɪ 𝐌‌ᴀᴀ गुलाम ₰🖤",
    "chl rndyce chud ke dikha 😂💥🤣🔥",
    "𝐊ɪ 𝐌ᴀᴀ 𝐌ᴀʀʀ 𝐆ᴀʏɪ naacho 💃🏻💃🏻🕺🏻🎶😂😆💞🔥 !",
    "tera baap bass 𝐋𝚘ғi̸ hai 😂🎀",
    " try maa hagte hue paad mari -#😹🔥🥀",
    "  𝐓ᴇʀɪ 𝐌ᴜᴍᴍʏ 𝐂ʜᴏᴅ 𝐃ɪ 𝐋𝚘ғi̸ 𝐍ᴇ 𝐁ᴡᴀʜᴀʜᴀʜᴀ ⚜",
    "𝐊ʏᴀ 𝐑ᴇ 𝐑ᴀɴᴅɪᴋᴇ 𝐂ᴏᴏʟ 𝐁ᴀɴᴇɢᴀ 𝐓ᴜ 𝐂ʜᴀʟ 𝐀ʙ 𝐂ʜᴜᴅ 𝐀ᴘɴᴇ 𝐁ᴀᴀᴘ 𝐋𝚘ғi̸ 𝐒ᴇ - 🦢💘",
    "𝐊ɪ 𝐌ᴀᴀ 𝐌ᴀʀʀ 𝐆ᴀʏɪ 𝐘ᴀᴀʀ - 𝐉ᴀɪ 𝐋𝚘ғi̸ ! 🌙",
    "acha beta 😂🔥👊🏻 ? coi na me toh HATER codunga 😹💔🔥😆👊🏻💥",
    "chudke bhaga kaise 😂💥🤣🤘🏻",
    "ne toh 𝐋𝚘ғi̸ ka lun muh me lelia 😂🙏🏻😂🙏🏻",
    "try maa सूर्य☀ nikalte hi pel du 😹🔥💔",
    "mkl lun te vaj 😂✊🏻💦",
    "𝗧ᴍᴋ𝗕 pe 𝐋𝚘ғi̸ ka hamla 😂⚔🔥💥",
    "𝐂ʜʟ 𝐇ᴀʀᴍᴢᴀᴅ𝐈 𝐊ᴇ लड़के 💛🤍🩵",
    "oi 𝐓ᴇʀɪ 𝐌‌ᴀᴀ गुलाम ₰🖤",
    "chl rndyce chud ke dikha 😂💥🤣🔥",
    "𝐊ɪ 𝐌ᴀᴀ 𝐌ᴀʀʀ 𝐆ᴀʏɪ naacho 💃🏻💃🏻🕺🏻🎶😂😆💞🔥 !",
    "tera baap bass 𝐋𝚘ғi̸ hai 😂🎀",
    " try maa hagte hue paad mari -#😹🔥🥀",
    "  𝐓ᴇʀɪ 𝐌ᴜᴍᴍʏ 𝐂ʜᴏᴅ 𝐃ɪ 𝐋𝚘ғi̸ 𝐍ᴇ 𝐁ᴡᴀʜᴀʜᴀʜᴀ ⚜",
    ]

reply_texts = [
   "🇺🇾🇨 🇭 🇺 🇵ִֶָ. ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
"सत समुंदर पार  समुद्र 𝘽𝙞𝙘𝙝 𝙢𝙚𝙝 𝙩𝙝_i 𝙣𝙖𝙞𝙮_a पहले तेरी बहन 𝘾_h𝙪_d_e𝙜_i 𝐅_i_r चुदेगी तेरी मैया 😂💔",
"Tmkc pe chppl hi chppl marunga !!re rndce ladce 🔥😂🩴",
"तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi‌ ִֶָ🤍་༘࿐",
"ᥫ᭡.ִֶָ𓂃 ˚˖𓍢ִ໋˚ 𝙏𝙀𝙍𝙔 ꪑꫝꫝ ᵏⁱ 🇨‌🇭‌🇺‌🇹‌ ᶠᵃᵗᵍᵃʸⁱ💔 𝐅ᴇ𝐕ɪ𝐂ᴏ𝐋 𝘴ꫀ ᶜʰⁱᵖᵏᵃ ᵈᵘ➡️💢⋆˚꩜｡ִֶָ. ..𓂃 ࣪ 🦋ִֶָ𓏲ּ𝄢𓍯𓂃</tool_call>",
"तेरी बहन का भोसड़ा 😂🤸🏻‍♂️😂🤸🏻‍♂️😂🤸🏻‍♂️😂🤸🏻‍♂️",
"🛵 𝐀ᴀ 𝐂ʜᴀʟ 𝐁ᴀɪᴛʜ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐂ᴏᴅ 𝐊ᴇ 𝐀ᴛᴇ",
"𝐓ᴇʀI 𝐌ᴀA 𝐊ᴇ 𝐒ᴀ𝑡H 𝐗xx 𝐊ᴀR 𝐃ᴜɴɢA 😜😜🔥🔥😜🔥 𝐓ᴇʀI 𝐌ᴀA 𝐊ᴇ 𝐒ᴀ𝑡H 𝐗xx 𝐊ᴀR 𝐃ᴜɴɢA 😜😜🔥🔥😜🔥",
"🇺🇾🇨 🇭 🇺 🇵ִֶָ. ..𓂃 ࣪ ִִֶֶََ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
"सत समुंदर पार  समुद्र 𝘽𝙞𝙘𝙝 𝙢𝙚𝙝 𝙩𝙝_i 𝙣𝙖𝙞𝙮_a पहले तेरी बहन घ_h𝙪_d_e𝙜_i चुदेगी तेरी मैया 😂💔",
"Tmkc pe chppl hi chppl marunga !!re rndce ladce 🔥😂🩴",
"तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi‌ ִֶָ🤍་༘࿐",
"ᥫ᭡.ִֶָ𓂃 ˚˖𓍢ִ໋˚ 𝙏𝙀𝙍𝙔 ꪑꫝꫝ ᵏⁱ 🇨‌🇭‌🇺‌🇹‌ ᶠᵃᵗᵍᵃʸⁱ💔 𝐅ᴇ𝐕ɪ𝐂ᴏ𝐋ꫀ ᶜʰⁱᵖᵏᵃ ᵈᵘ➡️💢⋆˚꩜｡ִֶَ. ..𓂃 ࣪ 🦋ִَ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 🄰🄳🄲🄔 ̷̷̷̷̷̷̷̷̷̷̷̷̷̷̷̷̷̷̷̷",
"𝐓ᴇʀI 𝐌ᴀA 𝐊ᴇ 𝐒ᴀ𝑡H 𝐗xx 𝐊ᴀR 𝐃ᴜɴɢA 😜😜🔥🔥😜🔥 𝐓ᴇʀI 𝐌ᴀA 𝐊ᴇ 𝐒ᴀ𝑡H 𝐗xx 𝐊ᴀR 𝐃ᴜɴɢA 😜😜🔥🔥😜🔥",
]

fun_texts = [
    "🇺🇾🇨 🇭  Hugo ִֶָ. ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
"सत समुंदर पार  समुद्र 𝘽𝙞𝙘𝙝 𝙢𝙚𝙝 𝙩𝙝_i 𝙣𝙖𝙞𝙮_a पहले तेरी बहन 𝘾_h𝙪_d_e𝙜_i 𝐅_i_r चुदेगी तेरी मैया 😂💔",
"Tmkc pe chppl hi chppl marunga !!re rndce ladce 🔥😂🩴",
"तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi‌ ִֶָ🤍་༘࿐",
"ᥫ᭡.ִֶָ𓂃 ˚˖𓍢ִ໋˚ 𝙏𝙀𝙍𝙔 ꪑꫝꫝ ᵏⁱ 🇨‌🇭‌🇺‌🇹‌ ᶠᵃᵗᵍᵃʸⁱ💔 𝐅ᴇ𝐕ɪ𝐂ᴏ𝐋 𝘴ꫀ ᶜʰⁱᵖᵏᵃ ᵈᵘ➡️💢⋆˚꩜｡ִֶָ. ..𓂃 ࣪ 🦋ִֶָ𓏲ּ𝄢𓍯𓂃</tool_call>",
"तेरी बहन का भोसड़ा 😂🤸🏻‍♂️😂🤸🏻‍♂️😂🤸🏻‍♂️😂🤸🏻‍♂️",
"🛵 𝐀ᴀ 𝐂ʜᴀʟ 𝐁ᴀɪᴛʜ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐂ᴏᴅ 𝐊ᴇ 𝐀ᴛᴇ",
"𝐓ᴇʀI 𝐌ᴀA 𝐊ᴇ 𝐒ᴀ𝑡H 𝐗xx 𝐊ᴀR 𝐃ᴜɴɢA 😜😜🔥🔥😜🔥 𝐓ᴇʀI 𝐌ᴀA 𝐊ᴇ 𝐒ᴀ𝑡H 𝐗xx 𝐊ᴀR 𝐃ᴜɴɢA 😜😜🔥🔥😜🔥",
]

flag_texts = [
    "🇺🇾🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
    "🇩🇿🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇩🇿.",
    "🇾🇹🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇾🇹.",
     "🇺🇾🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
    "🇩🇿🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇩🇿.",
    "🇾🇹🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇾🇹.",
     "🇺🇾🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
    "🇩🇿🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇩🇿.",
    "🇾🇹🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇾🇹.",
     "🇺🇾🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
    "🇩🇿🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇩🇿.",
    "🇾🇹🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇾🇹.",
     "🇺🇾🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
    "🇩🇿🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇩🇿.",
    "🇾🇹🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇾🇹.",
     "🇺🇾🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇺🇾",
    "🇩🇿🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇩🇿.",
    "🇾🇹🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇾🇹.",
    "🇪🇭🇨 🇭 . ..𓂃 ࣪ ִִֶֶָָ. ..𓂃   𝗧𝗠𝗞𝗕 𝐑𝐍𝐃𝐂𝐄 𝙇𝘼𝘿𝘾𝙀 ִֶָ་༘࿐🇪🇭.",
]

heart_replies = [
    "💜 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💜་༘࿐.",
    "🤎तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶ🤎་༘࿐.",
    "💛तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💛་༘࿐.",
    "💚 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💚་༘࿐.",
    "💙 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💙་༘࿐.",
     "💜 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💜་༘࿐.",
    "🤎तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶ🤎་༘࿐.",
    "💛तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💛་༘࿐.",
    "💚 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💚་༘࿐.",
    "💙 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💙་༘࿐.",
     "💜 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💜་༘࿐.",
    "🤎तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶ🤎་༘࿐.",
    "💛तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💛་༘࿐.",
    "💚 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💚་༘࿐.",
    "💙 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💙་༘࿐.",
     "💜 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💜་༘࿐.",
    "🤎तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶ🤎་༘࿐.",
    "💛तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💛་༘࿐.",
    "💚 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💚་༘࿐.",
    "💙 तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ💙་༘࿐.",
    "🤍तेरी मां की बुर कोन्से 𝐂ᴏꪶꪮᴜʀ ᴋi̸ ִֶָ🤍་༘࿐.",
]

# ────────────────────────────────────────────────
#              ꧁𓊈𒆜DECORATOR𒆜𓊉꧂
# ────────────────────────────────────────────────
# ================= COMMAND REGISTRY =================

commands = {}

def register_cmd(name: str, needs_reply: bool = False, group_only: bool = False):

    def decorator(func):

        key = (name or "").lower().strip()

        if not key:
            raise ValueError("Command name cannot be empty")

        if key in commands:
            raise ValueError(f"Duplicate command registered: {key}")

        commands[key] = {
            "func": func,
            "needs_reply": bool(needs_reply),
            "group_only": bool(group_only),
        }

        return func

    return decorator


# ────────────────────────────────────────────────
#                   MENU (STYLISH) - UNCHANGED
# ────────────────────────────────────────────────
@register_cmd("rn")
async def cmd_auto_reply(event, arg):
    global auto_reply_enabled, auto_reply_text, auto_reply_target_user_id

    replied_message = None
    get_reply_message = getattr(event, "get_reply_message", None)
    if callable(get_reply_message):
        replied_message = await get_reply_message()
    if replied_message is not None:
        target_user_id = getattr(replied_message, "sender_id", None)
        if not isinstance(target_user_id, int) or target_user_id <= 0:
            return await safe_edit(event, "❌ Reply to a user's message to select the target.")
    elif auto_reply_target_user_id is None:
        return await safe_edit(event, "❌ Reply to a user's message to select the target.")

    text = arg.strip()
    if text:
        if len(text.encode("utf-16-le")) // 2 > 4096:
            return await safe_edit(event, "❌ Auto-reply text must be 4096 characters or fewer.")
        save_reply_text(text)
        auto_reply_text = text
    elif not auto_reply_text:
        auto_reply_text = load_reply_text()

    if not auto_reply_text:
        return await safe_edit(
            event,
            "❌ No saved reply text. Set it with `~rn <text>` first.",
        )

    if replied_message is not None:
        auto_reply_target_user_id = target_user_id
    auto_reply_enabled = True
    await safe_edit(event, "✅ Global auto-reply is on.")


@register_cmd("stoprn")
async def cmd_stop_auto_reply(event, _):
    global auto_reply_enabled
    auto_reply_enabled = False
    await safe_edit(event, "✅ Global auto-reply is off. Your saved text was kept.")


@register_cmd("menu")
@register_cmd("lmenu")
async def cmd_menu(event, _):
    global menu_banner_msg
    await send_worker_menu(event, event.sender_id or 0)
    
    if menu_banner_msg:
        chat_id, msg_id = menu_banner_msg
        try:
            msg = await bot.get_messages(chat_id, ids=msg_id)
            await bot.send_file(
                event.chat_id,
                file=msg.media,
                caption=lofi_format("LOFI VOID CORE 𝐄ɴᴛᴇʀs ❤️‍🔥")
            )
        except:
            pass
#                   COMMANDS
# ────────────────────────────────────────────────

# Banner
@register_cmd("lbanner", needs_reply=True)
async def cmd_banner(event, _):
    global menu_banner_msg

    reply = await event.get_reply_message()

    # ─── MEDIA VALIDATION ENGINE ───
    if not reply:
        return await safe_edit(
            event,
            "❌ 𝐍ᴏ 𝐑ᴇᴘʟʏ\n👉 𝐑ᴇᴘʟʏ 𝐓ᴏ 𝐏ʜᴏᴛᴏ / 𝐕ɪᴅᴇᴏ"
        )

    if not reply.media:
        return await safe_edit(
            event,
            "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐌ᴇᴅɪᴀ\n👉 𝐎ɴʟʏ 𝐏ʜᴏᴛᴏ / 𝐕ɪᴅᴇᴏ"
        )

    await safe_edit(
        event,
        "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐁ᴀɴɴᴇʀ...\n━━━━━━━━━━━━━━━"
    )

    try:
        # ─── FORWARD TRY FIRST (FAST PATH) ───
        try:
            saved = await reply.forward_to("me")
        except Exception:
            # ⭐ FORWARD RESTRICTED FALLBACK
            file = await reply.download_media(file=bytes)
            if not file:
                return await safe_edit(event,
                    "❌ 𝐌ᴇᴅɪᴀ 𝐃ᴏᴡɴʟᴏᴀᴅ 𝐅ᴀɪʟ"
                )

            bio = BytesIO(file)
            bio.name = "banner"
            saved = await bot.send_file("me", bio)

        menu_banner_msg = (saved.chat_id, saved.id)
        save_banner()

        text = (
            "🖼️ 𝐁ᴀɴɴᴇʀ 𝐒ᴇᴛ\n"
            "━━━━━━━━━━━━━━━\n"
            f"📌 𝐒ᴀᴠᴇᴅ 𝐈ᴅ → `{saved.id}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ 𝐖ᴀɪᴛ → {fw.seconds}s"
        )

    except RPCError as e:
        await safe_edit(event,
            f"❌ 𝐓ɢ 𝐄ʀʀ → `{str(e)[:40]}`"
        )

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐁ᴀɴɴᴇʀ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("lrembanner")
async def cmd_rembanner(event, _):
    global menu_banner_msg

    if not menu_banner_msg:
        return await safe_edit(event,
            "⚠️ 𝐍ᴏ 𝐁ᴀɴɴᴇʀ 𝐒ᴇᴛ"
        )

    await safe_edit(
        event,
        "⚡ 𝐑ᴇᴍᴏᴠɪɴɢ 𝐁ᴀɴɴᴇʀ...\n━━━━━━━━━━━━━━━"
    )

    try:
        chat_id, msg_id = menu_banner_msg

        try:
            await bot.delete_messages(chat_id, [msg_id])
        except Exception:
            # ⭐ banner already manually deleted
            pass

        menu_banner_msg = None
        save_banner()

        text = (
            "🗑️ 𝐁ᴀɴɴᴇʀ 𝐑ᴇᴍᴏᴠᴇᴅ\n"
            "━━━━━━━━━━━━━━━"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )

    except RPCError as e:
        await safe_edit(event,
            f"❌ 𝐓ɢ 𝐄ʀʀ → `{str(e)[:40]}`"
        )

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐑ᴇᴍᴏᴠᴇ 𝐄ʀʀ → `{str(e)[:50]}`"
        )
        
# Admin Commands (Updated)
@register_cmd("laddadmin", needs_reply=True)
async def cmd_addadmin(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ\n👉 𝐑ᴇᴘʟʏ / @username / ID"
            )

        await safe_edit(
            event,
            "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐀ᴅᴍɪɴ 𝐀ᴅᴅ...\n━━━━━━━━━━━━━━━"
        )

        added = []
        already = []
        skipped_owner = []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == OWNER_ID:
                skipped_owner.append(str(uid))
                continue

            if uid in admins:
                already.append(str(uid))
            else:
                admins.add(uid)
                added.append(str(uid))

        try:
            save_admins()
        except Exception:
            pass

        parts = []

        if added:
            parts.append(
                f"✅ 𝐀ᴅᴍɪɴ 𝐀ᴅᴅᴇᴅ → `{', '.join(added)}`"
            )

        if already:
            parts.append(
                f"⚠️ 𝐀ʟʀᴇᴀᴅʏ 𝐀ᴅᴍɪɴ → `{', '.join(already)}`"
            )

        if skipped_owner:
            parts.append(
                f"👑 𝐎ᴡɴᴇʀ 𝐒ᴋɪᴘᴘᴇᴅ → `{', '.join(skipped_owner)}`"
            )

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        text = "\n".join(parts)

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except RPCError as e:
        await safe_edit(event,
            f"❌ 𝐓ɢ 𝐄ʀʀ → `{str(e)[:40]}`"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴍɪɴ 𝐀ᴅᴅ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("ldeladmin", needs_reply=True)
async def cmd_deladmin(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ\n👉 𝐑ᴇᴘʟʏ / @username / ID"
            )

        await safe_edit(
            event,
            "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇ...\n━━━━━━━━━━━━━━━"
        )

        removed = []
        not_admin = []
        skipped_owner = []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == OWNER_ID:
                skipped_owner.append(str(uid))
                continue

            if uid in admins:
                admins.remove(uid)
                removed.append(str(uid))
            else:
                not_admin.append(str(uid))

        try:
            save_admins()
        except Exception:
            pass

        parts = []

        if removed:
            parts.append(
                f"🗑️ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇᴅ → `{', '.join(removed)}`"
            )

        if not_admin:
            parts.append(
                f"⚠️ 𝐍ᴏᴛ 𝐀ᴅᴍɪɴ → `{', '.join(not_admin)}`"
            )

        if skipped_owner:
            parts.append(
                f"👑 𝐎ᴡɴᴇʀ 𝐏ʀᴏᴛᴇᴄᴛᴇᴅ → `{', '.join(skipped_owner)}`"
            )

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        text = "\n".join(parts)

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except RPCError as e:
        await safe_edit(event,
            f"❌ 𝐓ɢ 𝐄ʀʀ → `{str(e)[:40]}`"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("ladmins")
async def cmd_admins(event, _):
    try:
        await safe_edit(
            event,
            "⚡ 𝐅ᴇᴛᴄʜɪɴɢ 𝐀ᴅᴍɪɴ 𝐋ɪsᴛ...\n━━━━━━━━━━━━━━━"
        )

        if admins:
            admin_list = "\n".join(
                f"• `{a}`" for a in sorted(admins)
            )
        else:
            admin_list = "⚠️ 𝐍ᴏ 𝐄xᴛʀᴀ 𝐀ᴅᴍɪɴs"

        txt = (
            "👑 𝐀ᴅᴍɪɴ 𝐋ɪsᴛ\n"
            "━━━━━━━━━━━━━━━\n"
            f"👑 𝐎ᴡɴᴇʀ → `{OWNER_ID}`\n\n"
            f"{admin_list}\n\n"
            f"📊 𝐓ᴏᴛᴀʟ → `{len(admins)}`"
        )

        await safe_edit(event, txt)

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴍɪɴ 𝐋ɪsᴛ 𝐄ʀʀ → `{str(e)[:50]}`"
        )

# ────────────────────────────────────────────────
#              ROOT / SUPER ADMIN COMMANDS
# ────────────────────────────────────────────────
@register_cmd("help")
async def cmd_proot_help(event, _):

    if LOFI_HOSTER_WORKER:
        await send_worker_menu(event, event.sender_id or 0)
        return

    menu_text = (
        "╭─〔 † SUPER ADMIN 〕─╮\n"
        "│ †whoami\n"
        "│ †addadmin\n"
        "│ †removeadmin\n"
        "│ †addowner\n"
        "│ †removeowner\n"
        "╰──────────────────╯"
    )

    try:
        await safe_edit(event, menu_text)
    except:
        pass


@register_cmd("whoami")
async def cmd_proot_whoami(event, _):
    if LOFI_HOSTER_WORKER:
        me = await bot.get_me()
        message = f"This account is signed in as {me.id}."
        await safe_edit(event, message)
        return

    message = "You are the God of Lofi. Welcome, Lofi Papa. 🦢🌸"
    try:
        await safe_edit(event, message)
    except:
        pass


@register_cmd("addadmin")
async def cmd_proot_addadmin(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ\n👉 𝐑ᴇᴘʟʏ / @username / ID"
            )

        added = []
        already = []
        skipped = []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == OWNER_ID or is_proot(uid):
                skipped.append(str(uid))
                continue

            if uid in admins:
                already.append(str(uid))
            else:
                admins.add(uid)
                added.append(str(uid))

        try:
            save_admins()
        except Exception:
            pass

        parts = []
        if added:
            parts.append(f"✅ 𝐀ᴅᴍɪɴ 𝐀ᴅᴅᴇᴅ → `{', '.join(added)}`")
        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ 𝐀ᴅᴍɪɴ → `{', '.join(already)}`")
        if skipped:
            parts.append(f"👑 𝐎ᴡɴᴇʀ/𝐏ʀᴏᴏᴛ 𝐒ᴋɪᴘᴘᴇᴅ → `{', '.join(skipped)}`")
        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        await safe_edit(event, "\n".join(parts))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴅ 𝐀ᴅᴍɪɴ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("removeadmin")
async def cmd_proot_removeadmin(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ\n👉 𝐑ᴇᴘʟʏ / @username / ID"
            )

        removed = []
        not_admin = []
        skipped = []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == OWNER_ID or is_proot(uid):
                skipped.append(str(uid))
                continue

            if uid in admins:
                admins.remove(uid)
                removed.append(str(uid))
            else:
                not_admin.append(str(uid))

        try:
            save_admins()
        except Exception:
            pass

        parts = []
        if removed:
            parts.append(f"🗑️ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇᴅ → `{', '.join(removed)}`")
        if not_admin:
            parts.append(f"⚠️ 𝐍ᴏᴛ 𝐀ᴅᴍɪɴ → `{', '.join(not_admin)}`")
        if skipped:
            parts.append(f"👑 𝐎ᴡɴᴇʀ/𝐏ʀᴏᴏᴛ 𝐏ʀᴏᴛᴇᴄᴛᴇᴅ → `{', '.join(skipped)}`")
        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        await safe_edit(event, "\n".join(parts))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇ 𝐄ʀʀ → `{str(e)[:50]}`"
        )
        if removed:
            parts.append(f"🗑️ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇᴅ → `{', '.join(removed)}`")
        if not_admin:
            parts.append(f"⚠️ 𝐍ᴏᴛ 𝐀ᴅᴍɪɴ → `{', '.join(not_admin)}`")
        if skipped:
            parts.append(f"👑 𝐎ᴡɴᴇʀ/𝐑ᴏᴏᴛ 𝐏ʀᴏᴛᴇᴄᴛᴇᴅ → `{', '.join(skipped)}`")
        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        await safe_edit(event, "\n".join(parts))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴍɪɴ 𝐑ᴇᴍᴏᴠᴇ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("addowner")
async def cmd_proot_addowner(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ\n👉 𝐑ᴇᴘʟʏ / @username / ID"
            )

        added = []
        already = []
        skipped = []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == OWNER_ID or is_proot(uid):
                skipped.append(str(uid))
                continue

            if uid in globals().get('OWNERS', set()):
                already.append(str(uid))
            else:
                globals().setdefault('OWNERS', set()).add(uid)
                added.append(str(uid))

        try:
            save_owners()
        except Exception:
            pass

        parts = []
        if added:
            parts.append(f"✅ 𝐎ᴡɴᴇʀ 𝐀ᴅᴅᴇᴅ → `{', '.join(added)}`")
        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ 𝐎ᴡɴᴇʀ → `{', '.join(already)}`")
        if skipped:
            parts.append(f"👑 𝐎ᴡɴᴇʀ/𝐏ʀᴏᴏᴛ 𝐒ᴋɪᴘᴘᴇᴅ → `{', '.join(skipped)}`")
        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        await safe_edit(event, "\n".join(parts))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀ᴅᴅ 𝐎ᴡɴᴇʀ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("removeowner")
async def cmd_proot_removeowner(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ\n👉 𝐑ᴇᴘʟʏ / @username / ID"
            )

        removed = []
        not_owner = []
        skipped = []

        owners_set = globals().get('OWNERS', set())

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == OWNER_ID or is_proot(uid):
                skipped.append(str(uid))
                continue

            if uid in owners_set:
                owners_set.remove(uid)
                removed.append(str(uid))
            else:
                not_owner.append(str(uid))

        try:
            save_owners()
        except Exception:
            pass

        parts = []
        if removed:
            parts.append(f"🗑️ 𝐎ᴡɴᴇʀ 𝐑ᴇᴍᴏᴠᴇᴅ → `{', '.join(removed)}`")
        if not_owner:
            parts.append(f"⚠️ 𝐍ᴏᴛ 𝐎ᴡɴᴇʀ → `{', '.join(not_owner)}`")
        if skipped:
            parts.append(f"👑 𝐎ᴡɴᴇʀ/𝐏ʀᴏᴏᴛ 𝐏ʀᴏᴛᴇᴄᴛᴇᴅ → `{', '.join(skipped)}`")
        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇs 𝐌ᴀᴅᴇ")

        await safe_edit(event, "\n".join(parts))

    except FloodWaitError as fw:
        await safe_edit(event,
            f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s"
        )
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐎ᴡɴᴇʀ 𝐑ᴇᴍᴏᴠᴇ 𝐄ʀʀ → `{str(e)[:50]}`"
        )


# Basic Commands (Unchanged)
@register_cmd("ping")
@register_cmd("lping")
async def cmd_ping(event, _):
    try:
        t0 = time.perf_counter()
        try:
            if event.out:
                msg = await event.edit(lofi_format("🏓 𝐏ɪɴɢ..."))
            else:
                msg = await event.reply(lofi_format("🏓 𝐏ɪɴɢ..."))
        except:
            msg = None

        t1 = time.perf_counter()
        ms = round((t1 - t0) * 1000)

        # ─── RESPONSE EDIT SAFE ───
        try:
            if msg:
                await msg.edit(lofi_format(f"🏓 𝐏ᴏɴɢ → `{ms} ms`"))
            else:
                await event.reply(lofi_format(f"🏓 𝐏ᴏɴɢ → `{ms} ms`"))
        except:
            pass

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐏ɪɴɢ 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("status")
@register_cmd("lstatus")
async def cmd_status(event, _):
    try:
        now = time.time()

        # ─── START TIME CORRUPTION SAFE ───
        try:
            uptime = int(now - START_TIME)
            if uptime < 0:
                uptime = 0
        except:
            uptime = 0

        # ─── ADMIN STORAGE SAFE ───
        try:
            admin_count = len(admins)
        except:
            admin_count = 0

        txt = (
            "✅ 𝐔sᴇʀʙᴏᴛ 𝐒ᴛᴀᴛᴜs\n"
            "━━━━━━━━━━━━━━━\n"
            f"⏱️ 𝐔ᴘᴛɪᴍᴇ → `{uptime}s`\n"
            f"👑 𝐀ᴅᴍɪɴs → `{admin_count}`\n"
            f"⚙️ 𝐌ᴏᴅᴇ → `Operational`"
        )

        try:
            if event.out:
                await safe_edit(event, txt)
            else:
                await event.reply(lofi_format(txt))
        except:
            pass

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐒ᴛᴀᴛᴜs 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lflip")
async def cmd_flip(event, _):
    try:
        # ─── RANDOM SAFE ───
        try:
            result = random.choice(["🪙 𝐇ᴇᴀᴅs", "🪙 𝐓ᴀɪʟs"])
        except:
            result = "🪙 𝐇ᴇᴀᴅs"

        text = (
            "🎲 𝐂ᴏɪɴ 𝐅ʟɪᴘ\n"
            "━━━━━━━━━━━━━━━\n"
            f"👉 𝐑ᴇsᴜʟᴛ → {result}"
        )

        try:
            if event.out:
                await safe_edit(event, text)
            else:
                await event.reply(lofi_format(text))
        except:
            pass

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐅ʟɪᴘ 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("ldice")
async def cmd_dice(event, _):
    try:
        # ─── RANDOM SAFE ───
        try:
            num = random.randint(1, 6)
        except:
            num = 1

        text = (
            "🎲 𝐃ɪᴄᴇ 𝐑ᴏʟʟ\n"
            "━━━━━━━━━━━━━━━\n"
            f"👉 𝐑ᴇsᴜʟᴛ → `{num}`"
        )

        try:
            if event.out:
                await safe_edit(event, text)
            else:
                await event.reply(lofi_format(text))
        except:
            pass

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐃ɪᴄᴇ 𝐄ʀʀ → `{str(e)[:40]}`")

@register_cmd("lrr", needs_reply=True)
async def cmd_rr(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ"
            )

        await safe_edit(event,
            "⚡ 𝐄ɴᴀʙʟɪɴɢ 𝐑𝐑 𝐑ᴀɪᴅ (𝐆ʟᴏʙᴀʟ)...\n━━━━━━━━━━━━━━━"
        )

        added, already = [], []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in rr_users:
                already.append(str(uid))
            else:
                rr_users.add(uid)
                added.append(str(uid))

        parts = []

        if added:
            parts.append(f"⚡ 𝐑𝐑 𝐎ɴ → `{', '.join(added)}`")

        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ → `{', '.join(already)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐑𝐑 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lflag", needs_reply=True)
async def cmd_flag(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ"
            )

        await safe_edit(event,
            "⚡ 𝐄ɴᴀʙʟɪɴɢ 𝐅ʟᴀɢ 𝐑ᴀɪᴅ (𝐆ʟᴏʙᴀʟ)...\n━━━━━━━━━━━━━━━"
        )

        added, already = [], []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in flag_users:
                already.append(str(uid))
            else:
                flag_users.add(uid)
                added.append(str(uid))

        parts = []

        if added:
            parts.append(f"🌊 𝐅ʟᴀɢ 𝐎ɴ → `{', '.join(added)}`")

        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ → `{', '.join(already)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐅ʟᴀɢ 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lhrr", needs_reply=True)
async def cmd_hrr(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(event,
                "❌ 𝐍ᴏ 𝐕ᴀʟɪᴅ 𝐓ᴀʀɢᴇᴛ"
            )

        await safe_edit(event,
            "⚡ 𝐄ɴᴀʙʟɪɴɢ 𝐇ᴇᴀʀᴛ 𝐑ᴀɪᴅ (𝐆ʟᴏʙᴀʟ)...\n━━━━━━━━━━━━━━━"
        )

        added, already = [], []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in hrr_users:
                already.append(str(uid))
            else:
                hrr_users.add(uid)
                added.append(str(uid))

        parts = []

        if added:
            parts.append(f"💜 𝐇ᴇᴀʀᴛ 𝐑ᴀɪᴅ → `{', '.join(added)}`")

        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ → `{', '.join(already)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐇𝐑𝐑 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lspray")
async def cmd_spray(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴇxᴛ 𝐆ɪᴠᴇɴ\n"
                "👉 𝐔sᴀɢᴇ: `~spray <text>`"
            )

        chat = event.chat_id

        # ─── HUGE TEXT GUARD ───
        if len(arg) > 4000:
            arg = arg[:4000]

        # ─── ALREADY ACTIVE GUARD ───
        if chat in spray_tasks and not spray_tasks[chat].done():
            msg = "⚠️ 𝐒ᴘʀᴀʏ 𝐀ʟʀᴇᴀᴅʏ 𝐀ᴄᴛɪᴠᴇ"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐈ɴɪᴛɪᴀʟɪᴢɪɴɢ 𝐒ᴘʀᴀʏ...\n━━━━━━━━━━━━━━━"
        )

        async def spray_loop():
            try:
                while True:
                    # stop condition
                    if chat not in spray_tasks:
                        break

                    try:
                        await bot.send_message(chat, arg)
                    except FloodWaitError as fw:
                        await asyncio.sleep(fw.seconds)
                    except RPCError:
                        # permission lost / banned / readonly
                        spray_tasks.pop(chat, None)
                        break
                    except Exception:
                        # network / msg send fail
                        await asyncio.sleep(2)

                    await asyncio.sleep(SPRAY_DELAY)

            except asyncio.CancelledError:
                pass
            finally:
                spray_tasks.pop(chat, None)

        spray_tasks[chat] = asyncio.create_task(spray_loop())

        text = (
            "💣 𝐒ᴘʀᴀʏ 𝐒ᴛᴀʀᴛᴇᴅ\n"
            "━━━━━━━━━━━━━━━\n"
            f"📢 𝐓ᴇxᴛ → `{arg[:40]}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒ᴘʀᴀʏ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("rsm")
async def cmd_rsm(event, arg):
    try:
        text, delay_text = arg.rsplit(maxsplit=1)
        delay = float(delay_text)
    except (ValueError, TypeError):
        return await safe_edit(event, "Usage: `~rsm <text> <delay-seconds>`")

    if not text.strip() or not math.isfinite(delay):
        return await safe_edit(event, "Usage: `~rsm <text> <delay-seconds>`")
    if not RSM_MIN_DELAY_SECONDS <= delay <= RSM_MAX_DELAY_SECONDS:
        return await safe_edit(
            event,
            f"Delay must be between {RSM_MIN_DELAY_SECONDS:g} and "
            f"{RSM_MAX_DELAY_SECONDS:g} seconds.",
        )

    max_text_units = 4096 // 40 - 3
    if len(text.encode("utf-16-le")) // 2 > max_text_units:
        return await safe_edit(
            event,
            f"Text must be {max_text_units} UTF-16 characters or fewer.",
        )

    chat = event.chat_id
    if chat is None:
        return await safe_edit(event, "Cannot start a pattern task without a chat.")
    active_task = rsm_tasks.get(chat)
    if active_task is not None and not active_task.done():
        return await safe_edit(event, "A pattern task is already active in this chat.")

    repeated_message = random.choice(_PATTERNS).format(text=text)

    async def rsm_loop() -> None:
        task = asyncio.current_task()
        try:
            while True:
                if rsm_tasks.get(chat) is not task:
                    return
                try:
                    await bot.send_message(chat, repeated_message)
                except FloodWaitError as error:
                    print(f"[RSM_FLOOD_WAIT] chat={chat} seconds={error.seconds}")
                    await asyncio.sleep(max(delay, float(error.seconds)))
                    continue
                except (RPCError, OSError) as error:
                    print(f"[RSM_ERROR] chat={chat} error_type={type(error).__name__}")
                    await asyncio.sleep(delay)
                    continue
                except Exception as error:
                    print(f"[RSM_ERROR] chat={chat} error_type={type(error).__name__}")
                    await asyncio.sleep(delay)
                    continue
                await asyncio.sleep(delay)
        finally:
            if rsm_tasks.get(chat) is task:
                rsm_tasks.pop(chat, None)

    rsm_tasks[chat] = asyncio.create_task(rsm_loop())
    await safe_edit(
        event,
        f"Pattern task started and will repeat every {delay:g}s until stopped. "
        "Use `~stoprsm` to stop it.",
    )


@register_cmd("stoprsm")
async def cmd_stop_rsm(event, _):
    chat = event.chat_id
    task = rsm_tasks.get(chat)
    if task is None or task.done():
        return await safe_edit(event, "No active pattern task in this chat.")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await safe_edit(event, "Pattern task stopped.")


async def _timer_send_message(chat_id: int, delay_seconds: float, text: str, entry: dict):
    print(f"[TIMER] Created for chat_id={chat_id} delay={delay_seconds:.1f}s now={datetime.now().strftime('%H:%M:%S')}")
    print(f"[TIMER] Loop: {asyncio.get_running_loop()}")

    try:
        if delay_seconds > 0:
            print(f"[TIMER] Waiting started for chat_id={chat_id} for {delay_seconds:.1f}s")
            await asyncio.sleep(delay_seconds)

        print(f"[TIMER] Time reached for chat_id={chat_id} at {datetime.now().strftime('%H:%M:%S')}")
        print(f"[TIMER] Sending message for chat_id={chat_id}")
        await bot.send_message(chat_id, text, parse_mode=None)
        print(f"[TIMER] Message sent for chat_id={chat_id}")

    except Exception:
        print(f"[TIMER] Exception while sending to chat_id={chat_id}")
        traceback.print_exc()

    finally:
        if chat_id in scheduled_messages and entry in scheduled_messages[chat_id]:
            try:
                scheduled_messages[chat_id].remove(entry)
            except ValueError:
                pass
            if not scheduled_messages[chat_id]:
                scheduled_messages.pop(chat_id, None)


@register_cmd("send")
async def cmd_send(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐌ɪssɪɴɢ 𝐓ɪᴍᴇ 𝐨ʀ 𝐌𝐞𝐬sᴀɢᴇ\n"
                "👉 𝐔sᴀɢᴇ: `~gwdschedule HH:MM:SS <text>` (duration)"
            )

        parts = arg.strip().split(maxsplit=1)
        if len(parts) < 2:
            return await safe_edit(
                event,
                "❌ 𝐌ɪssɪɴɢ 𝐌𝐞ssᴀɢᴇ\n"
                "👉 𝐔sᴀɢᴇ: `~gwdschedule HH:MM:SS <text>`"
            )

        time_part, message_text = parts

        # accept HH:MM:SS or MM:SS
        parts_t = time_part.split(":")
        if len(parts_t) == 3:
            hour = int(parts_t[0])
            minute = int(parts_t[1])
            second = int(parts_t[2])
        elif len(parts_t) == 2:
            hour = 0
            minute = int(parts_t[0])
            second = int(parts_t[1])
        else:
            return await safe_edit(
                event,
                "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐃ᴜʀᴀᴛɪᴏɴ\n"
                "👉 𝐔sᴀɢᴇ: `~gwdschedule HH:MM:SS <text>`"
            )

        if minute < 0 or minute > 59 or second < 0 or second > 59 or hour < 0:
            return await safe_edit(
                event,
                "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐃ᴜʀᴀᴛɪᴏɴ\n"
                "👉 𝐔sᴀɢᴇ: `~gwdschedule HH:MM:SS <text>`"
            )

        total_seconds = hour * 3600 + minute * 60 + second
        if total_seconds <= 0:
            return await safe_edit(
                event,
                "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐃ᴜʀᴀᴛɪᴏɴ\n"
                "👉 𝐔sᴀɢᴇ: `~gwdschedule HH:MM:SS <text>`"
            )

        print(f"[TIMER] Schedule created for chat_id={event.chat_id} delay={total_seconds}s ({time_part}) now={datetime.now().strftime('%H:%M:%S')}")

        chat = event.chat_id
        entry = {
            "delay": total_seconds,
            "text": message_text,
        }
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _timer_send_message(chat, float(total_seconds), message_text, entry)
        )
        entry["task"] = task

        def _on_task_done(t):
            print(f"[TIMER] Task done callback for chat_id={chat} task={t} done={t.done()} exception={t.exception()}")
            if t.exception():
                traceback.print_exception(type(t.exception()), t.exception(), t.exception().__traceback__)

        task.add_done_callback(_on_task_done)

        print(f"[TIMER] Task created for chat_id={chat} task={task} done={task.done()}")

        scheduled_messages.setdefault(chat, []).append(entry)

        return await safe_edit(
            event,
            f"✅ Message scheduled after {time_part}"
        )

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒𝐞ɴᴅ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("ldspray")
async def cmd_dspray(event, _):
    try:
        chat = event.chat_id

        if chat not in spray_tasks:
            msg = "⚠️ 𝐍ᴏ 𝐀ᴄᴛɪᴠᴇ 𝐒ᴘʀᴀʏ"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐒ᴛᴏᴘᴘɪɴɢ 𝐒ᴘʀᴀʏ...\n━━━━━━━━━━━━━━━"
        )

        try:
            spray_tasks[chat].cancel()
        except:
            pass

        spray_tasks.pop(chat, None)

        text = (
            "🛑 𝐒ᴘʀᴀʏ 𝐒ᴛᴏᴘᴘᴇᴅ\n"
            "━━━━━━━━━━━━━━━"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐃𝐒ᴘʀᴀʏ 𝐄ʀʀ → `{str(e)[:40]}`"
        )

# Mute Commands
@register_cmd("lmute", needs_reply=True)
async def cmd_mute(event, arg):
    try:
        try:
            permissions = await bot.get_permissions(event.chat_id, "me")
        except Exception:
            return await safe_edit(event, "❌ Could not verify delete-message permission.")
        if not local_mute_allowed(event.is_group, permissions):
            return await safe_edit(event, "❌ I need group admin permission to delete messages.")

        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴀʀɢᴇᴛ\n👉 `~mute` (reply / @username / id)"
            )

        await safe_edit(
            event,
            "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐌ᴜᴛᴇ...\n━━━━━━━━━━━━━━━"
        )

        added, already = [], []
        chat_muted = muted_users.setdefault(event.chat_id, set())

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in chat_muted:
                already.append(str(uid))
            else:
                chat_muted.add(uid)
                added.append(str(uid))

        parts = []

        if added:
            parts.append(f"🔇 𝐌ᴜᴛᴇ → `{', '.join(added)}`")

        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ 𝐌ᴜᴛᴇᴅ → `{', '.join(already)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐌ᴜᴛᴇ 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lunmute", needs_reply=True)
async def cmd_unmute(event, arg):
    try:
        if not event.is_group:
            return await safe_edit(event, "❌ Local unmute is available only in groups.")

        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴀʀɢᴇᴛ\n👉 `~unmute` (reply / @username / id)"
            )

        await safe_edit(
            event,
            "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐔ɴᴍᴜᴛᴇ...\n━━━━━━━━━━━━━━━"
        )

        removed, not_muted = [], []
        chat_muted = muted_users.get(event.chat_id, set())

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in chat_muted:
                chat_muted.remove(uid)
                removed.append(str(uid))
            else:
                not_muted.append(str(uid))

        parts = []

        if removed:
            parts.append(f"🗣️ 𝐔ɴᴍᴜᴛᴇ → `{', '.join(removed)}`")

        if not_muted:
            parts.append(f"⚠️ 𝐍ᴏᴛ 𝐌ᴜᴛᴇᴅ → `{', '.join(not_muted)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if not chat_muted:
            muted_users.pop(event.chat_id, None)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐔ɴᴍᴜᴛᴇ 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lgmute", needs_reply=True)
async def cmd_gmute(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴀʀɢᴇᴛ\n👉 `~gmute` (reply / @username / id)"
            )

        await safe_edit(
            event,
            "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐆ᴍᴜᴛᴇ...\n━━━━━━━━━━━━━━━"
        )

        added, already = [], []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in global_muted:
                already.append(str(uid))
            else:
                global_muted.add(uid)
                added.append(str(uid))

        parts = []

        if added:
            parts.append(f"🔕 𝐆ᴍᴜᴛᴇ → `{', '.join(added)}`")

        if already:
            parts.append(f"⚠️ 𝐀ʟʀᴇᴀᴅʏ 𝐆ᴍᴜᴛᴇᴅ → `{', '.join(already)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐆ᴍᴜᴛᴇ 𝐄ʀʀ → `{str(e)[:40]}`")


@register_cmd("lgunmute", needs_reply=True)
async def cmd_gunmute(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴀʀɢᴇᴛ\n👉 `~gunmute` (reply / @username / id)"
            )

        await safe_edit(
            event,
            "⚡ 𝐏ʀᴏᴄᴇssɪɴɢ 𝐆ᴜɴᴍᴜᴛᴇ...\n━━━━━━━━━━━━━━━"
        )

        removed, not_muted = [], []

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid in global_muted:
                global_muted.remove(uid)
                removed.append(str(uid))
            else:
                not_muted.append(str(uid))

        parts = []

        if removed:
            parts.append(f"🔊 𝐆ᴜɴᴍᴜᴛᴇ → `{', '.join(removed)}`")

        if not_muted:
            parts.append(f"⚠️ 𝐍ᴏᴛ 𝐆ᴍᴜᴛᴇᴅ → `{', '.join(not_muted)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"❌ 𝐆𝐔𝐍𝐌𝐔𝐓𝐄 𝐄ʀʀ → `{str(e)[:40]}`")

# Group Mod
@register_cmd("lpurge")
async def cmd_purge(event, arg):
    try:
        try:
            count = int(arg) if arg else 50
        except:
            count = 50

        # ─── HARD LIMIT ENGINE ───
        if count < 1:
            count = 1
        if count > 200:
            count = 200

        await safe_edit(
            event,
            "⚡ 𝐏ᴜʀɢɪɴɢ 𝐌ᴇssᴀɢᴇs...\n━━━━━━━━━━━━━━━"
        )

        msgs = []
        async for m in bot.iter_messages(event.chat_id, limit=count + 1):
            msgs.append(m.id)

        if not msgs:
            return await safe_edit(event,
                "⚠️ 𝐍ᴏ 𝐌ᴇssᴀɢᴇs 𝐅ᴏᴜɴᴅ"
            )

        # ─── SAFE DELETE BATCH ───
        try:
            await bot.delete_messages(event.chat_id, msgs)
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds)
            await bot.delete_messages(event.chat_id, msgs)
        except RPCError:
            # readonly / no rights / protected msgs
            pass

        text = (
            "🧹 𝐏ᴜʀɢᴇ 𝐂ᴏᴍᴘʟᴇᴛᴇ\n"
            "━━━━━━━━━━━━━━━\n"
            f"🗑️ 𝐃ᴇʟᴇᴛᴇᴅ → `{max(len(msgs)-1,0)}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐏ᴜʀɢᴇ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lthrow", needs_reply=True, group_only=True)
async def cmd_throw(event, arg):
    try:
        targets = await get_targets(event, arg)

        if not targets:
            return await safe_edit(event,
                "❌ 𝐍ᴏ 𝐓ᴀʀɢᴇᴛ"
            )

        # ─── ADMIN CHECK SAFE ───
        try:
            perms = await bot.get_permissions(event.chat_id, 'me')
            if not perms.is_admin:
                return await safe_edit(event,
                    "❌ 𝐍ᴏ 𝐀ᴅᴍɪɴ 𝐑ɪɢʜᴛs"
                )
        except:
            return await safe_edit(event,
                "❌ 𝐏ᴇʀᴍ𝐢ssɪᴏɴ 𝐂ʜᴇᴄᴋ 𝐅ᴀɪʟ"
            )

        await safe_edit(
            event,
            "⚡ 𝐊ɪᴄᴋɪɴɢ 𝐓ᴀʀɢᴇᴛs...\n━━━━━━━━━━━━━━━"
        )

        kicked, failed, skipped = [], [], []

        me = await bot.get_me()

        for uid in targets:
            try:
                uid = int(uid)
            except:
                continue

            if uid == me.id:
                skipped.append(str(uid))
                continue

            try:
                await bot.kick_participant(event.chat_id, uid)
                kicked.append(str(uid))
            except:
                failed.append(str(uid))

        parts = []

        if kicked:
            parts.append(f"👞 𝐊ɪᴄᴋᴇᴅ → `{', '.join(kicked)}`")

        if failed:
            parts.append(f"⚠️ 𝐅ᴀɪʟᴇᴅ → `{', '.join(failed)}`")

        if skipped:
            parts.append(f"👑 𝐒ᴇʟғ 𝐒ᴋɪᴘ → `{', '.join(skipped)}`")

        if not parts:
            parts.append("❌ 𝐍ᴏ 𝐀ᴄᴛɪᴏɴ")

        msg = "\n".join(parts)

        if event.out:
            await safe_edit(event, msg)
        else:
            await event.reply(lofi_format(msg))

    except FloodWaitError as fw:
        await safe_edit(event, f"⏳ 𝐅ʟᴏᴏᴅ → {fw.seconds}s")
    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐓ʜʀᴏᴡ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lsrr")
async def cmd_srr(event, arg):
    try:
        targets = await get_targets(event, arg)

        if targets:
            stopped, not_active = [], []

            for uid in targets:
                try:
                    uid = int(uid)
                except:
                    continue

                if uid in rr_users:
                    rr_users.discard(uid)
                    stopped.append(str(uid))
                else:
                    not_active.append(str(uid))

            parts = []

            if stopped:
                parts.append(f"🛑 𝐑𝐑 𝐎ғғ → `{', '.join(stopped)}`")

            if not_active:
                parts.append(f"⚠️ 𝐍ᴏᴛ 𝐀ᴄᴛɪᴠᴇ → `{', '.join(not_active)}`")

            if not parts:
                parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

            text = "\n".join(parts)

        else:
            rr_users.clear()

            text = (
                "🛑 𝐑𝐑 𝐎ғғ\n"
                "━━━━━━━━━━━━━━━\n"
                "👉 𝐀ʟʟ 𝐔sᴇʀs"
            )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒𝐑𝐑 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lsflag")
async def cmd_sflag(event, arg):
    try:
        targets = await get_targets(event, arg)

        if targets:
            stopped, not_active = [], []

            for uid in targets:
                try:
                    uid = int(uid)
                except:
                    continue

                if uid in flag_users:
                    flag_users.discard(uid)
                    stopped.append(str(uid))
                else:
                    not_active.append(str(uid))

            parts = []

            if stopped:
                parts.append(f"🛑 𝐅ʟᴀɢ 𝐎ғғ → `{', '.join(stopped)}`")

            if not_active:
                parts.append(f"⚠️ 𝐍ᴏᴛ 𝐀ᴄᴛɪᴠᴇ → `{', '.join(not_active)}`")

            if not parts:
                parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

            text = "\n".join(parts)

        else:
            flag_users.clear()

            text = (
                "🛑 𝐅ʟᴀɢ 𝐎ғғ\n"
                "━━━━━━━━━━━━━━━\n"
                "👉 𝐀ʟʟ 𝐔sᴇʀs"
            )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒𝐅𝐋𝐀𝐆 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lshrr")
async def cmd_shrr(event, arg):
    try:
        targets = await get_targets(event, arg)

        if targets:
            await safe_edit(
                event,
                "⚡ 𝐃ɪsᴀʙʟɪɴɢ 𝐇ᴇᴀʀᴛ 𝐑ᴀɪᴅ (𝐆ʟᴏʙᴀʟ)...\n━━━━━━━━━━━━━━━"
            )

            stopped, not_active = [], []

            for uid in targets:
                try:
                    uid = int(uid)
                except:
                    continue

                if uid in hrr_users:
                    hrr_users.discard(uid)
                    stopped.append(str(uid))
                else:
                    not_active.append(str(uid))

            parts = []

            if stopped:
                parts.append(
                    f"🛑 𝐇ᴇᴀʀᴛ 𝐎ғғ → `{', '.join(stopped)}`"
                )

            if not_active:
                parts.append(
                    f"⚠️ 𝐍ᴏᴛ 𝐀ᴄᴛɪᴠᴇ → `{', '.join(not_active)}`"
                )

            if not parts:
                parts.append("❌ 𝐍ᴏ 𝐂ʜᴀɴɢᴇ")

            text = "\n".join(parts)

        else:
            hrr_users.clear()

            text = (
                "🛑 𝐇ᴇᴀʀᴛ 𝐎ғғ\n"
                "━━━━━━━━━━━━━━━\n"
                "👉 𝐀ʟʟ 𝐔sᴇʀs"
            )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒𝐇𝐑𝐑 𝐄ʀʀ → `{str(e)[:40]}`"
        )


# Lock / Unlock
@register_cmd("llock", group_only=True)
async def cmd_lock(event, _):
    try:
        chat = event.chat_id

        # ─── ADMIN CHECK ───
        try:
            perms = await bot.get_permissions(chat, 'me')
            if not perms.is_admin:
                return await safe_edit(event,
                    "❌ 𝐍ᴏ 𝐀ᴅᴍɪɴ 𝐑ɪɢʜᴛs"
                )
        except:
            pass

        if chat in group_locks:
            msg = "⚠️ 𝐆ʀᴏᴜᴘ 𝐀ʟʀᴇᴀᴅʏ 𝐋ᴏᴄᴋᴇᴅ"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐀ᴘᴘʟʏɪɴɢ 𝐋ᴏᴄᴋ...\n━━━━━━━━━━━━━━━"
        )

        group_locks.add(chat)

        text = (
            "🔒 𝐆ʀᴏᴜᴘ 𝐋ᴏᴄᴋᴇᴅ\n"
            "━━━━━━━━━━━━━━━\n"
            "🚫 𝐍ᴏɴ-𝐀ᴅᴍɪɴ 𝐌ᴇssᴀɢᴇs 𝐁ʟᴏᴄᴋᴇᴅ"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐋ᴏᴄᴋ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lunlock", group_only=True)
async def cmd_unlock(event, _):
    try:
        chat = event.chat_id

        # ─── ADMIN CHECK ───
        try:
            perms = await bot.get_permissions(chat, 'me')
            if not perms.is_admin:
                return await safe_edit(event,
                    "❌ 𝐍ᴏ 𝐀ᴅᴍɪɴ 𝐑ɪɢʜᴛs"
                )
        except:
            pass

        if chat not in group_locks:
            msg = "⚠️ 𝐆ʀᴏᴜᴘ 𝐀ʟʀᴇᴀᴅʏ 𝐔ɴʟᴏᴄᴋᴇᴅ"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐑ᴇᴍᴏᴠɪɴɢ 𝐋ᴏᴄᴋ...\n━━━━━━━━━━━━━━━"
        )

        group_locks.discard(chat)

        text = (
            "🔓 𝐆ʀᴏᴜᴘ 𝐔ɴʟᴏᴄᴋᴇᴅ\n"
            "━━━━━━━━━━━━━━━\n"
            "✅ 𝐌ᴇssᴀɢɪɴɢ 𝐑ᴇsᴛᴏʀᴇᴅ"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐔ɴʟᴏᴄᴋ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


# ───────── AUTO REACT ─────────

@register_cmd("lar")
async def cmd_ar(event, arg):
    global auto_react_emoji

    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐄ᴍᴏᴊɪ\n👉 `~ar <emoji>`"
            )

        emoji = arg.strip()

        # ─── EMOJI SANITY GUARD ───
        if len(emoji) > 8:
            return await safe_edit(event,
                "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐄ᴍᴏᴊɪ"
            )

        if auto_react_emoji == emoji:
            msg = f"⚠️ 𝐀ʀ 𝐀ʟʀᴇᴀᴅʏ → {emoji}"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐀ᴘᴘʟʏɪɴɢ 𝐀ᴜᴛᴏ-𝐑ᴇᴀᴄᴛ...\n━━━━━━━━━━━━━━━"
        )

        auto_react_emoji = emoji

        text = (
            "✅ 𝐀ᴜᴛᴏ 𝐑ᴇᴀᴄᴛ 𝐄ɴᴀʙʟᴇᴅ\n"
            "━━━━━━━━━━━━━━━\n"
            f"😀 𝐄ᴍᴏᴊɪ → {emoji}"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀𝐑 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lsar")
async def cmd_sar(event, _):
    global auto_react_emoji

    try:
        if auto_react_emoji is None:
            msg = "⚠️ 𝐀𝐑 𝐍ᴏᴛ 𝐀ᴄᴛɪᴠᴇ"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐃ɪsᴀʙʟɪɴɢ 𝐀ᴜᴛᴏ-𝐑ᴇᴀᴄᴛ...\n━━━━━━━━━━━━━━━"
        )

        auto_react_emoji = None

        text = (
            "🛑 𝐀ᴜᴛᴏ 𝐑ᴇᴀᴄᴛ 𝐃ɪsᴀʙʟᴇᴅ\n"
            "━━━━━━━━━━━━━━━"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒𝐀𝐑 𝐄ʀʀ → `{str(e)[:40]}`"
        )

# FastGC
@register_cmd("lfastgc", group_only=True)
async def cmd_fastgc(event, arg):
    title = arg.strip()
    if not title:
        return await safe_edit(
            event,
            "Usage: `~gwdfasttitle <new group title>`; this changes the title once.",
        )
    if (
        "\n" in title
        or "\r" in title
        or len(title.encode("utf-16-le")) // 2 > 128
    ):
        return await safe_edit(
            event,
            "The group title must be a single line of at most 128 UTF-16 code units.",
        )

    requester_permissions = await bot.get_permissions(
        event.chat_id, event.sender_id
    )
    if not can_change_group_title(requester_permissions):
        return await safe_edit(
            event,
            "You need group title-change rights to use this command.",
        )

    account_permissions = await bot.get_permissions(event.chat_id, "me")
    if not can_change_group_title(account_permissions):
        return await safe_edit(
            event,
            "This account needs group title-change rights to update the title.",
        )

    chat = await event.get_chat()
    if isinstance(chat, types.Chat):
        request = functions.messages.EditChatTitleRequest(
            chat_id=event.chat_id,
            title=title,
        )
    elif isinstance(chat, types.Channel) and chat.megagroup:
        request = functions.channels.EditTitleRequest(
            channel=chat,
            title=title,
        )
    else:
        return await safe_edit(
            event,
            "This command is only available in groups and supergroups.",
        )

    await bot(request)
    await safe_edit(
        event,
        "Group title updated once. Continuous title rotation is disabled.",
    )


# ───────── NOTES ─────────

@register_cmd("lnotesadd")
async def notes_add(event, arg):
    try:
        if not arg:
            return await safe_edit(event,
                "❌ 𝐆ɪᴠᴇ 𝐍ᴏᴛᴇ 𝐓ᴇxᴛ"
            )

        await safe_edit(
            event,
            "⚡ 𝐒ᴀᴠɪɴɢ 𝐍ᴏᴛᴇ...\n━━━━━━━━━━━━━━━"
        )

        nid = max(notes.keys(), default=0) + 1
        notes[nid] = arg[:4000]

        try:
            save_notes()
        except:
            pass

        await safe_edit(
            event,
            f"📝 𝐍ᴏᴛᴇ 𝐒ᴀᴠᴇᴅ\n━━━━━━━━━━━━━━━\n🆔 `{nid}`"
        )

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐍ᴏᴛᴇ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lnoteslist")
async def notes_list(event, _):
    try:
        if not notes:
            return await safe_edit(event,
                "📭 𝐍ᴏ 𝐍ᴏᴛᴇs"
            )

        await safe_edit(
            event,
            "⚡ 𝐅ᴇᴛᴄʜɪɴɢ 𝐍ᴏᴛᴇs...\n━━━━━━━━━━━━━━━"
        )

        msg = "📝 𝐘ᴏᴜʀ 𝐍ᴏᴛᴇs\n━━━━━━━━━━━━━━━\n"

        for i, t in sorted(notes.items()):
            msg += f"🔹 `{i}` → {t[:100]}\n"

        await safe_edit(event, msg)

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐍ᴏᴛᴇ𝐋𝐢sᴛ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lnotesdelete")
async def notes_delete(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐆ɪᴠᴇ 𝐈𝐃"
            )

        try:
            nid = int(arg)
        except:
            return await safe_edit(event,
                "❌ 𝐈𝐃 𝐌ᴜsᴛ 𝐁ᴇ 𝐍ᴜᴍʙᴇʀ"
            )

        if nid not in notes:
            msg = "⚠️ 𝐍ᴏᴛᴇ 𝐍ᴏᴛ 𝐅ᴏᴜɴᴅ"
            if event.out:
                return await safe_edit(event, msg)
            else:
                return await event.reply(lofi_format(msg))

        await safe_edit(
            event,
            "⚡ 𝐃ᴇʟᴇᴛɪɴɢ 𝐍ᴏᴛᴇ...\n━━━━━━━━━━━━━━━"
        )

        notes.pop(nid, None)

        try:
            save_notes()
        except:
            pass

        text = (
            "🗑️ 𝐍ᴏᴛᴇ 𝐃ᴇʟᴇᴛᴇᴅ\n"
            "━━━━━━━━━━━━━━━\n"
            f"🆔 `{nid}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐍ᴏᴛᴇ𝐃𝐞𝐥 𝐄ʀʀ → `{str(e)[:40]}`"
        )


# ───────── TTS ─────────

@register_cmd("ltts")
async def cmd_tts(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴇxᴛ\n👉 `~tts <text>`"
            )

        await safe_edit(
            event,
            "⚡ 𝐆ᴇɴᴇʀᴀᴛɪɴɢ 𝐓𝐓𝐒...\n━━━━━━━━━━━━━━━"
        )

        fname = os.path.join(TEMP_PATH, f"tts_{uuid.uuid4().hex}.mp3")

        try:
            try:
                gTTS(text=arg[:5000], lang="hi", slow=False).save(fname)
            except Exception:
                return await safe_edit(event, "❌ 𝐓𝐓𝐒 𝐍ᴇᴛᴡᴏʀᴋ 𝐅ᴀɪʟ")
            if event.out:
                await event.delete()

            await bot.send_file(
                event.chat_id,
                fname,
                caption=lofi_format("🎙️  𝐓𝐓𝐒 𝐆ᴇɴ"),
            )
        finally:
            try:
                os.remove(fname)
            except OSError:
                pass

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐓𝐓𝐒 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("lqrcode")
async def cmd_qrcode(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴇxᴛ / 𝐋ɪɴᴋ\n👉 `~qrcode <text>`"
            )

        await safe_edit(
            event,
            "⚡ 𝐆ᴇɴᴇʀᴀᴛɪɴɢ 𝐐𝐑...\n━━━━━━━━━━━━━━━"
        )

        file = os.path.join(TEMP_PATH, f"qr_{uuid.uuid4().hex}.png")
        try:
            qrcode.make(arg[:3000]).save(file)
            if event.out:
                await event.delete()

            await bot.send_file(
                event.chat_id,
                file,
                caption=lofi_format("🔳  𝐐𝐑 𝐂ᴏᴅᴇ"),
            )
        finally:
            try:
                os.remove(file)
            except OSError:
                pass

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐐𝐑 𝐄ʀʀ → `{str(e)[:50]}`"
        )


@register_cmd("lfancy")
async def cmd_fancy(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴇxᴛ\n👉 `~fancy <text>`"
            )

        t = arg[:2000]

        styles = [
            t.upper(),
            t.lower(),
            f"★彡 {t} 彡★",
            f"『 {t} 』",
            f"✦ {t} ✦",
            f"☾ {t} ☽",
            f"➳ {t} ➳",
            f"⚡ {t} ⚡",
            f"❖ {t} ❖",
            f"⫷ {t} ⫸",
            f"♛ {t} ♛",
            f"✧･ﾟ: *✧ {t} ✧*:･ﾟ✧",
            f"꧁ {t} ꧂",
            f"░▒▓ {t} ▓▒░",
            f"➶➶ {t} ➷➷",
            f"✿ {t} ✿",
            f"彡★ {t} ★彡",
            f"⧼ {t} ⧽",
            f"⟪ {t} ⟫",
            f"⌁ {t} ⌁"
        ]

        text = (
            "✨ 𝐅ᴀɴᴄʏ 𝐒ᴛʏʟᴇs\n"
            "━━━━━━━━━━━━━━━\n"
            + "\n".join(styles)
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐅ᴀɴᴄʏ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lstyle")
async def cmd_style(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴇxᴛ\n👉 `~style <text>`"
            )

        t = arg[:2000]

        fancy = (
            t.replace('a','𝒶').replace('b','𝒷')
             .replace('c','𝒸').replace('d','𝒹')
             .replace('e','𝑒').replace('f','𝒻')
             .replace('g','𝑔').replace('h','𝒽')
             .replace('i','𝒾').replace('j','𝒿')
             .replace('k','𝓀').replace('l','𝓁')
             .replace('m','𝓂').replace('n','𝓃')
             .replace('o','𝑜').replace('p','𝓅')
             .replace('q','𝓆').replace('r','𝓇')
             .replace('s','𝓈').replace('t','𝓉')
             .replace('u','𝓊').replace('v','𝓋')
             .replace('w','𝓌').replace('x','𝓍')
             .replace('y','𝓎').replace('z','𝓏')
        )

        text = (
            "🎨 𝐓ᴇxᴛ 𝐒ᴛʏʟᴇ\n"
            "━━━━━━━━━━━━━━━\n"
            f"𝒇𝒂𝒏𝒄ʏ → {fancy}\n"
            f"**Bold** → **{t}**\n"
            f"__Italic__ → __{t}__\n"
            f"`Mono` → `{t}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒ᴛʏʟᴇ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


@register_cmd("lemoji")
async def cmd_emoji(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐓ᴇxᴛ\n👉 `~emoji <text>`"
            )

        pool = [
            "🔥","❤️","✨","⚡","💥","🌟","💫","🎯",
            "💎","🦋","🌈","🧨","🎆","👑","🌸","🪄",
            "🌊","❄️","🍁","🌙","☀️","💣","🎵","🧿"
        ]

        emojis = "".join(random.choice(pool) for _ in range(8))

        text = (
            "😀 𝐄ᴍᴏᴊɪ 𝐒ᴛʏʟᴇ\n"
            "━━━━━━━━━━━━━━━\n"
            f"{arg[:2000]} {emojis}"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐄ᴍᴏᴊɪ 𝐄ʀʀ → `{str(e)[:40]}`"
        )


def calculate_expression(expression):
    if not expression or len(expression) > 120:
        raise ValueError("Expression must contain at most 120 characters")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("Expression is too complex")

    binary_ops = {
        ast.Add: lambda left, right: left + right,
        ast.Sub: lambda left, right: left - right,
        ast.Mult: lambda left, right: left * right,
        ast.Div: lambda left, right: left / right,
        ast.FloorDiv: lambda left, right: left // right,
        ast.Mod: lambda left, right: left % right,
        ast.Pow: lambda left, right: left ** right,
    }
    unary_ops = {ast.UAdd: lambda value: value, ast.USub: lambda value: -value}

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            value = node.value
        elif isinstance(node, ast.BinOp) and type(node.op) in binary_ops:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("Exponent is too large")
            try:
                value = binary_ops[type(node.op)](left, right)
            except ArithmeticError as exc:
                raise ValueError("Invalid arithmetic operation") from exc
        elif isinstance(node, ast.UnaryOp) and type(node.op) in unary_ops:
            value = unary_ops[type(node.op)](evaluate(node.operand))
        else:
            raise ValueError("Unsupported expression")

        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError):
            finite = False
        if not finite or abs(value) > 1_000_000_000_000:
            raise ValueError("Result is out of range")
        return value

    return evaluate(tree)


@register_cmd("lcalc")
async def cmd_calc(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐄xᴘʀᴇssɪᴏɴ\n👉 `~calc <math>`"
            )

        expr = arg.strip()

        await safe_edit(
            event,
            "⚡ 𝐂ᴀʟᴄᴜʟᴀᴛɪɴɢ...\n━━━━━━━━━━━━━━━"
        )

        res = calculate_expression(expr)

        text = (
            "🧮 𝐂ᴀʟᴄ𝐮ʟᴀᴛᴏʀ\n"
            "━━━━━━━━━━━━━━━\n"
            f"📥 `{expr}`\n"
            f"📤 `{res}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception:
        await safe_edit(event,
            "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐄xᴘʀᴇssɪᴏɴ"
        )

@register_cmd("lweather")
async def cmd_weather(event, arg):
    try:
        if not arg:
            return await safe_edit(event,
                "❌ 𝐆ɪᴠᴇ 𝐂ɪᴛʏ"
            )

        await safe_edit(
            event,
            "⚡ 𝐅ᴇᴛᴄʜɪɴɢ 𝐖ᴇᴀᴛʜᴇʀ...\n━━━━━━━━━━━━━━━"
        )

        try:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": arg[:100], "count": 1},
                timeout=8,
            ).json()
        except:
            return await safe_edit(event,
                "❌ 𝐆ᴇᴏ 𝐍ᴇᴛᴡᴏʀᴋ 𝐅ᴀɪʟ"
            )

        if not geo.get("results"):
            return await safe_edit(event,
                "❌ 𝐂ɪᴛʏ 𝐍ᴏᴛ 𝐅ᴏᴜɴᴅ"
            )

        res = geo["results"][0]

        lat = res.get("latitude")
        lon = res.get("longitude")
        name = res.get("name")

        try:
            w = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon, "current_weather": "true"},
                timeout=8,
            ).json()
        except:
            return await safe_edit(event,
                "❌ 𝐖ᴇᴀᴛʜᴇʀ 𝐍ᴇᴛᴡᴏʀᴋ 𝐅ᴀɪʟ"
            )

        cw = w.get("current_weather")

        if not cw:
            return await safe_edit(event,
                "❌ 𝐍ᴏ 𝐖ᴇᴀᴛʜᴇʀ 𝐃ᴀᴛᴀ"
            )

        temp = cw.get("temperature")
        wind = cw.get("windspeed")
        code = cw.get("weathercode")

        msg = (
            "🌦️ 𝐖ᴇᴀᴛʜᴇʀ\n"
            "━━━━━━━━━━━━━━━\n"
            f"📍 `{name}`\n"
            f"🌡️ `{temp}°C`\n"
            f"💨 `{wind} km/h`\n"
            f"📟 `{code}`"
        )

        await safe_edit(event, msg)

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐖ᴇᴀᴛʜᴇʀ → `{str(e)[:40]}`"
        )


@register_cmd("lip")
async def cmd_ip(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐈𝐏"
            )

        try:
            address = str(ipaddress.ip_address(arg.strip()))
        except ValueError:
            return await safe_edit(event, "❌ 𝐄ɴᴛᴇʀ 𝐀 𝐕ᴀʟɪᴅ 𝐈𝐏 𝐀ᴅᴅʀᴇss")

        await safe_edit(
            event,
            "⚡ 𝐋ᴏᴏᴋɪɴɢ 𝐈𝐏...\n━━━━━━━━━━━━━━━"
        )

        try:
            data = requests.get(
                f"https://ipapi.co/{address}/json/",
                timeout=8,
            ).json()
        except:
            return await safe_edit(event,
                "❌ 𝐈𝐏 𝐍ᴇᴛᴡᴏʀᴋ 𝐅ᴀɪʟ"
            )

        if data.get("error"):
            return await safe_edit(event,
                "❌ 𝐈𝐧𝐯ᴀʟɪᴅ 𝐈𝐏"
            )

        text = (
            "🌍 𝐈𝐏 𝐈𝐍𝐅𝐎\n"
            "━━━━━━━━━━━━━━━\n"
            f"📡 `{data.get('query')}`\n"
            f"🌐 `{data.get('country_name')}`\n"
            f"🏙️ `{data.get('city')}`\n"
            f"📍 `{data.get('org')}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐈𝐏 → `{str(e)[:40]}`"
        )


@register_cmd("lshort")
async def cmd_short(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ 𝐍ᴏ 𝐔𝐑𝐋"
            )

        if not arg.startswith(("http://", "https://")):
            arg = "https://" + arg
        parsed_url = requests.utils.urlparse(arg)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            return await safe_edit(event, "❌ 𝐄ɴᴛᴇʀ 𝐀 𝐕ᴀʟɪᴅ 𝐇𝐓𝐓𝐏 𝐔𝐑𝐋")

        await safe_edit(
            event,
            "⚡ 𝐒ʜᴏʀᴛᴇɴɪɴɢ...\n━━━━━━━━━━━━━━━"
        )

        try:
            short_url = requests.get(
                "https://tinyurl.com/api-create.php",
                params={"url": arg},
                timeout=8,
            ).text.strip()
        except:
            return await safe_edit(event,
                "❌ 𝐒ʜᴏʀᴛ 𝐍ᴇᴛᴡᴏʀᴋ 𝐅ᴀɪʟ"
            )

        text = (
            "🔗 𝐒ʜᴏʀᴛ 𝐔𝐑𝐋\n"
            "━━━━━━━━━━━━━━━\n"
            f"`{short_url}`"
        )

        if event.out:
            await safe_edit(event, text)
        else:
            await event.reply(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐒ʜᴏʀᴛ → `{str(e)[:40]}`"
        )


@register_cmd("linfo", needs_reply=False)
async def cmd_info(event, arg):
    try:
        target = None

        if event.is_reply:
            r = await event.get_reply_message()
            if r and r.sender_id:
                target = r.sender_id

        elif arg:
            try:
                ent = await bot.get_entity(arg)
                target = ent.id
            except:
                return await safe_edit(event,
                    "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐔sᴇʀ"
                )

        if not target:
            return await safe_edit(event,
                "⚠️ `~gwduserinfo` (reply / @user / id)"
            )

        await safe_edit(
            event,
            "⚡ 𝐅ᴇᴛᴄʜɪɴɢ 𝐔𝐬𝐞𝐫...\n━━━━━━━━━━━━━━━"
        )

        user = await bot.get_entity(target)

        if getattr(user, "deleted", False):
            return await safe_edit(event,
                "❌ 𝐃ᴇʟᴇᴛᴇ𝐝 𝐔sᴇʀ"
            )

        if user.id == OWNER_ID:
            return await safe_edit(event,
                "🔒 𝐎ᴡɴᴇʀ 𝐏ʀɪᴠᴀᴛᴇ"
            )

        full = await bot(functions.users.GetFullUserRequest(user.id))

        bio = full.full_user.about or "𝐍ᴏ 𝐁ɪᴏ"
        uname = f"@{user.username}" if user.username else "𝐍ᴏ 𝐔sᴇʀ"

        # ─── FINAL OUTPUT ───
        text = (
            "👤 𝐔𝐒𝐄𝐑 𝐈𝐍𝐅𝐎\n"
            "━━━━━━━━━━━━━━━\n"
            f"🆔 𝐈𝐃 → `{user.id}`\n"
            f"📛 𝐍ᴀᴍᴇ → {user.first_name or ''} {user.last_name or ''}\n"
            f"🔗 𝐔sᴇʀ → {uname}\n"
            f"📝 𝐁ɪᴏ → {bio}"
        )

        await safe_edit(event, text)

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐈𝐍𝐅𝐎 → `{str(e)[:50]}`"
        )
        
        
@register_cmd("lcopy", needs_reply=False)
async def cmd_copy(event, args):
    global CLONE_DATA, CLONE_ACTIVE, LAST_CLONE_ID

    try:
        reply = await event.get_reply_message()
        target = None

        # ───── TARGET RESOLVE ─────
        if reply:
            try:
                if reply.sender_id:
                    target = await bot.get_entity(reply.sender_id)
            except:
                pass

            if not target and getattr(reply, "fwd_from", None):
                try:
                    fid = reply.fwd_from.from_id
                    if fid:
                        target = await bot.get_entity(fid)
                except:
                    pass

        if not target and args:
            try:
                target = await bot.get_entity(args.strip())
            except:
                pass

        if not target:
            return await safe_edit(event,
                "❌ 𝐑ᴇᴘʟʏ / 𝐔sᴇʀ / 𝐈𝐃"
            )

        me = await bot.get_me()

        # ───── SELF BLOCK ─────
        if target.id == me.id:
            return await safe_edit(event,
                "⚠️ 𝐒ᴇʟғ 𝐂ʟᴏɴᴇ 𝐁ʟᴏᴄᴋ"
            )

        # ───── SAME CLONE BLOCK ─────
        if CLONE_ACTIVE and LAST_CLONE_ID == target.id:
            return await safe_edit(event,
                "⚠️ 𝐀ʟʀᴇᴀᴅʏ 𝐂ʟᴏɴᴇᴅ"
            )

        await safe_edit(event,
            "⚡ 𝐂ʟᴏɴᴇ 𝐈ɴɪᴛ\n━━━━━━━━━━━━━━━"
        )

        # ───── ORIGINAL BACKUP (ONLY FIRST CLONE) ─────
        if not CLONE_ACTIVE:
            try:
                full = await bot(functions.users.GetFullUserRequest(me.id))

                CLONE_DATA.clear()

                CLONE_DATA["name"] = me.first_name
                CLONE_DATA["last"] = me.last_name
                CLONE_DATA["bio"] = full.full_user.about
                CLONE_DATA["username"] = me.username

                try:
                    dp = await bot.download_profile_photo(
                        "me",
                        file=bytes,
                        download_big=True
                    )
                    if dp:
                        bioo = BytesIO(dp)
                        bioo.name = "orig.jpg"
                        CLONE_DATA["photo_bytes"] = bioo
                except:
                    CLONE_DATA["photo_bytes"] = None

                CLONE_ACTIVE = True

            except Exception as e:
                print("backup fail:", e)

        # ───── NAME ─────
        await safe_edit(event, "⚡ 𝐂ʟᴏɴɪɴɢ 𝐍ᴀᴍᴇ...")
        try:
            await bot(functions.account.UpdateProfileRequest(
                first_name=target.first_name or "",
                last_name=target.last_name or ""
            ))
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds)

        # ───── BIO ─────
        await safe_edit(event, "⚡ 𝐂ʟᴏɴɪɴɢ 𝐁ɪᴏ...")
        try:
            tfull = await bot(functions.users.GetFullUserRequest(target.id))
            bio_text = tfull.full_user.about or ""

            bio_text = bio_text.encode(
                "utf-16", "surrogatepass"
            ).decode("utf-16", "ignore")

            bio_text = bio_text.strip()

            if len(bio_text) > 70:
                bio_text = bio_text[:70]

            await bot(functions.account.UpdateProfileRequest(about=""))
            await asyncio.sleep(0.7)

            await bot(functions.account.UpdateProfileRequest(
                about=bio_text
            ))
        except:
            pass

        # ───── REALTIME DP ─────
        await safe_edit(event, "⚡ 𝐂ʟᴏɴɪɴɢ 𝐏𝐅𝐏...")
        try:
            file = await bot.download_profile_photo(
                target,
                file=bytes,
                download_big=True
            )

            if file:
                bio = BytesIO(file)
                bio.name = "clone.jpg"

                up = await bot.upload_file(bio)

                try:
                    cur = await bot.get_profile_photos("me", limit=1)
                    if cur:
                        await bot(functions.photos.DeletePhotosRequest(
                            id=[cur[0]]
                        ))
                except:
                    pass

                await bot(functions.photos.UploadProfilePhotoRequest(
                    file=up
                ))
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds)
        except:
            pass

        LAST_CLONE_ID = target.id

        await safe_edit(event,
            "✅ 𝐂ʟᴏɴᴇ 𝐂ᴏᴍᴘʟᴇᴛᴇ\n━━━━━━━━━━━━━━━"
        )

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐂ʟᴏɴᴇ → `{str(e)[:40]}`"
        )


@register_cmd("lnormal")
async def cmd_normal(event, _):
    global CLONE_DATA, CLONE_ACTIVE, LAST_CLONE_ID

    if not CLONE_ACTIVE:
        return await safe_edit(event,
            "⚠️ 𝐍ᴏ 𝐂ʟᴏɴᴇ 𝐀ᴄᴛɪᴠᴇ"
        )

    try:
        await safe_edit(event,
            "⚡ 𝐑ᴇsᴛᴏʀᴇ 𝐈ɴɪᴛ\n━━━━━━━━━━━━━━━"
        )

        # ───── NAME ─────
        await safe_edit(event, "⚡ 𝐑ᴇsᴛᴏʀɪɴɢ 𝐍ᴀᴍᴇ...")
        try:
            await bot(functions.account.UpdateProfileRequest(
                first_name=CLONE_DATA.get("name") or "",
                last_name=CLONE_DATA.get("last") or ""
            ))
        except:
            pass

        # ───── BIO ─────
        await safe_edit(event, "⚡ 𝐑ᴇsᴛᴏʀɪɴɢ 𝐁ɪᴏ...")
        try:
            await bot(functions.account.UpdateProfileRequest(about=""))
            await asyncio.sleep(0.7)

            await bot(functions.account.UpdateProfileRequest(
                about=CLONE_DATA.get("bio") or ""
            ))
        except:
            pass

        # ───── DP ─────
        await safe_edit(event, "⚡ 𝐑ᴇsᴛᴏʀɪɴɢ 𝐏𝐅𝐏...")
        try:
            cur = await bot.get_profile_photos("me", limit=1)
            if cur:
                await bot(functions.photos.DeletePhotosRequest(
                    id=[cur[0]]
                ))

            if CLONE_DATA.get("photo_bytes"):
                bio = CLONE_DATA["photo_bytes"]
                bio.name = "restore.jpg"
                up = await bot.upload_file(bio)

                await bot(functions.photos.UploadProfilePhotoRequest(
                    file=up
                ))
        except:
            pass

        CLONE_ACTIVE = False
        LAST_CLONE_ID = None
        CLONE_DATA.clear()

        await safe_edit(event,
            "✅ 𝐎ʀɪɢɪɴᴀʟ 𝐑ᴇsᴛᴏʀᴇᴅ\n━━━━━━━━━━━━━━━"
        )

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐑ᴇsᴛᴏʀᴇ → `{str(e)[:40]}`"
        )
        

        
        
@register_cmd("lmutelist")
async def cmd_mutelist(event, _):
    try:
        if not event.is_group:
            return await safe_edit(event, "❌ Mute list is available only in groups.")

        await safe_edit(
            event,
            "⚡ 𝐅ᴇᴛᴄʜɪɴɢ 𝐌ᴜᴛᴇ𝐏ᴀɴᴇʟ...\n━━━━━━━━━━━━━━━"
        )

        text = "📋 𝐌ᴜᴛᴇ & 𝐋ᴏᴄᴋ 𝐏ᴀɴᴇʟ\n━━━━━━━━━━━━━━━\n"

        # ───── LOCAL MUTE ─────
        text += "\n🔇 𝐋ᴏᴄᴀʟ 𝐌ᴜᴛᴇᴅ\n"
        chat_muted = muted_users.get(event.chat_id, set())
        if chat_muted:
            for uid in sorted(chat_muted):
                try:
                    u = await bot.get_entity(uid)
                    uname = f"@{u.username}" if u.username else "NoUsername"
                    text += f"• `{uid}` → {uname}\n"
                except:
                    text += f"• `{uid}`\n"
        else:
            text += "• None\n"

        # ───── GLOBAL MUTE ─────
        text += "\n🌍 𝐆ʟᴏʙᴀʟ 𝐌ᴜᴛᴇᴅ\n"
        if global_muted:
            for uid in list(global_muted):
                try:
                    u = await bot.get_entity(uid)
                    uname = f"@{u.username}" if u.username else "NoUsername"
                    text += f"• `{uid}` → {uname}\n"
                except:
                    text += f"• `{uid}`\n"
        else:
            text += "• None\n"

        # ───── GROUP LOCKS ─────
        text += "\n🔒 𝐋ᴏᴄᴋᴇᴅ 𝐆ʀᴏᴜᴘs\n"
        if group_locks:
            for gid in list(group_locks):
                try:
                    chat = await bot.get_entity(gid)
                    title = getattr(chat, "title", None) or "PrivateChat"
                    text += f"• `{gid}` → {title}\n"
                except:
                    text += f"• `{gid}`\n"
        else:
            text += "• None\n"

        await safe_edit(event, text)

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐌ᴜᴛᴇ𝐋ɪsᴛ → `{str(e)[:40]}`"
        )




@register_cmd("laddbots", group_only=True)
async def cmd_addbots(event, arg):
    try:
        if not arg:
            return await safe_edit(
                event,
                "❌ `~gwdinvitebots <count>`"
            )

        try:
            limit = int(arg.strip())
        except:
            return await safe_edit(event,
                "❌ 𝐈ɴᴠᴀʟɪᴅ 𝐍ᴜᴍʙᴇʀ"
            )

        # ───── HARD LIMIT SAFE ─────
        if limit < 1:
            limit = 1
        if limit > len(ADD_BOTS_LIST):
            limit = len(ADD_BOTS_LIST)

        chat = await event.get_chat()

        # ───── ADMIN CHECK ─────
        try:
            perms = await bot.get_permissions(event.chat_id, 'me')
            if not perms or not perms.is_admin:
                return await safe_edit(event,
                    "⚠️ 𝐈 𝐌ᴜsᴛ 𝐁ᴇ 𝐀ᴅᴍɪɴ"
                )
        except:
            return await safe_edit(event,
                "❌ 𝐏ᴇʀᴍ 𝐄ʀʀ"
            )

        if event.out:
            status = await event.edit(lofi_format(f"🔄 𝐀ᴅᴅɪɴɢ `{limit}` 𝐁ᴏᴛs..."))
        else:
            status = await event.reply(lofi_format(f"🔄 𝐀ᴅᴅɪɴɢ `{limit}` 𝐁ᴏᴛs..."))

        added = 0
        failed = 0
        already = 0

        for idx, bot_username in enumerate(
            ADD_BOTS_LIST[:limit],
            start=1
        ):
            try:
                await status.edit(lofi_format(f"🔄 `{idx}/{limit}` → @{bot_username}"))

                entity = await bot.get_entity(bot_username)

                # ───── GROUP TYPE AUTO ENGINE ─────
                if isinstance(chat, types.Chat):
                    await bot(functions.messages.AddChatUserRequest(
                        chat_id=chat.id,
                        user_id=entity,
                        fwd_limit=0
                    ))
                else:
                    await bot(functions.channels.InviteToChannelRequest(
                        channel=event.chat_id,
                        users=[entity]
                    ))

                added += 1
                await asyncio.sleep(2.5)

            except FloodWaitError as fw:
                await status.edit(lofi_format(f"⏳ 𝐅ʟᴏᴏᴅ `{fw.seconds}s`"))
                await asyncio.sleep(fw.seconds)

            except RPCError as e:
                err = str(e).lower()

                if (
                    "already" in err
                    or "participant" in err
                    or "member" in err
                ):
                    already += 1
                else:
                    failed += 1
                    print("ADD BOT RPC:", e)

                await asyncio.sleep(1)

            except Exception as e:
                failed += 1
                print("ADD BOT:", e)
                await asyncio.sleep(1)

        text = (
            "📊 𝐁ᴏᴛ 𝐀ᴅ𝐝 𝐑ᴇsᴜʟᴛ\n"
            "━━━━━━━━━━━━━━━\n"
            f"➕ `{added}` 𝐀ᴅᴅᴇᴅ\n"
            f"✅ `{already}` 𝐀ʟʀᴇᴀᴅʏ\n"
            f"❌ `{failed}` 𝐅ᴀɪʟᴇᴅ"
        )

        await status.edit(lofi_format(text))

    except Exception as e:
        await safe_edit(event,
            f"❌ 𝐀𝐝𝐝𝐁𝐨𝐭𝐬 → `{str(e)[:40]}`"
        )


@register_cmd("add")
async def cmd_add(event, arg):
    sender = event.sender_id
    if not sender:
        return await safe_edit(event, "❌ Could not verify the command sender.")
    if LOFI_HOSTER_WORKER:
        if sender != OWNER_ID:
            return await safe_edit(event, "❌ `~gwdinvite` is available to the account owner only.")
    elif not is_admin(sender):
        return await safe_edit(event, "❌ You must be an account admin to use `~gwdinvite`.")

    username = arg.strip()
    if not username or "," in username or any(character.isspace() for character in username):
        return await safe_edit(event, "❌ Usage: `~gwdinvite @username` (one invite per command).")

    chat = await event.get_chat()
    if not event.is_group or isinstance(chat, types.User):
        return await safe_edit(event, "⚠️ `~gwdinvite` works only in groups and supergroups.")

    try:
        perms = await bot.get_permissions(event.chat_id, "me")
    except Exception as error:
        print(f"[ADD] Could not verify invite permission: {str(error)[:100]}")
        return await safe_edit(event, "❌ Could not verify this account's group permissions.")
    if not perms or not getattr(perms, "is_admin", False):
        return await safe_edit(event, "❌ This account must be a group admin to invite users.")

    username = username.removeprefix("@").strip()
    if not username:
        return await safe_edit(event, "❌ Usage: `~gwdinvite @username` (one invite per command).")

    try:
        entity = await bot.get_entity(username)
    except Exception as error:
        print(f"[ADD] Could not resolve @{username}: {str(error)[:100]}")
        return await safe_edit(event, f"❌ Could not find `@{username}`.")

    try:
        if isinstance(chat, types.Chat):
            await bot(functions.messages.AddChatUserRequest(
                chat_id=chat.id,
                user_id=entity,
                fwd_limit=0
            ))
        else:
            await bot(functions.channels.InviteToChannelRequest(
                channel=event.chat_id,
                users=[entity]
            ))
    except FloodWaitError as error:
        return await safe_edit(event, f"⏳ Telegram rate-limited this invite for {error.seconds}s.")
    except RPCError as error:
        message = str(error).lower()
        if "already" in message or "participant" in message or "member" in message:
            return await safe_edit(event, f"⚠️ `@{username}` is already in this group.")
        print(f"[ADD] Invite failed for @{username}: {str(error)[:100]}")
        return await safe_edit(event, f"❌ Telegram could not invite `@{username}`.")
    except Exception as error:
        print(f"[ADD] Invite failed for @{username}: {str(error)[:100]}")
        return await safe_edit(event, f"❌ Could not invite `@{username}`.")

    await safe_edit(event, f"✅ Invited `@{username}`. No admin rights were granted.")


@register_cmd("lmusic")
async def cmd_music(event, arg):

    if not arg:
        return await safe_edit(event,
            "❌ 𝐔sᴀɢᴇ → `~music <song>`"
        )

    query = arg.strip()

    frames = [
        "▰▱▱▱▱",
        "▰▰▱▱▱",
        "▰▰▰▱▱",
        "▰▰▰▰▱",
        "▰▰▰▰▰"
    ]

    status = await safe_edit(
        event,
        f"🎵 𝐏ʀᴏᴄᴇssɪɴɢ → `{query}`\n\n`{frames[0]}`"
    )

    stop_loader = asyncio.Event()

    async def loader():
        i = 0
        while not stop_loader.is_set():
            try:
                    await status.edit(lofi_format(f"🎵 𝐏ʀᴏᴄᴇssɪɴɢ → `{query}`\n\n`{frames[i % 5]}`"))
            except:
                pass
            i += 1
            await asyncio.sleep(1)

    loader_task = asyncio.create_task(loader())

    async def voice_music():
        try:
            import yt_dlp, glob, os, re, asyncio

            loop = asyncio.get_event_loop()

            ydl_opts = {
                "format": "bestaudio[abr<=128]/bestaudio",
                "outtmpl": "vn_%(id)s.%(ext)s",
                "quiet": True,
                "default_search": "ytsearch1",
                "noplaylist": True,
                "retries": 2
            }

            def dl():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(query, download=True)

            info = await loop.run_in_executor(None, dl)

            if "entries" in info:
                info = info["entries"][0]

            vid = info.get("id")
            title = info.get("title") or query
            dur = info.get("duration") or 0

            mins = dur // 60
            secs = dur % 60
            dtext = f"{mins}:{secs:02d}"

            files = glob.glob(f"vn_{vid}.*")
            if not files:
                stop_loader.set()
                loader_task.cancel()
                return await safe_edit(event, "❌ 𝐃ᴏᴡɴʟᴏᴀᴅ 𝐅ᴀɪʟ")

            src = files[0]

            clean = re.sub(r"[^\w\s-]", "", title).strip()[:40]
            new = f"{clean}.ogg"

            try:
                os.rename(src, new)
            except:
                new = src

            stop_loader.set()
            loader_task.cancel()

            await safe_edit(event,
                f"🎙️ 𝐒ᴇɴᴅɪɴɢ → `{clean}`"
            )

            await bot.send_file(
                event.chat_id,
                new,
                caption=lofi_format(
                    "🎵 𝐌ᴜsɪᴄ\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"📀 `{clean}`\n"
                    f"⏱ `{dtext}`"
                )
            )

            try:
                os.remove(new)
            except:
                pass

            await event.delete()

        except Exception as e:
            stop_loader.set()
            loader_task.cancel()
            await safe_edit(event,
                f"❌ 𝐌ᴜsɪᴄ → `{str(e)[:60]}`"
            )

    asyncio.create_task(voice_music())
 
# ────────────────────────────────────────────────
#                   AUTO HANDLER
# ────────────────────────────────────────────────
@bot.on(events.NewMessage)
async def auto_handler(event):

    # ⭐ ignore self messages fast
    if event.out:
        return

    sender = event.sender_id
    chat = event.chat_id

    # ⭐ null safety
    if not sender:
        return

    if (
        auto_reply_enabled
        and auto_reply_target_user_id == sender
        and auto_reply_text
    ):
        message_id = getattr(event, "id", None)
        if message_id is not None:
            message_key = (chat, message_id)
            if message_key in auto_reply_seen_messages:
                return
            auto_reply_seen_messages[message_key] = None
            if len(auto_reply_seen_messages) > AUTO_REPLY_DEDUPE_LIMIT:
                auto_reply_seen_messages.popitem(last=False)
        try:
            await event.reply(auto_reply_text)
        except FloodWaitError as error:
            print(
                f"[AUTO_REPLY_FLOOD_WAIT] chat={chat} "
                f"seconds={error.seconds}"
            )
        except (RPCError, OSError) as error:
            print(
                f"[AUTO_REPLY_ERROR] chat={chat} "
                f"error_type={type(error).__name__}"
            )
        except Exception as error:
            print(
                f"[AUTO_REPLY_ERROR] chat={chat} "
                f"error_type={type(error).__name__}"
            )
        return

    # 👑 OWNER / SUPER ADMIN PROTECT
    if is_owner(sender) or sender in PROOT_ADMINS:
        return

    # ⭐ GLOBAL / LOCAL MUTE ENGINE
    if sender in global_muted or is_locally_muted(chat, sender):
        try:
            await event.delete()
        except:
            pass
        return

    # ⭐ GROUP LOCK ENGINE
    if chat in group_locks:
        try:
            if not is_admin(sender):
                try:
                    await event.delete()
                except:
                    pass
                return
        except:
            pass

    # ⭐ MAIN AUTO ACTION ENGINE
    try:

        # 🌊 FLAG RAID
        if sender in flag_users:
            try:
                await event.reply(lofi_format(random.choice(flag_texts)))
            except FloodWaitError as fw:
                await asyncio.sleep(fw.seconds)
            except:
                pass

        # 💜 HEART RAID
        if sender in hrr_users:
            try:
                await event.reply(lofi_format(random.choice(heart_replies)))
            except FloodWaitError as fw:
                await asyncio.sleep(fw.seconds)
            except:
                pass

        # 🤣 RR RAID (GLOBAL)
        if sender in rr_users:
            try:
                bot_msg = await event.reply(lofi_format(random.choice(fun_texts)))

                try:
                    await bot(functions.messages.SendReactionRequest(
                        peer=chat,
                        msg_id=bot_msg.id,
                        reaction=[types.ReactionEmoji(
                            emoticon="🤣"
                        )]
                    ))
                except:
                    pass

            except FloodWaitError as fw:
                await asyncio.sleep(fw.seconds)
            except:
                pass

    except Exception as e:
        print(f"[AUTO_HANDLER_ERR] {str(e)[:80]}")


@bot.on(events.NewMessage(outgoing=True))
async def auto_react(event):

    # ⭐ FAST EXIT
    emoji = auto_react_emoji
    if not emoji:
        return

    # ⭐ MESSAGE VALIDATION
    msg_id = event.id
    chat = event.chat_id

    if not msg_id or not chat:
        return

    try:

        # ⭐ REACTION ENGINE
        await bot(functions.messages.SendReactionRequest(
            peer=chat,
            msg_id=msg_id,
            reaction=[
                types.ReactionEmoji(
                    emoticon=emoji
                )
            ]
        ))

    except FloodWaitError as fw:
        try:
            await asyncio.sleep(fw.seconds)
        except:
            pass

    except RPCError:
        # ⭐ silent rpc fail safe
        pass

    except Exception:
        # ⭐ unknown crash isolation
        pass

# ────────────────────────────────────────────────
#                   COMMAND HANDLER
# ────────────────────────────────────────────────
@bot.on(events.NewMessage)
async def dispatcher(event):

    text = event.raw_text

    # ⭐ basic validation ultra fast
    if not text:
        return

    sender = event.sender_id

    if text.startswith(PROOT_PREFIX):
        if not sender or not is_proot(sender):
            return
        body = text[1:].strip()
        is_proot_cmd = True
    elif not text.startswith(COMMAND_PREFIXES):
        return
    else:
        if not sender:
            return
        body = text[1:].strip()
        is_proot_cmd = is_proot(sender)

    if not body:
        return

    parts = body.split(maxsplit=1)

    cmd = parts[0].lower().strip()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # ⭐ command lookup safety
    cmd_data = commands.get(cmd)
    if not cmd_data:
        return

    if LOFI_HOSTER_WORKER:
        if not worker_command_allowed(cmd_data, sender):
            if sender == OWNER_ID or sender in admins:
                handler_name = getattr(cmd_data.get("func"), "__name__", "")
                if handler_name in (
                    WORKER_OWNER_ONLY_HANDLERS | GLOBAL_REPLY_OWNER_ONLY_HANDLERS
                ):
                    reason = "This command is available to the configured account owner only."
                else:
                    reason = WORKER_DISABLED_HANDLER_REASONS.get(
                        handler_name,
                        "This command is unavailable under the hosted worker policy.",
                    )
                await safe_edit(event, f"⚠️ `{COMMAND_PREFIX}{cmd}` is unavailable. {reason}")
            return

    handler_name = getattr(cmd_data.get("func"), "__name__", "")
    if handler_name in GLOBAL_REPLY_OWNER_ONLY_HANDLERS and not is_owner(sender):
        return

    if cmd in PROOT_COMMANDS and not text.startswith(PROOT_PREFIX):
        if not (
            LOFI_HOSTER_WORKER
            and cmd_data["func"].__name__ in {"cmd_proot_help", "cmd_proot_whoami"}
            and text.startswith(COMMAND_PREFIX)
        ):
            return
    if LOFI_HOSTER_WORKER and cmd_data["func"].__name__ in WORKER_OWNER_ONLY_HANDLERS and sender != OWNER_ID:
        return

    # ⭐ null sender protection (channel / anon admin)
    if not sender:
        return

    # ⚡ permission branch
    is_owner_flag = is_owner(sender)

    if not is_proot_cmd and not is_owner_flag:

        if sender not in admins:
            try:
                await event.reply(lofi_format("𝐋ᴏꜰɪ 𝐒ɪᴅᴇ 𝐕ɪʙᴇ 𝐀ᴄᴛɪᴠᴇ — 𝐃ʀɪꜰᴛ 𝐒ᴏꜰᴛʟʏ 🌙✨"))
            except:
                pass
            return

        if cmd in {"copy", "normal", "addadmin", "deladmin", "admins"}:
            await safe_edit(event, "❌ 𝐎ᴡɴᴇʀ 𝐎ɴʟʏ 𝐂ᴍᴅ")
            return

    # ⭐ group restriction engine
    if cmd_data.get("group_only"):
        try:
            if not event.is_group:
                await safe_edit(event, "⚠️ 𝐆ʀᴏᴜᴘ 𝐎ɴʟʏ 𝐂ᴏᴍᴍᴀɴᴅ")
                return
        except:
            return

    # ⭐ reply requirement engine
    if cmd_data.get("needs_reply"):
        try:
            if not event.is_reply and not arg:
                await safe_edit(
                    event,
                    f"❌ 𝐑ᴇᴘʟʏ 𝐎ʀ 𝐏ᴀss 𝐓ᴀʀɢᴇᴛ\n👉 ~{cmd} @user / id",
                )
                return
        except:
            return

    # ⭐ command execution core
    try:

        await cmd_data["func"](event, arg)

    except FloodWaitError as e:
        try:
            await safe_edit(
                event,
                f"⏳ 𝐅ʟᴏᴏᴅ𝐖ᴀɪᴛ → `{e.seconds}s`"
            )
        except:
            pass
    except Exception as error:
        handler_name = getattr(cmd_data.get("func"), "__name__", "unknown")
        print(
            f"[COMMAND_ERROR] command={cmd} handler={handler_name} "
            f"error_type={type(error).__name__}"
        )
        await safe_edit(
            event,
            f"❌ `~{cmd}` could not complete because of an internal error. "
            "Please check the worker log and try again.",
        )

def replace_registry_with_new_command_names() -> None:
    handlers_by_name = {
        getattr(command_data.get("func"), "__name__", ""): command_data
        for command_data in commands.values()
    }
    new_registry = {}
    for handler_name, command_name in NEW_COMMAND_NAMES.items():
        command_data = handlers_by_name.get(handler_name)
        if command_data is None:
            raise ValueError(f"No registered handler for new command name: {handler_name}")
        if command_name in new_registry:
            raise ValueError(f"Duplicate new command name: {command_name}")
        new_registry[command_name] = command_data

    for alias, handler_name in NEW_COMMAND_ALIASES.items():
        command_data = handlers_by_name.get(handler_name)
        if command_data is None:
            raise ValueError(f"No registered handler for new command alias: {alias}")
        if alias in new_registry:
            raise ValueError(f"Duplicate new command name or alias: {alias}")
        new_registry[alias] = command_data

    commands.clear()
    commands.update(new_registry)
    PROOT_COMMANDS.clear()
    PROOT_COMMANDS.update(
        command_name
        for handler_name, command_name in NEW_COMMAND_NAMES.items()
        if handler_name in {
            "cmd_proot_help",
            "cmd_proot_whoami",
            "cmd_proot_addadmin",
            "cmd_proot_addowner",
            "cmd_proot_removeadmin",
            "cmd_proot_removeowner",
        }
    )

# Install only the renamed command interface.
replace_registry_with_new_command_names()

    
# ────────────────────────────────────────────────
#                   START
# ────────────────────────────────────────────────
async def main():

    try:
        if LOFI_HOSTER_WORKER:
            loop = asyncio.get_running_loop()
            for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    shutdown_signal,
                    lambda: asyncio.create_task(bot.disconnect()),
                )

        # ⭐ start engine
        await bot.start()

        me = await bot.get_me()

        uname = f"@{me.username}" if me.username else "NoUsername"

        print("⋆.𐙚˚  LOFI VOID CORE 𖨆♡  —  System Boot  𖤐  ⋆.𐙚˚")
        print(f"✧  Logged in as → {me.first_name} ({uname})  ✧")
        print(f"𓂃  User ID → {me.id}  𓂃")
        print("⪩ෆ⪨  •  Ready  •  ꩜  •  Listening for commands")

        # ⭐ connection watchdog
        while True:
            try:
                await bot.run_until_disconnected()
                break
            except (ConnectionError, OSError):
                print("⚠️ Connection Lost → Reconnecting...")
                await asyncio.sleep(3)

    except KeyboardInterrupt:
        print("\n🛑 Userbot stopped manually")

    except Exception as e:
        print(f"\n❌ Startup Error → {str(e)[:80]}")

    finally:
        try:
            if bot.is_connected():
                await bot.disconnect()
        except:
            pass


if __name__ == "__main__":
    if LOFI_HOSTER_WORKER:
        if len(sys.argv) != 2 or not re.fullmatch(r"U\d{3,8}", sys.argv[1]):
            raise SystemExit("Worker requires an account ID such as U001")
        account_id = sys.argv[1].upper()
        data_dir = DEFAULT_DATA_DIR
        expected_work = (data_dir / "accounts" / account_id).resolve()
        expected_session = (data_dir / "sessions" / account_id).resolve()
        if (
            account_id != os.environ.get("LOFI_ACCOUNT_ID")
            or BASE_DIR != expected_work
            or Path(SESSION).resolve() != expected_session
        ):
            raise SystemExit("Worker account paths do not match its assigned account")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
