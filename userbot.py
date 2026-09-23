# -*- coding: utf-8 -*-
import os
import random
import logging
import asyncio

from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityPre,
    MessageEntityTextUrl, MessageEntityUrl, MessageEntityMention, MessageEntityMentionName,
    MessageEntityHashtag, MessageEntityBotCommand, MessageEntityEmail, MessageEntityPhone,
    MessageEntityStrike, MessageEntityUnderline, MessageEntitySpoiler, MessageEntityBlockquote,
    MessageEntityCustomEmoji, MessageMediaWebPage,
)
from telethon.tl.types import InputMediaWebPage  # for checking media type

logger = logging.getLogger(__name__)

# === ENV VARS KHUSUS USERBOT ===
USERBOT_API_ID = int(os.environ.get("USERBOT_API_ID", "0"))
USERBOT_API_HASH = os.environ.get("USERBOT_API_HASH", "")
USERBOT_SESSION = os.environ.get("USERBOT_SESSION", "")

CHANNEL_UTAMA_ID = os.environ.get("CHANNEL_ID")          # channel tujuan
BOLEHVIP_USERNAME = os.environ.get("BOLEHVIP_USERNAME", "bolehvip")

userbot_client = TelegramClient(
    StringSession(USERBOT_SESSION),
    USERBOT_API_ID,
    USERBOT_API_HASH,
)

_target_cache = None
_sendas_cache = None


async def start_userbot():
    """Panggil ini sekali di on_startup bot utama."""
    if not USERBOT_SESSION:
        logger.warning("⚠️ USERBOT_SESSION kosong, fitur menfess VIP send_as TIDAK aktif.")
        return
    await userbot_client.connect()
    if not await userbot_client.is_user_authorized():
        raise RuntimeError(
            "Session userbot tidak valid/expired. Generate ulang StringSession "
            "(lihat generate_session.py)."
        )
    me = await userbot_client.get_me()
    logger.info(f"✅ Userbot VIP siap: {me.first_name} (id={me.id})")


# ==================== STEP 6: REFRESH ENTITY CACHE ====================
async def _refresh_entity_cache():
    """Refresh cache entity channel & send_as, dipanggil saat error atau startup."""
    global _target_cache, _sendas_cache
    _target_cache = await userbot_client.get_input_entity(CHANNEL_UTAMA_ID)
    _sendas_cache = await userbot_client.get_input_entity(BOLEHVIP_USERNAME)
    logger.info("🔄 Entity cache userbot direfresh.")


# --- Mapping entity dari python-telegram-bot (PTB) ke tipe Telethon ---
_PTB_TO_TL_ENTITY = {
    "bold": MessageEntityBold,
    "italic": MessageEntityItalic,
    "code": MessageEntityCode,
    "pre": MessageEntityPre,
    "text_link": MessageEntityTextUrl,
    "url": MessageEntityUrl,
    "mention": MessageEntityMention,
    "text_mention": MessageEntityMentionName,
    "hashtag": MessageEntityHashtag,
    "bot_command": MessageEntityBotCommand,
    "email": MessageEntityEmail,
    "phone_number": MessageEntityPhone,
    "strikethrough": MessageEntityStrike,
    "underline": MessageEntityUnderline,
    "spoiler": MessageEntitySpoiler,
    "blockquote": MessageEntityBlockquote,
    "custom_emoji": MessageEntityCustomEmoji,
}


def _convert_entities(ptb_entities):
    """Ubah list MessageEntity dari python-telegram-bot jadi tipe TL Telethon."""
    tl_entities = []
    for e in (ptb_entities or []):
        cls = _PTB_TO_TL_ENTITY.get(e.type)
        if not cls:
            logger.warning(f"Entity tipe '{e.type}' belum di-mapping, dilewati.")
            continue
        kwargs = {"offset": e.offset, "length": e.length}
        if e.type == "text_link":
            kwargs["url"] = e.url
        elif e.type == "text_mention" and e.user:
            kwargs["user_id"] = e.user.id
        elif e.type == "pre":
            kwargs["language"] = getattr(e, "language", "") or ""
        elif e.type == "custom_emoji":
            raw_id = getattr(e, "custom_emoji_id", None)
            if raw_id is None:
                logger.warning("Entity custom_emoji tanpa custom_emoji_id, dilewati.")
                continue
            kwargs["document_id"] = int(raw_id)
        try:
            tl_entities.append(cls(**kwargs))
        except Exception as ex:
            logger.warning(f"Skip entity {e.type} karena gagal convert: {ex}")
    return tl_entities


