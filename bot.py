# -*- coding: utf-8 -*-
import json
import logging
import re
import markdown
import os
import asyncio

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes, CallbackContext
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, MessageEntity
from supabase import create_client

# Tarik data dari Environment Variables (Heroku)
try:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    CHANNEL_ID = os.environ.get('CHANNEL_ID')
    
    # Gunakan default 0 agar tidak crash jika variabel belum diset di Heroku
    GROUP_ID_DISKUSI = int(os.environ.get('GROUP_ID_DISKUSI', 0))
    ADMIN_GROUP_ID = int(os.environ.get('ADMIN_GROUP_ID', 0))
    LOG_GROUP_ID = int(os.environ.get('LOG_GROUP_ID', 0))
    
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
except Exception as e:
    print(f"⚠️ Error mengambil Environment Variables: {e}")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

bot_active = True
MENFESS_MODE = "auto" # Cache default

WAITING_USERNAME = 1

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error(f"Gagal koneksi ke Supabase: {e}")

CACHE_HASHTAGS = []
required_channels = []
CACHE_BANNED_USERS = []
CACHE_COMSECT_OFF = set() 
CACHE_BAD_WORDS = set() # CACHE LOKAL UNTUK BANNED WORDS

async def update_settings_cache():
    global MENFESS_MODE
    try:
        response = supabase.table("bot_settings").select("value").eq("key", "menfess_mode").execute()
        if hasattr(response, 'data') and response.data:
            MENFESS_MODE = response.data[0]["value"]
        else:
            supabase.table("bot_settings").insert({"key": "menfess_mode", "value": "auto"}).execute()
            MENFESS_MODE = "auto"
    except Exception as e:
        logger.error(f"Gagal memuat setting bot: {e}")