# ==================== STEP 5 + 6: KIRIM VIP DENGAN RETRY & REFRESH ====================
async def kirim_menfess_vip(teks: str, ptb_entities=None, target_url: str = None) -> int:
    """
    Kirim menfess via userbot dengan send_as = @bolehvip (atau username lain).
    Jika gagal karena entity basi, refresh cache dan retry sekali.
    """
    global _target_cache, _sendas_cache

    if not USERBOT_SESSION:
        raise RuntimeError("USERBOT_SESSION belum di-set.")

    # Pastikan cache ada
    if _target_cache is None or _sendas_cache is None:
        await _refresh_entity_cache()

    tl_entities = _convert_entities(ptb_entities)

    # --- INJECT LINK PREVIEW via ZERO-WIDTH SPACE (tetap dipertahankan) ---
    if target_url:
        clean_url = target_url.replace("telegram.me", "t.me")
        blank_space = "\u200b"
        offset = len(teks.encode('utf-16-le')) // 2
        length = len(blank_space.encode('utf-16-le')) // 2
        teks += blank_space
        tl_entities.append(MessageEntityTextUrl(offset=offset, length=length, url=clean_url))

    random_id = random.randint(0, 2**63 - 1)

    # --- EKSEKUSI DENGAN RETRY (2x) ---
    for attempt in range(2):
        try:
            result = await userbot_client(functions.messages.SendMessageRequest(
                peer=_target_cache,
                message=teks,
                send_as=_sendas_cache,
                entities=tl_entities,
                random_id=random_id,
            ))

            # Ambil message_id dari response
            new_message_id = None
            for upd in getattr(result, "updates", []) or []:
                msg = getattr(upd, "message", None)
                if msg is not None:
                    new_message_id = msg.id
                    break

            if new_message_id is None:
                raise RuntimeError("Gagal membaca message_id dari response Telegram.")

            return new_message_id

        except Exception as e:
            logger.warning(f"[Attempt {attempt+1}] Gagal kirim VIP: {e}")
            if attempt == 0:
                # Refresh entity cache & coba sekali lagi
                await _refresh_entity_cache()
            else:
                # Percobaan kedua gagal, lempar exception ke pemanggil
                raise RuntimeError(f"Kirim VIP gagal setelah 2 percobaan: {e}")

    # Seharusnya tidak sampai sini
    raise RuntimeError("Kirim VIP gagal tanpa exception yang jelas.")


# ==================== STEP 7: FUNGSI CEK PREVIEW ====================
async def has_preview(message_id: int) -> bool:
    """
    Cek apakah pesan di channel utama memiliki preview link (MessageMediaWebPage).
    """
    if not USERBOT_SESSION:
        return False
    try:
        chat_id = int(CHANNEL_UTAMA_ID) if CHANNEL_UTAMA_ID.lstrip('-').isdigit() else CHANNEL_UTAMA_ID
        msg = await userbot_client.get_messages(chat_id, ids=message_id)
        if not msg:
            return False
        # Cek apakah media adalah MessageMediaWebPage
        if msg.media and isinstance(msg.media, MessageMediaWebPage):
            return True
        return False
    except Exception as e:
        logger.error(f"Gagal cek preview message {message_id}: {e}")
        return False


# ==================== STEP 8: PERKUAT RELOAD PREVIEW ====================
async def reload_preview_userbot(message_id: int, retries: int = 2) -> bool:
    """
    Memancing ulang link preview dengan mengedit pesan menjadi titik,
    lalu mengembalikan teks asli. Dilengkapi retry dan verifikasi.
    """
    if not USERBOT_SESSION:
        return False

    chat_id = int(CHANNEL_UTAMA_ID) if CHANNEL_UTAMA_ID.lstrip('-').isdigit() else CHANNEL_UTAMA_ID

    for attempt in range(retries):
        try:
            # 1. Ambil pesan asli
            msg = await userbot_client.get_messages(chat_id, ids=message_id)
            if not msg or not msg.message:
                logger.warning(f"Pesan {message_id} tidak ditemukan atau bukan teks.")
                return False

            teks_asli = msg.message
            entitas_asli = msg.entities

            # 2. Edit menjadi titik
            await userbot_client.edit_message(
                entity=chat_id,
                message=message_id,
                text="."
            )

            # 3. Jeda lebih panjang agar cache Telegram invalidate
            await asyncio.sleep(2.5)

            # 4. Kembalikan teks asli + paksa preview
            await userbot_client.edit_message(
                entity=chat_id,
                message=message_id,
                text=teks_asli,
                formatting_entities=entitas_asli,
                parse_mode=None,          # penting: jangan parse markdown
                link_preview=True
            )

            # 5. Verifikasi: tunggu sebentar lalu cek apakah preview sudah muncul
            await asyncio.sleep(2)
            if await has_preview(message_id):
                logger.info(f"✅ Preview berhasil direload untuk message {message_id}")
                return True
            else:
                logger.warning(f"Preview belum muncul untuk {message_id}, attempt {attempt+1}")

        except Exception as e:
            logger.warning(f"Reload preview attempt {attempt+1} gagal: {e}")

        # Jika masih ada percobaan tersisa, tunggu sebelum retry
        if attempt < retries - 1:
            await asyncio.sleep(3)

    logger.error(f"❌ Gagal reload preview untuk message {message_id} setelah {retries} percobaan.")
    return False