async def update_hashtags_cache():
    global CACHE_HASHTAGS
    try:
        response = supabase.table("triggered_hashtags").select("hashtag").eq("active", True).execute()
        CACHE_HASHTAGS = [row["hashtag"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e:
        logger.error(f"Gagal memuat cache hashtag: {e}")

async def update_badwords_cache():
    global CACHE_BAD_WORDS
    try:
        response = supabase.table("bad_words").select("word").execute()
        CACHE_BAD_WORDS = {row["word"].lower() for row in response.data} if hasattr(response, 'data') and response.data else set()
    except Exception as e:
        logger.error(f"Gagal memuat cache bad words: {e}")

async def update_required_channels_cache():
    global required_channels
    try:
        response = supabase.table('required_channels').select("channel_username").execute()
        required_channels = [row["channel_username"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e:
        logger.error(f"Gagal memuat required channels: {e}")

async def update_banned_users_cache():
    global CACHE_BANNED_USERS
    try:
        response = supabase.table('banned_users').select("user_id").execute()
        CACHE_BANNED_USERS = [row["user_id"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e:
        logger.error(f"Gagal memuat banned users: {e}")

async def on_startup(application: Application):
    try:
        me = await application.bot.get_me()
        logger.info(f"✅ Bot siap: @{me.username} (id={me.id})")
        await update_settings_cache()
        await update_hashtags_cache()
        await update_badwords_cache()
        await update_required_channels_cache()
        await update_banned_users_cache()
    except Exception as e:
        logger.error(f"⚠️ Gagal get_me saat startup: {e}")

def save_required_channels(channels):
    try:
        supabase.table('required_channels').delete().neq("channel_username", "").execute()
        for channel in channels:
            supabase.table('required_channels').insert({"channel_username": channel}).execute()
    except Exception as e:
        logger.error(f"Gagal menyimpan required channels: {e}")

async def check_subscription(user_id, context: CallbackContext):
    if not required_channels: return True
    for channel in required_channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']: return False
        except Exception: return False
    return True

# === FITUR BANNED WORDS ===
async def add_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    raw_text = update.message.text.split(maxsplit=1)
    if len(raw_text) < 2: 
        return await update.message.reply_text("Format: /addbadwords kata1, kata2, kata3")
    
    words = [w.strip().lower() for w in raw_text[1].split(',')]
    inserted = 0
    for w in words:
        if w:
            try:
                supabase.table("bad_words").upsert({"word": w}).execute()
                inserted += 1
            except Exception: pass
    
    await update_badwords_cache()
    await update.message.reply_text(f"✅ {inserted} kata terlarang berhasil ditambahkan!")

async def remove_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    raw_text = update.message.text.split(maxsplit=1)
    if len(raw_text) < 2: 
        return await update.message.reply_text("Format: /removebadwords kata1, kata2")
    
    words = [w.strip().lower() for w in raw_text[1].split(',')]
    deleted = 0
    for w in words:
        if w:
            try:
                supabase.table("bad_words").delete().eq("word", w).execute()
                deleted += 1
            except Exception: pass
            
    await update_badwords_cache()
    await update.message.reply_text(f"✅ {deleted} kata terlarang berhasil dihapus!")

async def list_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not CACHE_BAD_WORDS:
        return await update.message.reply_text("Daftar kata terlarang saat ini kosong.")
    
    word_list = ", ".join(sorted(CACHE_BAD_WORDS))
    await update.message.reply_text(f"🚫 *Daftar Kata Terlarang:*\n\n{word_list}", parse_mode="Markdown")

# === FITUR BLOCK USER ===
async def block_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Format: /block <user_id>")
    try:
        target_id = int(context.args[0])
        supabase.table("banned_users").upsert({"user_id": target_id}).execute()
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil diblokir dari bot.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("❌ Gagal memblokir user. Pastikan format ID benar.")

async def unblock_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Format: /unblock <user_id>")
    try:
        target_id = int(context.args[0])
        supabase.table("banned_users").delete().eq("user_id", target_id).execute()
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil di-unblock.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("❌ Gagal unblock user.")

# === FITUR UBAH MODE MENFESS ===
async def set_mode_auto(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "auto"
    try:
        supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "auto"}).execute()
    except Exception as e:
        logger.error(f"Gagal simpan mode auto ke DB: {e}")
    await update.message.reply_text("✅ Mode menfess diubah ke *AUTO*. Menfess akan langsung terkirim ke channel (Comsect OFF Otomatis, Teks Only).", parse_mode="Markdown")

async def set_mode_manual(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "manual"
    try:
        supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "manual"}).execute()
    except Exception as e:
        logger.error(f"Gagal simpan mode manual ke DB: {e}")
    await update.message.reply_text("⏸️ Mode menfess diubah ke *MANUAL*. Menfess akan masuk ke grup admin untuk direview.", parse_mode="Markdown")

# === HASHTAG & SETTINGS LAINNYA ===
async def add_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /addhashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").upsert({"hashtag": hashtag}).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` berhasil ditambahkan!", parse_mode="Markdown")

async def remove_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /removehashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").delete().eq("hashtag", hashtag).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"❌ Hashtag `{hashtag}` berhasil dihapus!", parse_mode="Markdown")

async def enable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /enablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").update({"active": True}).eq("hashtag", hashtag).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` diaktifkan!", parse_mode="Markdown")

async def disable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /disablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").update({"active": False}).eq("hashtag", hashtag).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"⚠️ Hashtag `{hashtag}` dinonaktifkan!", parse_mode="Markdown")

async def set_required_channels(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /setrequired @channel1 @channel2")
    global required_channels
    required_channels = context.args
    save_required_channels(required_channels)
    await update.message.reply_text(f"Daftar channel wajib diikuti telah diperbarui: {', '.join(required_channels)}")

async def save_user(user_id, username):
    try:
        supabase.table("users").upsert({"user_id": user_id, "username": username}, on_conflict=["user_id"]).execute()
    except Exception: pass

async def start(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private": return
    user_id = update.effective_user.id

    if user_id in CACHE_BANNED_USERS:
        return await update.message.reply_text("❌ Akses kamu ke bot ini telah diblokir.")

    await save_user(user_id, update.effective_user.username)

    if await check_subscription(user_id, context):
        await update.message.reply_text(
            "Halo Kens, selamat datang di *Kitheons*! ☕️\n\n"
            "𔐼 *Kitheons:* [@kitheons](https://t.me/kitheons)\n"
            "𔐼 *Ch Arsip:* [@kithives](https://t.me/kithives)\n\n"
            "Ketuk /menu untuk menampilkan navigasi.\n"
            "*(Semua pesan yang kamu kirim otomatis diajukan sebagai menfess)*", parse_mode="Markdown"
        )
    else:
        keyboard = [[InlineKeyboardButton("Join Channels", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

# === ALUR MENFESS (CONVERSATION HANDLER) ===
async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE
    if update.effective_chat.type != "private": return ConversationHandler.END
    if not bot_active: 
        await update.message.reply_text("Bot sedang dipause oleh admin.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    display_name = f"@{username}" if username else first_name

    # Cek Blokir
    if user_id in CACHE_BANNED_USERS:
        await update.message.reply_text("❌ Pesan ditolak. Akses kamu ke bot ini telah diblokir.")
        return ConversationHandler.END

    # === BALASAN ANONIM VIA TEKS TERSEMBUNYI ===
    if update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        match = re.search(r"#ID:(\d+)", replied_text)
        if match:
            try:
                comment_msg_id = int(match.group(1))
                if update.message.text:
                    await context.bot.send_message(
                        chat_id=GROUP_ID_DISKUSI, text=f"🗣️ *Balasan Sender:*\n\n{update.message.text}",
                        reply_to_message_id=comment_msg_id, parse_mode="Markdown"
                    )
                else:
                    await context.bot.copy_message(
                        chat_id=GROUP_ID_DISKUSI, from_chat_id=user_id, message_id=update.message.message_id,
                        reply_to_message_id=comment_msg_id, caption=f"🗣️ *Balasan Sender:*\n\n{update.message.caption or ''}",
                        parse_mode="Markdown"
                    )
                await update.message.reply_text("✅ Balasan anonim berhasil dikirim ke pengomentar!")
            except Exception as e:
                logger.error(f"Gagal memproses balasan anonim: {e}")
                await update.message.reply_text("❌ Gagal mengirim balasan anonim, mungkin komentar aslinya dihapus.")
            return ConversationHandler.END

    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return ConversationHandler.END

    pesan_teks = update.message.text or update.message.caption or ""
    pesan_teks_lower = pesan_teks.lower()

    # VALIDASI BANNED WORDS (Berlaku buat semua mode)
    for bw in CACHE_BAD_WORDS:
        if re.search(rf'\b{re.escape(bw)}\b', pesan_teks_lower):
            await update.message.reply_text("❌ Menfess ditolak karena mengandung kata-kata yang dilarang oleh base.")
            return ConversationHandler.END

    # LOGIKA PENGIRIMAN MENFESS
    if MENFESS_MODE == "auto":
        # Sesi auto: HANYA TEKS, NO MEDIA
        if not update.message.text:
            await update.message.reply_text("❌ Sesi /auto sedang aktif! Kamu hanya diperbolehkan mengirim pesan teks saja (tanpa media).")
            return ConversationHandler.END

        if len(update.message.text) > 70:
            await update.message.reply_text(
                f"❌ Menfess terlalu panjang! Maksimal 70 karakter ya. "
                f"(Pesanmu saat ini: {len(update.message.text)} karakter)."
            )
            return ConversationHandler.END

        # VALIDASI ANTI MENTION
        ada_mention = False
        if update.message.entities:
            for ent in update.message.entities:
                if ent.type == "mention": ada_mention = True; break
        
        if ada_mention or re.search(r'(?:^|\s)@/?\w+', pesan_teks):
            await update.message.reply_text("❌ Menfess dilarang menyertakan mention atau username! (Link URL tetap diperbolehkan).")
            return ConversationHandler.END

        # Simpan state ke context untuk input username berikutnya
        context.user_data['teks_menfess'] = update.message.text
        context.user_data['entities'] = update.message.entities or []

        await update.message.reply_text("⏳ Teks diterima! Sekarang kirimkan **username** kamu untuk di-hyperlink (contoh: radit atau @radit).\n\n*Ketik /cancel untuk membatalkan.*", parse_mode="Markdown")
        return WAITING_USERNAME

    else:
        # Flow MANUAL REVIEW KE ADMIN GRUP (Persis kayak awal)
        try:
            fw_msg = await context.bot.copy_message(chat_id=ADMIN_GROUP_ID, from_chat_id=user_id, message_id=update.message.message_id)

            keyboard = [
                [
                    InlineKeyboardButton("✅ Acc (CS ON)", callback_data=f"mf|A_ON|{user_id}|{update.message.message_id}"),
                    InlineKeyboardButton("🔕 Acc (CS OFF)", callback_data=f"mf|A_OFF|{user_id}|{update.message.message_id}")
                ],
                [InlineKeyboardButton("❌ Tolak", callback_data=f"mf|R|{user_id}|{update.message.message_id}")]
            ]

            review_text = f"🚨 *REVIEW MENFESS*\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`"
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=review_text,
                reply_to_message_id=fw_msg.message_id,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

            await update.message.reply_text("⏳ Menfess kamu sedang masuk ke antrean admin untuk direview. Mohon tunggu ya!")
        except Exception as e:
            logger.error(f"Error kirim manual review: {e}")
            await update.message.reply_text("❌ Gagal mengirim menfess ke admin review.")
            
        return ConversationHandler.END


async def handle_username(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    display_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
    
    target_username = update.message.text.strip().replace("@", "")
    teks_asli = context.user_data.get('teks_menfess', "")
    original_entities = context.user_data.get('entities', [])

    # Tambah zero-width space
    final_text = teks_asli + "\u200B"
    
    # Kalkulasi offset Telegram
    offset = len(teks_asli.encode('utf-16-le')) // 2
    
    invisible_link = MessageEntity(
        type=MessageEntity.TEXT_LINK, 
        offset=offset, 
        length=1, 
        url=f"https://t.me/{target_username}"
    )
    
    final_entities = original_entities + [invisible_link]

    try:
        message_sent = await context.bot.send_message(
            chat_id=CHANNEL_ID, 
            text=final_text,
            entities=final_entities,
            link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True)
        )

        CACHE_COMSECT_OFF.add(message_sent.message_id)

        keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
        await update.message.reply_text("Pesan kamu telah dikirim ke channel! 🪶", reply_markup=InlineKeyboardMarkup(keyboard))
        
        try:
            supabase.table("menfess_map").insert({
                "post_id": message_sent.message_id, 
                "sender_user_id": user_id
            }).execute()
        except Exception as e: logger.error(f"DB Error Auto: {e}")

        log_msg = f"📌 Log Menfess (AUTO):\n🕰️ Waktu: {update.message.date}\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`\n🔗 Username Target: @{target_username}\n💬 Pesan: {teks_asli}"
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]))

    except Exception as e:
        logger.error(f"Error direct forward: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat mengirim menfess.")
            
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_menfess(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text("✅ Pengiriman menfess dibatalkan.")
    return ConversationHandler.END

# === HANDLER TOMBOL REVIEW (SETUJU/TOLAK) ===
async def handle_callback_review(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data

    if data.startswith("mf|"):
        await query.answer()
        parts = data.split("|")
        action = parts[1]
        user_id = int(parts[2])
        msg_id = int(parts[3])

        if action in ["A_ON", "A_OFF"]:  # Approve
            comsect_on = True if action == "A_ON" else False
            status_text = "DISETUJUI & COMSECT ON" if comsect_on else "DISETUJUI & COMSECT OFF"

            try:
                original_msg = query.message.reply_to_message

                if original_msg and original_msg.text:
                    sent_msg = await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=original_msg.text,
                        entities=original_msg.entities,
                        link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True)
                    )
                else:
                    sent_msg = await context.bot.copy_message(
                        chat_id=CHANNEL_ID,
                        from_chat_id=ADMIN_GROUP_ID,
                        message_id=original_msg.message_id
                    )

                # SIMPAN KE CACHE JIKA COMSECT OFF
                if not comsect_on:
                    CACHE_COMSECT_OFF.add(sent_msg.message_id)

                log_msg = f"📌 Log Menfess (Manual Approved):\n🆔 Pengirim ID: `{user_id}`\n⚙️ Comsect: {'ON' if comsect_on else 'OFF'}"
                await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, parse_mode="Markdown")

                # Simpan data asli ke DB
                try:
                    supabase.table("menfess_map").insert({
                        "post_id": sent_msg.message_id, 
                        "sender_user_id": user_id
                    }).execute()
                except Exception as e: logger.error(f"DB Error Map: {e}")

                await query.edit_message_text(f"{query.message.text}\n\n✅ *STATUS: {status_text}*", parse_mode="Markdown")

                keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{sent_msg.message_id}")]]
                await context.bot.send_message(chat_id=user_id, text=f"✅ Yay! Menfess kamu telah disetujui admin! ({status_text})", reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                logger.error(f"Gagal publish manual menfess: {e}")
                await query.edit_message_text(f"{query.message.text}\n\n❌ *GAGAL DIPUBLISH:* Pesan asli mungkin dihapus.", parse_mode="Markdown")

        elif action == "R":  # Reject
            await query.edit_message_text(f"{query.message.text}\n\n❌ *STATUS: DITOLAK*", parse_mode="Markdown")
            warning_text = (
                "⚠️ *Menfess Ditolak*\n\n"
                "Maaf, menfess kamu ditolak oleh admin karena belum sesuai dengan rules base. "
                "Silakan perbaiki format/isi menfess kamu dan kirim ulang ya!"
            )
            await context.bot.send_message(chat_id=user_id, text=warning_text, parse_mode="Markdown")

async def handle_admin_reply(update: Update, context: CallbackContext):
    # Cek apakah ini di ADMIN GRUP atau LOG GRUP
    if update.effective_chat.id not in [ADMIN_GROUP_ID, LOG_GROUP_ID] or not update.message.reply_to_message: return

    match = re.search(r"ID(?:\s*Pengguna)?:?\s*(\d+)", update.message.reply_to_message.text or update.message.reply_to_message.caption or "")
    if not match: return

    user_id = int(match.group(1))
    reply_text = update.message.text or update.message.caption

    if reply_text and reply_text.startswith("/"):
        try:
            response = supabase.table("commands").select("content").eq("name", reply_text.split()[0]).execute()
            if hasattr(response, 'data') and response.data:
                await context.bot.send_message(chat_id=user_id, text=response.data[0]["content"], parse_mode="Markdown")
                notif = await update.message.reply_text(f"✅ Command dikirim ke user {user_id}")
                await asyncio.sleep(5)
                try: await notif.delete()
                except: pass
        except Exception: pass
        return

    try:
        await context.bot.copy_message(chat_id=user_id, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        notif = await update.message.reply_text("✅ Balasan telah dikirim ke user.")
        await asyncio.sleep(5)
        try: await notif.delete()
        except: pass
    except Exception: await update.message.reply_text("❌ Gagal mengirim balasan.")

# === FUNGSI TAMBAHAN UNTUK FIX ERROR NameError ===
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Pass karena post channel sudah dihandle melalui logic flow atau handle_discussion 
    pass

# === HANDLE GRUP DISKUSI ===
async def handle_discussion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return

    if msg.is_automatic_forward and msg.forward_origin and msg.forward_origin.type == "channel":
        post_id = msg.forward_origin.message_id

        # CEK VIA MEMORI LOKAL INSTAN SAJA (TANPA DATABASE)
        if post_id in CACHE_COMSECT_OFF:
            try:
                await msg.delete()
                CACHE_COMSECT_OFF.discard(post_id)
                return 
            except Exception as e:
                logger.error(f"Gagal hapus comsect via cache: {e}")

        # Jika tidak ada di cache (Comsect ON), simpan ID diskusi ke DB
        origin_chat = msg.forward_origin.chat
        if origin_chat.username and ("@" + origin_chat.username.lower() == CHANNEL_ID.lower()):
            try:
                supabase.table("menfess_map").update({"discussion_message_id": msg.message_id}).eq("post_id", post_id).execute()
            except Exception: pass
        return

    # Notifikasi Balasan Anonim
    if msg.reply_to_message:
        try:
            replied_msg_id = msg.reply_to_message.message_id
            response = supabase.table("menfess_map").select("sender_user_id, post_id").eq("discussion_message_id", replied_msg_id).execute()
            if hasattr(response, 'data') and response.data:
                sender_user_id = response.data[0]["sender_user_id"]
                post_id = response.data[0]["post_id"]

                commenter = f"{msg.from_user.first_name} (@{msg.from_user.username})" if msg.from_user.username else msg.from_user.first_name
                link = f"https://t.me/{CHANNEL_ID.lstrip('@')}/{post_id}?comment={msg.message_id}"

                notif_text = (
                    f"📬 {commenter} berkomentar di menfess kamu!\n\n"
                    f"*(balas/reply pesan ini jika kamu ingin membalas komentarnya secara anonim)*\n\n"
                    f"`#ID:{msg.message_id}`"
                )

                await context.bot.send_message(
                    chat_id=sender_user_id,
                    text=notif_text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Lihat Balasan", url=link)]]),
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"❌ Gagal proses balasan diskusi: {e}")

async def open_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = True
        await update.message.reply_text("✅ Bot telah diaktifkan kembali.")

async def close_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = False
        await update.message.reply_text("⏸️ Bot telah dipause.")

async def get_group_id(update: Update, context: CallbackContext):
    await update.message.reply_text(f"🆔 ID: `{update.effective_chat.id}`\n🏷️ Nama: {update.effective_chat.title or 'Private'}", parse_mode="Markdown")

async def get_all_user_ids():
    try:
        response = supabase.table("users").select("user_id").execute()
        return [row["user_id"] for row in response.data] if hasattr(response, "data") and response.data else []
    except Exception: return []

async def menu(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private": return
    menu_text = "𔐼 *Kitheons:* [@kitheons](https://t.me/kitheons)\n𔐼 *Ch Arsip:* [@kithives](https://t.me/kithives)\n\n"
    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📜 Info Kitheons", url="https://t.me/kithives")]]))

async def broadcast_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args:
        return await update.message.reply_text("Format: /broadcastfw <link>")

    link = context.args[0]
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", link)

    if not match:
        return await update.message.reply_text("❌ Link tidak valid! Pastikan formatnya t.me/username_channel/angka")

    channel_username, message_id = match.groups()

    if channel_username == "c":
        return await update.message.reply_text("❌ Tidak bisa forward menggunakan link dari channel private!")

    user_list = await get_all_user_ids()
    sc, fc = 0, 0

    for user_id in user_list:
        try:
            await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=f"@{channel_username}",
                message_id=int(message_id)
            )
            sc += 1
        except Exception as e:
            fc += 1

        await asyncio.sleep(0.05)

    await update.message.reply_text(f"✅ Selesai! Berhasil: {sc}, Gagal: {fc}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcast <teks>")
    message_text = " ".join(context.args)
    user_list = await get_all_user_ids()
    sc, fc = 0, 0
    for user_id in user_list:
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            sc += 1
        except Exception: fc += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ Selesai! Berhasil: {sc}, Gagal: {fc}")

async def add_command(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        command_name = context.args[0] if context.args else None
        command_content = update.message.reply_to_message.text
    else:
        if len(context.args) < 2: return await update.message.reply_text("Format: /addcommand <nama> <isi>")
        command_name, command_content = context.args[0], " ".join(context.args[1:])
    command_name = command_name if command_name.startswith("/") else "/" + command_name
    try:
        supabase.table("commands").upsert({"name": command_name, "content": command_content}).execute()
        await update.message.reply_text(f"✅ `{command_name}` disimpan!", parse_mode='Markdown')
    except Exception: await update.message.reply_text("❌ Gagal.")

async def delete_command(update: Update, context: CallbackContext) -> None:
    if not context.args: return await update.message.reply_text("Format: /deletecommand <nama>")
    command_name = context.args[0] if context.args[0].startswith("/") else "/" + context.args[0]
    try:
        supabase.table("commands").delete().eq("name", command_name).execute()
        await update.message.reply_text(f"✅ `{command_name}` dihapus!", parse_mode='Markdown')
    except Exception: await update.message.reply_text("❌ Gagal.")

async def settings(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    channels_text = "\n".join([f"𔐼 {c}" for c in required_channels]) if required_channels else "–"
    hashtags_text = "\n".join([f"𔐼 `{h}`" for h in CACHE_HASHTAGS]) if CACHE_HASHTAGS else "–"
    global MENFESS_MODE
    try:
        response = supabase.table("commands").select("name, content").execute()
        commands_text = "\n\n".join([f"*{c['name']}*\n{c['content']}" for c in response.data]) if hasattr(response, 'data') and response.data else "–"
    except Exception:
        commands_text = "– Error –"
    await update.message.reply_text(
        f"⚙️ *Settings*\n\n"
        f"🔄 *Mode Menfess:* `{MENFESS_MODE.upper()}`\n\n"
        f"📌 *Channels:*\n{channels_text}\n\n"
        f"🏷️ *Hashtags:*\n{hashtags_text}\n\n"
        f"💻 *Commands:*\n{commands_text}", parse_mode="Markdown"
    )

def main():
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # Commands admin
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('unblock', unblock_user))
    application.add_handler(CommandHandler('auto', set_mode_auto))
    application.add_handler(CommandHandler('manual', set_mode_manual))
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', menu))
    application.add_handler(CommandHandler('open', open_bot))
    application.add_handler(CommandHandler('close', close_bot))
    application.add_handler(CommandHandler('grupid', get_group_id))
    application.add_handler(CommandHandler('setrequired', set_required_channels))
    
    application.add_handler(CommandHandler("addhashtag", add_hashtag))
    application.add_handler(CommandHandler("removehashtag", remove_hashtag))
    application.add_handler(CommandHandler("enablehashtag", enable_hashtag))
    application.add_handler(CommandHandler("disablehashtag", disable_hashtag))
    
    # Fitur Banned Words
    application.add_handler(CommandHandler("addbadwords", add_badwords))
    application.add_handler(CommandHandler("removebadwords", remove_badwords))
    application.add_handler(CommandHandler("listbadwords", list_badwords))
    
    application.add_handler(CommandHandler('broadcastfw', broadcast_forward))
    application.add_handler(CommandHandler('broadcast', broadcast))
    application.add_handler(CommandHandler("addcommand", add_command))
    application.add_handler(CommandHandler("deletecommand", delete_command))
    application.add_handler(CommandHandler("settings", settings))

    # Conversation Handler untuk Menfess (Hanya masuk sini kalau AUTO)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_pesan)],
        states={
            WAITING_USERNAME: [MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_username)]
        },
        fallbacks=[CommandHandler('cancel', cancel_menfess)]
    )
    application.add_handler(conv_handler)

    # Handler Grup (Admin & Diskusi)
    application.add_handler(CallbackQueryHandler(handle_callback_review))
    application.add_handler(MessageHandler(filters.ALL & filters.Chat([ADMIN_GROUP_ID, LOG_GROUP_ID]), handle_admin_reply))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    application.add_handler(MessageHandler(filters.Chat(GROUP_ID_DISKUSI), handle_discussion))
    
    # Message handler untuk file, media dll (diluar conversation handler)
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.TEXT, handle_pesan))

    logger.info("✅ Membangun bot selesai. Menjalankan polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__ == '__main__':
    main()
