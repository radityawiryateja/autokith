# -*- coding: utf-8 -*-
import json
import logging
import re
import markdown
import os
import random
import uuid
import subprocess
import shutil
import asyncio
import httpx

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes, CallbackContext
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LinkPreviewOptions, MessageEntity, ChatMemberUpdated, ChatMember
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

# FIX: bot_active sekarang di-load dari DB saat startup agar persist antar restart/dyno.
bot_active = True
MENFESS_MODE = "auto"  # Cache default, di-update dari DB saat startup
TITLE_PRICE = 500  # Harga Custom Title
LIVE_PHOTO_PRICE = int(os.environ.get("LIVE_PHOTO_PRICE", "100"))  # Harga fitur Photo Live

# === KONFIGURASI LIVE PHOTO TELEGRAM NATIVE ===
LIVE_MAX_DURATION = min(float(os.environ.get("LIVE_MAX_DURATION", "9.8")), 9.8)
LIVE_MAX_INPUT_FILE_SIZE_MB = int(os.environ.get("LIVE_MAX_INPUT_FILE_SIZE_MB", "50"))
LIVE_MAX_OUTPUT_FILE_SIZE_MB = min(int(os.environ.get("LIVE_MAX_OUTPUT_FILE_SIZE_MB", "10")), 10)
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

WAITING_USERNAME = 1
KEYBOARD_STATE_TITLE = "WAITING_TITLE_FROM_KEYBOARD"
KEYBOARD_STATE_LIVE = "WAITING_LIVE_FROM_KEYBOARD"

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error(f"Gagal koneksi ke Supabase: {e}")

CACHE_HASHTAGS = []
required_channels = []
CACHE_BANNED_USERS = []
CACHE_COMSECT_OFF = set()
CACHE_BAD_WORDS = set()


# ==============================================================================
# FIX: Semua panggilan Supabase (sinkron) dibungkus asyncio.to_thread() agar
#      tidak memblokir event loop asyncio. Ini adalah penyebab utama bot "stuck".
# ==============================================================================
async def db(fn):
    """Helper: jalankan callable Supabase sinkron di thread pool agar non-blocking."""
    return await asyncio.to_thread(fn)


# === CACHE LOADERS ===

async def update_settings_cache():
    global MENFESS_MODE, bot_active
    try:
        response = await db(lambda: supabase.table("bot_settings").select("key, value").execute())
        if hasattr(response, 'data') and response.data:
            settings = {row["key"]: row["value"] for row in response.data}
            MENFESS_MODE = settings.get("menfess_mode", "auto")
            # FIX: bot_active sekarang diambil dari DB agar persist antar restart
            bot_active = settings.get("bot_active", "true").lower() != "false"
        else:
            await db(lambda: supabase.table("bot_settings").insert({"key": "menfess_mode", "value": "auto"}).execute())
            await db(lambda: supabase.table("bot_settings").insert({"key": "bot_active", "value": "true"}).execute())
            MENFESS_MODE = "auto"
            bot_active = True
    except Exception as e:
        logger.error(f"Gagal memuat setting bot: {e}")


async def update_hashtags_cache():
    global CACHE_HASHTAGS
    try:
        response = await db(lambda: supabase.table("triggered_hashtags").select("hashtag").eq("active", True).execute())
        CACHE_HASHTAGS = [row["hashtag"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e:
        logger.error(f"Gagal memuat cache hashtag: {e}")


async def update_badwords_cache():
    global CACHE_BAD_WORDS
    try:
        response = await db(lambda: supabase.table("bad_words").select("word").execute())
        CACHE_BAD_WORDS = {row["word"].lower() for row in response.data} if hasattr(response, 'data') and response.data else set()
    except Exception as e:
        logger.error(f"Gagal memuat cache bad words: {e}")


async def update_required_channels_cache():
    global required_channels
    try:
        response = await db(lambda: supabase.table('required_channels').select("channel_username").execute())
        required_channels = [row["channel_username"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e:
        logger.error(f"Gagal memuat required channels: {e}")


async def update_banned_users_cache():
    global CACHE_BANNED_USERS
    try:
        response = await db(lambda: supabase.table('banned_users').select("user_id").execute())
        CACHE_BANNED_USERS = [row["user_id"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e:
        logger.error(f"Gagal memuat banned users: {e}")


async def check_system_tools():
    """Mengecek apakah FFmpeg sudah terinstal di server untuk fitur /live."""
    tools = {"FFmpeg": "ffmpeg"}
    for name, cmd in tools.items():
        path = shutil.which(cmd)
        if path:
            logger.info(f"🚀 {name} terdeteksi di: {path}")
        else:
            logger.warning(f"⚠️ {name} TIDAK ditemukan! Fitur /live bakal error.")


async def on_startup(application: Application):
    try:
        me = await application.bot.get_me()
        logger.info(f"✅ Bot siap: @{me.username} (id={me.id})")
        await check_system_tools()
        await update_settings_cache()
        await update_hashtags_cache()
        await update_badwords_cache()
        await update_required_channels_cache()
        await update_banned_users_cache()
    except Exception as e:
        logger.error(f"⚠️ Gagal get_me saat startup: {e}")


async def save_required_channels(channels):
    """FIX: dibuat async agar bisa menggunakan db() helper."""
    try:
        await db(lambda: supabase.table('required_channels').delete().neq("channel_username", "").execute())
        for channel in channels:
            await db(lambda c=channel: supabase.table('required_channels').insert({"channel_username": c}).execute())
    except Exception as e:
        logger.error(f"Gagal menyimpan required channels: {e}")


async def check_subscription(user_id, context: CallbackContext):
    if not required_channels:
        return True
    for channel in required_channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception:
            return False
    return True


# === HELPER: MANAJEMEN KOIN ===
# kith_coins = saldo saat ini (boleh berkurang kalau dipakai belanja)
# total_kith_coins = total koin yang pernah diperoleh (untuk leaderboard, tidak berkurang saat belanja)
async def add_kith_coins(user_id: int, amount: int):
    try:
        response = await db(lambda: supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute())
        row = response.data[0] if hasattr(response, 'data') and response.data else {}

        current_balance = row.get("kith_coins") if row.get("kith_coins") is not None else 0
        current_total = row.get("total_kith_coins") if row.get("total_kith_coins") is not None else current_balance

        new_balance = current_balance + amount
        new_total = current_total + amount

        await db(lambda: supabase.table("users").update({
            "kith_coins": new_balance,
            "total_kith_coins": new_total
        }).eq("user_id", user_id).execute())

        return new_balance
    except Exception as e:
        logger.error(f"Gagal tambah koin untuk {user_id}: {e}")
        return None


# === FITUR PROFIL & LEADERBOARD ===
async def cek_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    try:
        res = await db(lambda: supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute())
        row = res.data[0] if res.data else {}
        coins = row.get("kith_coins") if row.get("kith_coins") is not None else 0
        total_coins = row.get("total_kith_coins") if row.get("total_kith_coins") is not None else coins

        text = (
            f"👤 *PROFIL KAMU*\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"🪙 Saldo Kith-Coins: *{coins}*\n"
            f"🏆 Total Koin Diperoleh: *{total_coins}*\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("❌ Gagal mengambil data profil.")


# === FITUR LEADERBOARD ===
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # HANYA mengambil user_id dan koin dari database (tanpa username)
        res = await db(lambda: supabase.table("users")
                       .select("user_id, kith_coins, total_kith_coins")
                       .order("total_kith_coins", desc=True)
                       .limit(10)
                       .execute())
        if not res.data:
            return await update.message.reply_text("Belum ada data pemain.")

        text = "🏆 *LEADERBOARD TOTAL KITH-COINS* 🏆\n\n"
        for i, row in enumerate(res.data):
            user_id = row.get("user_id")
            coins = row.get("total_kith_coins") if row.get("total_kith_coins") is not None else row.get("kith_coins", 0)
            
            # Mencoba menarik Display Name (first name) langsung dari Telegram
            try:
                chat = await context.bot.get_chat(user_id)
                display_name = chat.first_name
                # Jika ingin menyertakan nama belakang juga (opsional):
                # if chat.last_name:
                #     display_name += f" {chat.last_name}"
            except Exception:
                # Fallback: Jika bot gagal menarik data (misal user memblokir bot)
                display_name = f"Pemain {user_id}"

            # Format Hyperlink ke profil menggunakan ID, bukan username
            text += f"{i+1}. [{display_name}](tg://user?id={user_id}) - *{coins}* Coins\n"

        text += "\nLeaderboard dihitung dari total koin yang pernah diperoleh, bukan saldo saat ini."
        
        # Wajib menggunakan parse_mode="Markdown" agar hyperlink tg://user bisa diklik
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Gagal memuat leaderboard: {e}")
        await update.message.reply_text("❌ Gagal mengambil data leaderboard.")


# === FITUR BELI TITLE ===
async def buy_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return await update.message.reply_text("🛒 Silakan gunakan command ini di chat pribadi (DM) dengan bot.")

    user_id = update.effective_user.id

    if not context.args:
        return await update.message.reply_text(
            f"⚠️ Format salah!\nGunakan: `/buytitle <nama_title>`\nContoh: `/buytitle The Undercover Pro`\n\n*Harga: {TITLE_PRICE} Kith-Coins*",
            parse_mode="Markdown"
        )

    new_title = " ".join(context.args)

    if len(new_title) > 16:
        return await update.message.reply_text("❌ Gagal! Nama title maksimal 16 karakter ya.")

    try:
        response = await db(lambda: supabase.table("users").select("kith_coins").eq("user_id", user_id).execute())
        current_balance = response.data[0].get("kith_coins") if hasattr(response, 'data') and response.data and response.data[0].get("kith_coins") is not None else 0

        if current_balance < TITLE_PRICE:
            return await update.message.reply_text(f"❌ Kith-Coins kamu tidak cukup.\nSaldo kamu: {current_balance} Coins\nHarga Title: {TITLE_PRICE} Coins")

        new_balance = current_balance - TITLE_PRICE
        await db(lambda: supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", user_id).execute())

        try:
            await context.bot.set_chat_member_tag(
                chat_id=GROUP_ID_DISKUSI,
                user_id=user_id,
                tag=new_title
            )
            await update.message.reply_text(
                f"✅ Transaksi Berhasil!\n\n"
                f"🏷️ Title barumu: `{new_title}`\n"
                f"🪙 Sisa saldo Kith-Coins: {new_balance}\n\n"
                f"Silakan kirim pesan di grup diskusi untuk melihat title barumu!",
                parse_mode="Markdown"
            )
        except Exception as telegram_err:
            await db(lambda: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute())
            logger.error(f"Gagal set title Telegram: {telegram_err}")
            if "User_Not_Participant" in str(telegram_err) or "user is not a member" in str(telegram_err):
                await update.message.reply_text("❌ Gagal menerapkan title. Pastikan kamu sudah join ke grup diskusi terlebih dahulu dan jangan gunakan emoji!\n\nKoin kamu telah dikembalikan (Refund).")
            else:
                await update.message.reply_text("❌ Gagal menerapkan title. Pastikan kamu sudah join ke grup diskusi terlebih dahulu dan jangan gunakan emoji.\n\nKoin kamu telah dikembalikan (Refund).")

    except Exception as db_err:
        logger.error(f"Error Database saat beli title: {db_err}")
        await update.message.reply_text("❌ Terjadi kesalahan pada database. Silakan coba lagi nanti.")


async def _apply_title_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, new_title: str):
    """Versi helper untuk tombol keyboard. Teks respons sengaja disamakan dengan /buytitle."""
    user_id = update.effective_user.id

    if len(new_title) > 16:
        return await update.message.reply_text("❌ Gagal! Nama title maksimal 16 karakter ya.", reply_markup=get_main_keyboard())

    try:
        response = await db(lambda: supabase.table("users").select("kith_coins").eq("user_id", user_id).execute())
        current_balance = response.data[0].get("kith_coins") if hasattr(response, 'data') and response.data and response.data[0].get("kith_coins") is not None else 0

        if current_balance < TITLE_PRICE:
            return await update.message.reply_text(f"❌ Kith-Coins kamu tidak cukup.\nSaldo kamu: {current_balance} Coins\nHarga Title: {TITLE_PRICE} Coins", reply_markup=get_main_keyboard())

        new_balance = current_balance - TITLE_PRICE
        await db(lambda: supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", user_id).execute())

        try:
            await context.bot.set_chat_member_tag(
                chat_id=GROUP_ID_DISKUSI,
                user_id=user_id,
                tag=new_title
            )
            await update.message.reply_text(
                f"✅ Transaksi Berhasil!\n\n"
                f"🏷️ Title barumu: `{new_title}`\n"
                f"🪙 Sisa saldo Kith-Coins: {new_balance}\n\n"
                f"Silakan kirim pesan di grup diskusi untuk melihat title barumu!",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
        except Exception as telegram_err:
            await db(lambda: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute())
            logger.error(f"Gagal set title Telegram: {telegram_err}")
            if "User_Not_Participant" in str(telegram_err) or "user is not a member" in str(telegram_err):
                await update.message.reply_text("❌ Gagal menerapkan title. Pastikan koin kamu mencukupi dan sudah join ke grup diskusi terlebih dahulu!\n\nKoin kamu telah dikembalikan (Refund).", reply_markup=get_main_keyboard())
            else:
                await update.message.reply_text("❌ Gagal menerapkan title. Pastikan koin kamu mencukupi dan sudah join ke grup diskusi terlebih dahulu!.\n\nKoin kamu telah dikembalikan (Refund).", reply_markup=get_main_keyboard())

    except Exception as db_err:
        logger.error(f"Error Database saat beli title: {db_err}")
        await update.message.reply_text("❌ Terjadi kesalahan pada database. Silakan coba lagi nanti.", reply_markup=get_main_keyboard())


# === FITUR BANNED WORDS ===
async def add_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    raw_text = update.message.text.split(maxsplit=1)
    if len(raw_text) < 2:
        return await update.message.reply_text("Format: /addbadwords kata1, kata2, kata3")

    words = [w.strip().lower() for w in raw_text[1].split(',')]
    inserted = 0
    for w in words:
        if w:
            try:
                await db(lambda word=w: supabase.table("bad_words").upsert({"word": word}).execute())
                inserted += 1
            except Exception:
                pass

    await update_badwords_cache()
    await update.message.reply_text(f"✅ {inserted} kata terlarang berhasil ditambahkan!")


async def remove_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    raw_text = update.message.text.split(maxsplit=1)
    if len(raw_text) < 2:
        return await update.message.reply_text("Format: /removebadwords kata1, kata2")

    words = [w.strip().lower() for w in raw_text[1].split(',')]
    deleted = 0
    for w in words:
        if w:
            try:
                await db(lambda word=w: supabase.table("bad_words").delete().eq("word", word).execute())
                deleted += 1
            except Exception:
                pass

    await update_badwords_cache()
    await update.message.reply_text(f"✅ {deleted} kata terlarang berhasil dihapus!")


async def list_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not CACHE_BAD_WORDS:
        return await update.message.reply_text("Daftar kata terlarang saat ini kosong.")
    word_list = ", ".join(sorted(CACHE_BAD_WORDS))
    await update.message.reply_text(f"🚫 *Daftar Kata Terlarang:*\n\n{word_list}", parse_mode="Markdown")


# === FITUR BLOCK USER ===
async def block_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Format: /block <user_id>")
    try:
        target_id = int(context.args[0])
        await db(lambda: supabase.table("banned_users").upsert({"user_id": target_id}).execute())
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil diblokir dari bot.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("❌ Gagal memblokir user. Pastikan format ID benar.")


async def unblock_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Format: /unblock <user_id>")
    try:
        target_id = int(context.args[0])
        await db(lambda: supabase.table("banned_users").delete().eq("user_id", target_id).execute())
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil di-unblock.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("❌ Gagal unblock user.")


# === FITUR UBAH MODE MENFESS ===
async def set_mode_auto(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    global MENFESS_MODE
    MENFESS_MODE = "auto"
    try:
        await db(lambda: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "auto"}).execute())
    except Exception as e:
        logger.error(f"Gagal simpan mode auto ke DB: {e}")
    await update.message.reply_text("✅ Mode menfess diubah ke *AUTO*. Menfess akan langsung terkirim ke channel (Comsect OFF Otomatis, Teks Only).", parse_mode="Markdown")


async def set_mode_manual(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    global MENFESS_MODE
    MENFESS_MODE = "manual"
    try:
        await db(lambda: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "manual"}).execute())
    except Exception as e:
        logger.error(f"Gagal simpan mode manual ke DB: {e}")
    await update.message.reply_text("⏸️ Mode menfess diubah ke *MANUAL*. Menfess akan masuk ke grup admin untuk direview.", parse_mode="Markdown")


# === HASHTAG & SETTINGS LAINNYA ===
async def add_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Gunakan format: /addhashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").upsert({"hashtag": hashtag}).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` berhasil ditambahkan!", parse_mode="Markdown")


async def remove_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Gunakan format: /removehashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").delete().eq("hashtag", hashtag).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"❌ Hashtag `{hashtag}` berhasil dihapus!", parse_mode="Markdown")


async def enable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Gunakan format: /enablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").update({"active": True}).eq("hashtag", hashtag).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` diaktifkan!", parse_mode="Markdown")


async def disable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Gunakan format: /disablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").update({"active": False}).eq("hashtag", hashtag).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"⚠️ Hashtag `{hashtag}` dinonaktifkan!", parse_mode="Markdown")


async def set_required_channels(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("Gunakan format: /setrequired @channel1 @channel2")
    global required_channels
    required_channels = context.args
    await save_required_channels(required_channels)
    await update.message.reply_text(f"Daftar channel wajib diikuti telah diperbarui: {', '.join(required_channels)}")


async def save_user(user_id, context: CallbackContext):
    try:
        # Eksekusi simpan ke database
        await db(lambda: supabase.table("users").upsert({"user_id": user_id}, on_conflict=["user_id"]).execute())
        
        # Jika berhasil, kirim log SUKSES ke grup admin
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"✅ *DB LOG:* Berhasil menyimpan/update user ID: `{user_id}` ke database Supabase.",
            parse_mode="Markdown"
        )
    except Exception as e:
        # Jika gagal, kirim log ERROR ke grup admin
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"⚠️ *DB ERROR:* Gagal menyimpan user ID: `{user_id}`!\n\n*Detail Error:*\n`{e}`",
            parse_mode="Markdown"
        )


def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💬 Beli title"), KeyboardButton("💌 Menfess")],
        [KeyboardButton("👤 Profile"), KeyboardButton("📸 Photo live")],
        [KeyboardButton("🎭 Set Profile Anon"), KeyboardButton("🔍 Cari Partner Anon")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_stop_anon_keyboard():
    keyboard = [[KeyboardButton("🛑 Stop Anon")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Cancel")]], resize_keyboard=True, is_persistent=True)


async def start(update: Update, context: CallbackContext):
    context.user_data.clear()
    
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id

    if user_id in CACHE_BANNED_USERS:
        return await update.message.reply_text("❌ Akses kamu ke bot ini telah diblokir.")

    await save_user(user_id, context)

    if await check_subscription(user_id, context):
        await update.message.reply_text(
            "Halo Kens, selamat datang di *Kitheons*! ☕️\n\n"
            "𔐼 *Kitheons:* [@kitheons](https://t.me/kitheons)\n"
            "𔐼 *Ch Arsip:* [@kithives](https://t.me/kithives)\n\n"
            "Ketuk /menu untuk menampilkan navigasi.\n"
            "*(Semua pesan yang kamu kirim otomatis diajukan sebagai menfess)*",
            parse_mode="Markdown", reply_markup=get_main_keyboard()
        )
    else:
        keyboard = [[InlineKeyboardButton("Join Channels", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)


# === ALUR MENFESS ===
async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE

    # FIX: guard agar tidak crash jika effective_user atau message kosong
    if not update.effective_user or not update.message:
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    display_name = f"@{username}" if username else first_name

    if update.message.text == "🛑 Stop Anon":
        await stop_anon(update, context)
        return ConversationHandler.END

    # --- CEGAT LOGIKA ANON CHAT DI SINI ---
    res = await db(lambda: supabase.table("users").select("chat_state, partner_id").eq("user_id", user_id).execute())
    user_state = res.data[0].get("chat_state") if res.data else "menfess"
    
    if user_state == "chatting_admin":
        # 1. Kirim pesan pengenal (header) ke admin agar admin tahu ini dari siapa
        header_msg = await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"💬 #AnonFallback\nDari ID: `{user_id}`\n*(Reply pesan ini untuk membalas ke user)*",
            parse_mode="Markdown"
        )
        # 2. Kirim/copy pesan aslinya (teks, gambar, stiker, dll)
        await context.bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=user_id,
            message_id=update.message.message_id,
            reply_to_message_id=header_msg.message_id
        )
        return ConversationHandler.END
        
    elif user_state == "chatting":
        partner_id = res.data[0].get("partner_id")
        if partner_id:
            await context.bot.copy_message(chat_id=partner_id, from_chat_id=user_id, message_id=update.message.message_id)
        return ConversationHandler.END
    # ----------------------------------------

    if user_id in CACHE_BANNED_USERS:
        await update.message.reply_text("❌ Pesan ditolak. Akses kamu ke bot ini telah diblokir.")
        return ConversationHandler.END

    if update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        match = re.search(r"#ID:(\d+)", replied_text)
        if match:
            try:
                comment_msg_id = int(match.group(1))
                if update.message.text:
                    await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text=f"🗣️ *Balasan Sender:*\n\n{update.message.text}", reply_to_message_id=comment_msg_id, parse_mode="Markdown")
                else:
                    await context.bot.copy_message(chat_id=GROUP_ID_DISKUSI, from_chat_id=user_id, message_id=update.message.message_id, reply_to_message_id=comment_msg_id, caption=f"🗣️ *Balasan Sender:*\n\n{update.message.caption or ''}", parse_mode="Markdown")
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
    keyboard_state = context.user_data.get("keyboard_state")

    if update.message.text == "❌ Cancel":
        context.user_data.clear()
        context.user_data.pop("keyboard_state", None)
        await update.message.reply_text("✅ Aksi dibatalkan. Kembali ke menu utama.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    if keyboard_state == KEYBOARD_STATE_TITLE:
        context.user_data.pop("keyboard_state", None)
        new_title = (update.message.text or "").strip()
        if not new_title:
            await update.message.reply_text("❌ Nama title tidak boleh kosong.", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        await _apply_title_purchase(update, context, new_title)
        return ConversationHandler.END

    if keyboard_state == KEYBOARD_STATE_LIVE:
        context.user_data.pop("keyboard_state", None)
        if not _get_video_file_from_message(update.message):
            await update.message.reply_text("❌ Itu bukan video! Aksi dibatalkan.", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        await live_photo_handler(update, context)
        return ConversationHandler.END

    # --- LOGIKA KEYBOARD STATE ANON PROFILE ---
    if keyboard_state == "ANON_AGE":
        if pesan_teks not in ["Legal (≥ 18)", "Minor (< 18)"]:
            await update.message.reply_text("⚠️ Silakan gunakan tombol di bawah untuk memilih umur.")
            return ConversationHandler.END
            
        context.user_data['anon_age'] = "legal" if "Legal" in pesan_teks else "minor"
        context.user_data["keyboard_state"] = "ANON_GENDER"
        
        keyboard = [
            [KeyboardButton("Male ♂️"), KeyboardButton("Female ♀️")],
            [KeyboardButton("❌ Cancel")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("2️⃣ Pilih gender kamu:", reply_markup=reply_markup)
        return ConversationHandler.END

    if keyboard_state == "ANON_GENDER":
        if pesan_teks not in ["Male ♂️", "Female ♀️"]:
            await update.message.reply_text("⚠️ Silakan gunakan tombol di bawah untuk memilih gender.")
            return ConversationHandler.END
            
        context.user_data['anon_gen'] = "male" if "Male" in pesan_teks else "female"
        context.user_data["keyboard_state"] = "ANON_ORI"
        
        keyboard = [
            [KeyboardButton("bxg"), KeyboardButton("bxb")],
            [KeyboardButton("gxg"), KeyboardButton("nbxnb")],
            [KeyboardButton("❌ Cancel")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "3️⃣ Pilih orientasi kamu (maksimal 3 filter).\n\n"
            "*(Bisa tekan tombol di bawah, atau ketik manual jika lebih dari 1, contoh: bxg, bxb)*:", 
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    if keyboard_state == "ANON_ORI":
        valid_oris = ["bxg", "bxb", "gxg", "nbxnb"]
        # Ambil filter yang diketik/diklik user
        user_oris = [o for o in valid_oris if o in pesan_teks_lower]
        
        if not user_oris:
            await update.message.reply_text("⚠️ Silakan pilih atau ketik orientasi yang valid (bxg, bxb, gxg, nbxnb).")
            return ConversationHandler.END
            
        if len(user_oris) > 3:
            await update.message.reply_text("⚠️ Maksimal 3 filter orientasi ya! Silakan ketik ulang.")
            return ConversationHandler.END
            
        ori = ",".join(user_oris)
        age = context.user_data.get('anon_age')
        gender = context.user_data.get('anon_gen')
        
        # Simpan ke Supabase
        await db(lambda: supabase.table("users").update({
            "age_group": age, 
            "gender": gender, 
            "orientation": ori
        }).eq("user_id", user_id).execute())
        
        # Bersihkan state agar bisa mengirim menfess lagi
        context.user_data.pop("keyboard_state", None)
        
        # Mengembalikan keyboard utama
        await update.message.reply_text(
            f"✅ Profil tersimpan!\nUmur: `{age}`\nGender: `{gender}`\nOrientasi: `{ori}`\n\nKetik /search untuk mencari partner.", 
            parse_mode="Markdown",
            reply_markup=get_main_keyboard() 
        )
        return ConversationHandler.END

    # --- LOGIKA TOMBOL MAIN KEYBOARD ---
    if update.message.text == "👤 Profile":
        await cek_profile(update, context)
        return ConversationHandler.END

    if update.message.text == "💬 Beli title":
        context.user_data["keyboard_state"] = KEYBOARD_STATE_TITLE
        await update.message.reply_text(
            f"🛒 *Beli Custom Title*\nHarga: *{TITLE_PRICE} Kith-Coins*.\n\nKetik *Nama Title Barumu* (Maks 16 karakter):",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        return ConversationHandler.END

    if update.message.text == "📸 Photo live":
        context.user_data["keyboard_state"] = KEYBOARD_STATE_LIVE
        await update.message.reply_text(
            f"📸 *Buat Photo Live*\nBiaya: *{LIVE_PHOTO_PRICE} Kith-Coins*\n\nKirim/forward videonya ke sini (maks {LIVE_MAX_DURATION:.0f} detik, input maksimal {LIVE_MAX_INPUT_FILE_SIZE_MB} MB).",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        return ConversationHandler.END

    if update.message.text == "🎭 Set Profile Anon":
        await set_profile(update, context)
        return ConversationHandler.END

    if update.message.text == "🔍 Cari Partner Anon":
        await search_anon(update, context)
        return ConversationHandler.END

    if update.message.text == "💌 Menfess":
        # Reset state di database kembali ke mode menfess
        await db(lambda: supabase.table("users").update({
            "chat_state": "menfess", 
            "partner_id": None
        }).eq("user_id", user_id).execute())
        
        await update.message.reply_text("💌 *Kirim Menfess*\n\nSilakan ketik pesan menfess kamu sekarang!", parse_mode="Markdown", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    # --- FILTER BAD WORDS UNTUK MENFESS ---
    for bw in CACHE_BAD_WORDS:
        if re.search(rf'\b{re.escape(bw)}\b', pesan_teks_lower):
            await update.message.reply_text("❌ Menfess ditolak karena mengandung kata-kata yang dilarang oleh base.")
            return ConversationHandler.END

    # --- CEK STATUS BOT (HANYA UNTUK MENFESS) ---
    if not bot_active:
        try:
            # 1. Kirim notifikasi header ke admin
            header_msg = await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"📥 *PESAN MASUK (BOT CLOSED)*\nDari: {display_name} (`{user_id}`)\n*(Pesan ini diteruskan langsung karena sesi menfess sedang ditutup)*",
                parse_mode="Markdown"
            )
            # 2. Copy pesan aslinya ke admin (mendukung teks, stiker, media)
            await context.bot.copy_message(
                chat_id=ADMIN_GROUP_ID,
                from_chat_id=user_id,
                message_id=update.message.message_id,
                reply_to_message_id=header_msg.message_id
            )
            await update.message.reply_text("⛔ Sesi menfess saat ini sedang ditutup. Pesanmu telah diteruskan langsung ke admin sebagai pesan biasa.")
        except Exception as e:
            logger.error(f"Gagal meneruskan pesan saat bot closed: {e}")
            await update.message.reply_text("⛔ Sesi menfess saat ini sedang ditutup oleh admin.")
            
        return ConversationHandler.END

    # --- PROSES MENFESS ---
    if MENFESS_MODE == "auto":
        if not update.message.text:
            await update.message.reply_text("❌ Sesi /auto sedang aktif! Kamu hanya diperbolehkan mengirim pesan teks saja (tanpa media).")
            return ConversationHandler.END

        if len(update.message.text) > 70:
            await update.message.reply_text(f"❌ Menfess terlalu panjang! Maksimal 70 karakter ya. (Pesanmu saat ini: {len(update.message.text)} karakter).")
            return ConversationHandler.END

        ada_mention = False
        if update.message.entities:
            for ent in update.message.entities:
                if ent.type == "mention":
                    ada_mention = True
                    break

        if ada_mention or re.search(r'(?:^|\s)@/?\w+', pesan_teks):
            await update.message.reply_text("❌ Menfess dilarang menyertakan mention atau username! (Link URL tetap diperbolehkan).")
            return ConversationHandler.END

        context.user_data['teks_menfess'] = update.message.text
        context.user_data['entities'] = update.message.entities or []

        await update.message.reply_text("⏳ Teks diterima! Sekarang kirimkan **username** kamu untuk di-hyperlink (contoh: jake/@jake).\n\n*Ketik /cancel untuk membatalkan.*", parse_mode="Markdown")
        return WAITING_USERNAME

    else:
        try:
            fw_msg = await context.bot.copy_message(chat_id=ADMIN_GROUP_ID, from_chat_id=user_id, message_id=update.message.message_id)

            keyboard = [
                [InlineKeyboardButton("✅ Acc (CS ON)", callback_data=f"mf|A_ON|{user_id}|{update.message.message_id}"), InlineKeyboardButton("🔕 Acc (CS OFF)", callback_data=f"mf|A_OFF|{user_id}|{update.message.message_id}")],
                [InlineKeyboardButton("❌ Tolak", callback_data=f"mf|R|{user_id}|{update.message.message_id}")]
            ]

            review_text = f"🚨 *REVIEW MENFESS*\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`"
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=review_text, reply_to_message_id=fw_msg.message_id, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            await update.message.reply_text("⏳ Menfess kamu sedang masuk ke antrean admin untuk direview. Mohon tunggu ya!")
        except Exception as e:
            logger.error(f"Error kirim manual review: {e}")
            await update.message.reply_text("❌ Gagal mengirim menfess ke admin review.")

        return ConversationHandler.END


async def handle_username(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    display_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

    # 1. Validasi Input Harus Berupa Teks
    if not update.message.text:
        await update.message.reply_text("❌ Gagal! Username harus berupa teks biasa. Silakan kirim ulang pesan menfess kamu dari awal.", reply_markup=get_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
        
    raw_input = update.message.text.strip()
    
    # 2. Validasi Format: 1 Kata, Tanpa Spasi, Hanya Karakter Legal
    if not re.match(r"^@?[a-zA-Z0-9_]+$", raw_input):
        await update.message.reply_text("❌ Gagal! Username tidak boleh lebih dari 1 kata, tidak boleh ada spasi, atau karakter aneh (contoh: jake).\n\nSilakan kirim ulang pesan menfess kamu dari awal.", reply_markup=get_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END

    target_username = raw_input.replace("@", "")
    teks_asli = context.user_data.get('teks_menfess', "")
    
    # FIX: Ubah menjadi list agar bisa digabungkan dengan list [invisible_link]
    original_entities = list(context.user_data.get('entities', []))

    final_text = teks_asli + "\u200B"
    offset = len(teks_asli.encode('utf-16-le')) // 2

    invisible_link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=offset, length=1, url=f"https://t.me/{target_username}")
    final_entities = original_entities + [invisible_link]

    try:
        # Kirim ke Channel
        message_sent = await context.bot.send_message(
            chat_id=CHANNEL_ID, 
            text=final_text, 
            entities=final_entities, 
            link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True)
        )
        CACHE_COMSECT_OFF.add(message_sent.message_id)
        
        # Tambah Koin
        new_balance = await add_kith_coins(user_id, 50)
        coin_msg = f"\n💰 *+50 Kith-Coins!* (Saldo: {new_balance})" if new_balance is not None else ""
        
        # Reply ke user
        keyboard_user = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
        await update.message.reply_text(f"Pesan kamu telah dikirim ke channel! 🪶{coin_msg}", reply_markup=InlineKeyboardMarkup(keyboard_user), parse_mode="Markdown")

        # Simpan ke DB
        try:
            await db(lambda: supabase.table("menfess_map").insert({"post_id": message_sent.message_id, "sender_user_id": user_id}).execute())
        except Exception as e:
            logger.error(f"DB Error Auto: {e}")

        # --- LOG DENGAN TOMBOL HAPUS & TEGUR ---
        log_msg = f"📌 Log Menfess (AUTO):\n🕰️ Waktu: {update.message.date}\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`\n🔗 Username Target: @{target_username}\n💬 Pesan: {teks_asli}"
        
        keyboard_log = [
            [InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")],
            [InlineKeyboardButton("❌ Hapus & Tegur", callback_data=f"del_{user_id}_{message_sent.message_id}")]
        ]
        
        await context.bot.send_message(
            chat_id=LOG_GROUP_ID, 
            text=log_msg, 
            reply_markup=InlineKeyboardMarkup(keyboard_log), 
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error direct forward: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat mengirim menfess.", reply_markup=get_main_keyboard())

    context.user_data.clear()
    return ConversationHandler.END


async def cancel_menfess(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text("✅ Pengiriman menfess dibatalkan.")
    return ConversationHandler.END


async def handle_callback_review(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data

    if data.startswith("mf|"):
        await query.answer()
        parts = data.split("|")
        action = parts[1]
        user_id = int(parts[2])
        msg_id = int(parts[3])

        if action in ["A_ON", "A_OFF"]:
            comsect_on = True if action == "A_ON" else False
            status_text = "DISETUJUI & COMSECT ON" if comsect_on else "DISETUJUI & COMSECT OFF"

            try:
                original_msg = query.message.reply_to_message
                earned_coins = 100 if (original_msg.photo or original_msg.video or original_msg.document or original_msg.animation) else 50

                if original_msg and original_msg.text:
                    sent_msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=original_msg.text, entities=original_msg.entities, link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True))
                else:
                    sent_msg = await context.bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=ADMIN_GROUP_ID, message_id=original_msg.message_id)

                if not comsect_on:
                    CACHE_COMSECT_OFF.add(sent_msg.message_id)

                log_msg = f"📌 Log Menfess (Manual Approved):\n🆔 Pengirim ID: `{user_id}`\n⚙️ Comsect: {'ON' if comsect_on else 'OFF'}"
                await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, parse_mode="Markdown")

                new_balance = await add_kith_coins(user_id, earned_coins)

                try:
                    await db(lambda: supabase.table("menfess_map").insert({"post_id": sent_msg.message_id, "sender_user_id": user_id}).execute())
                except Exception as e:
                    logger.error(f"DB Error Map: {e}")

                await query.edit_message_text(f"{query.message.text}\n\n✅ *STATUS: {status_text}*", parse_mode="Markdown")

                coin_msg = f"\n💰 *+{earned_coins} Kith-Coins!* (Saldo: {new_balance})" if new_balance is not None else ""
                keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{sent_msg.message_id}")]]
                await context.bot.send_message(chat_id=user_id, text=f"✅ Yay! Menfess kamu telah disetujui admin! ({status_text}){coin_msg}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Gagal publish manual menfess: {e}")
                await query.edit_message_text(f"{query.message.text}\n\n❌ *GAGAL DIPUBLISH:* Pesan asli mungkin dihapus.", parse_mode="Markdown")

        elif action == "R":
            await query.edit_message_text(f"{query.message.text}\n\n❌ *STATUS: DITOLAK*", parse_mode="Markdown")
            warning_text = "⚠️ *Menfess Ditolak*\n\nMaaf, menfess kamu ditolak oleh admin karena belum sesuai dengan rules base. Silakan perbaiki format/isi menfess kamu dan kirim ulang ya!"
            await context.bot.send_message(chat_id=user_id, text=warning_text, parse_mode="Markdown")


async def handle_admin_reply(update: Update, context: CallbackContext):
    if update.effective_chat.id not in [ADMIN_GROUP_ID, LOG_GROUP_ID] or not update.message.reply_to_message:
        return

    replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""

    # Ekstrak ID dari pesan yang di-reply (mendukung format ID Pengguna lama & format Dari ID AnonFallback)
    match = re.search(r"(?:ID(?:\s*Pengguna)?|Dari\s*ID):?\s*[`]*(\d+)", replied_text, re.IGNORECASE)
    if not match:
        return

    user_id = int(match.group(1))
    reply_text = update.message.text or update.message.caption or ""

    # --- LOGIKA KHUSUS #AnonFallback ---
    if "#AnonFallback" in replied_text:
        # Jika admin ingin memutus sesi anonim
        if reply_text.strip() == "/stop_anon":
            try:
                await db(lambda: supabase.table("users").update({"chat_state": "menfess"}).eq("user_id", user_id).execute())
                await context.bot.send_message(chat_id=user_id, text="🔴 Partner kamu telah meninggalkan obrolan. (Kembali ke mode menfess)")
                await update.message.reply_text(f"✅ Sesi obrolan anonim dengan ID `{user_id}` telah diakhiri oleh Admin.", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Gagal mengakhiri sesi anonim: {e}")
            return
        
        # Jika balasan admin bukan command, biarkan kode mengalir ke blok copy_message di bawah
        # agar dikirim ke user seolah-olah dari partner aslinya.

    # --- LOGIKA BALASAN COMMAND DARI DATABASE ---
    if reply_text and reply_text.startswith("/") and reply_text.strip() != "/stop_anon":
        try:
            response = await db(lambda: supabase.table("commands").select("content").eq("name", reply_text.split()[0]).execute())
            if hasattr(response, 'data') and response.data:
                await context.bot.send_message(chat_id=user_id, text=response.data[0]["content"], parse_mode="Markdown")
                notif = await update.message.reply_text(f"✅ Command dikirim ke user {user_id}")
                await asyncio.sleep(5)
                try:
                    await notif.delete()
                except Exception:
                    pass
        except Exception:
            pass
        return

    # --- LOGIKA MENGIRIM BALASAN (TEKS/MEDIA) KE USER ---
    try:
        # copy_message menjamin tidak ada tulisan "Forwarded from Admin"
        await context.bot.copy_message(
            chat_id=user_id, 
            from_chat_id=update.effective_chat.id, 
            message_id=update.message.message_id
        )
        
        # Sesuaikan teks notifikasi agar admin tahu pesan masuk ke jalur mana
        notif_text = "💬 Pesan anonim terkirim!" if "#AnonFallback" in replied_text else "✅ Balasan telah dikirim ke user."
        notif = await update.message.reply_text(notif_text)
        
        await asyncio.sleep(5)
        try:
            await notif.delete()
        except Exception:
            pass
    except Exception:
        await update.message.reply_text("❌ Gagal mengirim balasan.")


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def handle_discussion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    if msg.is_automatic_forward and msg.forward_origin and msg.forward_origin.type == "channel":
        post_id = msg.forward_origin.message_id

        if post_id in CACHE_COMSECT_OFF:
            try:
                await msg.delete()
                CACHE_COMSECT_OFF.discard(post_id)
                return
            except Exception as e:
                logger.error(f"Gagal hapus comsect via cache: {e}")

        origin_chat = msg.forward_origin.chat
        if origin_chat.username and ("@" + origin_chat.username.lower() == CHANNEL_ID.lower()):
            try:
                await db(lambda: supabase.table("menfess_map").update({"discussion_message_id": msg.message_id}).eq("post_id", post_id).execute())
            except Exception:
                pass
        return

    if msg.reply_to_message:
        try:
            replied_msg_id = msg.reply_to_message.message_id
            response = await db(lambda: supabase.table("menfess_map").select("sender_user_id, post_id").eq("discussion_message_id", replied_msg_id).execute())
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
                await context.bot.send_message(chat_id=sender_user_id, text=notif_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Lihat Balasan", url=link)]]), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"❌ Gagal proses balasan diskusi: {e}")


async def open_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = True
        # FIX: persist ke DB agar tidak reset saat bot restart
        try:
            await db(lambda: supabase.table("bot_settings").upsert({"key": "bot_active", "value": "true"}).execute())
        except Exception as e:
            logger.error(f"Gagal simpan bot_active ke DB: {e}")
        await update.message.reply_text("✅ Bot telah diaktifkan kembali.")


async def close_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = False
        # FIX: persist ke DB agar tidak reset saat bot restart
        try:
            await db(lambda: supabase.table("bot_settings").upsert({"key": "bot_active", "value": "false"}).execute())
        except Exception as e:
            logger.error(f"Gagal simpan bot_active ke DB: {e}")
        await update.message.reply_text("⏸️ Bot telah dipause.")


async def get_group_id(update: Update, context: CallbackContext):
    await update.message.reply_text(f"🆔 ID: `{update.effective_chat.id}`\n🏷️ Nama: {update.effective_chat.title or 'Private'}", parse_mode="Markdown")


async def get_all_user_ids():
    try:
        response = await db(lambda: supabase.table("users").select("user_id").execute())
        return [row["user_id"] for row in response.data] if hasattr(response, "data") and response.data else []
    except Exception:
        return []


async def menu(update: Update, context: CallbackContext):
    context.user_data.clear()
    
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text("👋 *Navigasi Bot*\n\nSilakan pilih menu di bawah:", parse_mode="Markdown", reply_markup=get_main_keyboard())


async def broadcast_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcastfw <link>")
    link = context.args[0]
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", link)
    if not match: return await update.message.reply_text("❌ Link tidak valid!")
    channel_username, message_id = match.groups()
    if channel_username == "c": return await update.message.reply_text("❌ Tidak bisa forward menggunakan link dari channel private!")

    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0: return await update.message.reply_text("⚠️ Tidak ada user di database.")

    sc, fc = 0, 0
    failed_users = []
    batch_size = 20 # Mengirim 50 pesan sekaligus secara paralel
    
    status_msg = await update.message.reply_text(f"⏳ *Memulai broadcast forward ke {total_users} user...*", parse_mode="Markdown")
    
    for i in range(0, total_users, batch_size):
        batch = user_list[i : i + batch_size]
        tasks = [context.bot.forward_message(chat_id=uid, from_chat_id=f"@{channel_username}", message_id=int(message_id)) for uid in batch]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                fc += 1
                failed_users.append(str(batch[idx]))
            else:
                sc += 1
        
        await status_msg.edit_text(f"⏳ *Sedang memproses broadcast forward... ({min(i + batch_size, total_users)}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
        await asyncio.sleep(1.5) # Jeda aman untuk Telegram API

    await status_msg.edit_text(f"✅ *Broadcast Forward Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

    if failed_users:
        await context.bot.send_document(
            chat_id=update.effective_chat.id, 
            document="\n".join(failed_users).encode('utf-8'),
            filename="failed_broadcast_forward.txt",
            caption=f"📄 Terdapat {len(failed_users)} user yang gagal menerima broadcast forward."
        )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcast <teks>")
    message_text = " ".join(context.args)
    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0: return await update.message.reply_text("⚠️ Tidak ada user di database.")

    sc, fc = 0, 0
    failed_users = []
    batch_size = 50
    
    status_msg = await update.message.reply_text(f"⏳ *Memulai broadcast ke {total_users} user...*", parse_mode="Markdown")
    
    for i in range(0, total_users, batch_size):
        batch = user_list[i : i + batch_size]
        tasks = [context.bot.send_message(chat_id=uid, text=message_text) for uid in batch]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                fc += 1
                failed_users.append(str(batch[idx]))
            else:
                sc += 1
        
        await status_msg.edit_text(f"⏳ *Sedang memproses broadcast... ({min(i + batch_size, total_users)}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
        await asyncio.sleep(1)

    await status_msg.edit_text(f"✅ *Broadcast Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

    if failed_users:
        await context.bot.send_document(
            chat_id=update.effective_chat.id, 
            document="\n".join(failed_users).encode('utf-8'),
            filename="failed_broadcast.txt",
            caption=f"📄 Terdapat {len(failed_users)} user yang gagal menerima pesan broadcast."
        )


async def add_command(update: Update, context: CallbackContext) -> None:
    # FIX: cek command_name tidak None sebelum memanggil .startswith(),
    # mencegah AttributeError crash ketika /addcommand dikirim via reply tanpa argumen.
    if update.message.reply_to_message:
        if not context.args:
            return await update.message.reply_text("Format (reply): /addcommand <nama>")
        command_name = context.args[0]
        command_content = update.message.reply_to_message.text
    else:
        if len(context.args) < 2:
            return await update.message.reply_text("Format: /addcommand <nama> <isi>")
        command_name, command_content = context.args[0], " ".join(context.args[1:])

    command_name = command_name if command_name.startswith("/") else "/" + command_name
    try:
        await db(lambda: supabase.table("commands").upsert({"name": command_name, "content": command_content}).execute())
        await update.message.reply_text(f"✅ `{command_name}` disimpan!", parse_mode='Markdown')
    except Exception:
        await update.message.reply_text("❌ Gagal.")


async def delete_command(update: Update, context: CallbackContext) -> None:
    if not context.args:
        return await update.message.reply_text("Format: /deletecommand <nama>")
    command_name = context.args[0] if context.args[0].startswith("/") else "/" + context.args[0]
    try:
        await db(lambda: supabase.table("commands").delete().eq("name", command_name).execute())
        await update.message.reply_text(f"✅ `{command_name}` dihapus!", parse_mode='Markdown')
    except Exception:
        await update.message.reply_text("❌ Gagal.")


# === FITUR TAMBAH KATA UNDERCOVER (ADMIN) ===
async def add_uc_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    text = " ".join(context.args)
    if "-" not in text:
        return await update.message.reply_text("❌ Format salah! Gunakan: `/adducword Nasi Goreng - Mie Goreng`", parse_mode="Markdown")
    words = text.split("-")
    w1, w2 = words[0].strip(), words[1].strip()
    try:
        await db(lambda: supabase.table("uc_words").insert({"word1": w1, "word2": w2}).execute())
        await update.message.reply_text(f"✅ Berhasil menambahkan kata: *{w1}* vs *{w2}*", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal masuk database: {e}")


# === INPUT KATA PEMAIN (COMMAND /vote) ===
async def submit_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI:
        return
    if not context.args:
        return await update.message.reply_text("Format salah! Ketik: `/vote [kata/kalimat deskripsi]`", parse_mode="Markdown")

    user_id = str(update.effective_user.id)
    desc_word = " ".join(context.args)

    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("status", "playing").execute())
    if not res.data:
        return

    game = next((g for g in res.data if user_id in g['players']), None)
    if not game:
        return

    players = game['players']
    players[user_id]['current_word'] = desc_word
    await db(lambda: supabase.table("uc_active_games").update({"players": players}).eq("game_id", game['game_id']).execute())
    await update.message.reply_text(f"✅ Deskripsi diterima dari {update.effective_user.first_name}!", reply_to_message_id=update.message.message_id)


async def get_discussion_link(comment_msg_id: int, thread_id: int = None):
    """Buat link publik ke komentar diskusi."""
    fallback = f"https://t.me/c/{str(GROUP_ID_DISKUSI).replace('-100', '')}/{comment_msg_id}"
    try:
        lookup_id = thread_id or comment_msg_id
        map_res = await db(lambda: supabase.table("menfess_map").select("post_id").eq("discussion_message_id", lookup_id).execute())
        if hasattr(map_res, 'data') and map_res.data:
            post_id = map_res.data[0]['post_id']
            channel_username = CHANNEL_ID.replace('@', '')
            return f"https://t.me/{channel_username}/{post_id}?comment={comment_msg_id}"
    except Exception as e:
        logger.error(f"Gagal membuat link diskusi: {e}")
    return fallback

# === LOBBY & CALLBACK GAME ===
async def start_undercover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI:
        return await update.message.reply_text("🎮 Game ini hanya bisa dimainkan di dalam Grup Diskusi!")

    creator_id = update.effective_user.id
    creator_name = update.effective_user.first_name
    creator_username = update.effective_user.username or str(creator_id)

    keyboard = [
        [InlineKeyboardButton("🎮 Gabung Game", callback_data="uc_join")], 
        [InlineKeyboardButton("▶️ Mulai Game", callback_data="uc_start")]
    ]
    msg = await update.message.reply_text(
        f"🕵️‍♂️ *GAME UNDERCOVER*\n\n👑 Room Master: {creator_name}\n\n👥 *Pemain Terdaftar:*\n1. {creator_name} (@{creator_username})\n\n*(Minimal 3 pemain)*",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

    players_data = {str(creator_id): {"name": creator_name, "username": creator_username, "current_word": "", "missed_turns": 0}}
    try:
        await db(lambda: supabase.table("uc_active_games").insert({
            "game_id": msg.message_id, "chat_id": update.effective_chat.id, "status": "lobby",
            "creator_id": creator_id, "players": players_data, "undercover_id": 0,
            "civilian_word": "", "undercover_word": "", "votes": {}
        }).execute())
    except Exception as e:
        logger.error(f"DB Error: {e}")


async def handle_uc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query.data.startswith("uc_"):
        return
    await query.answer()

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    username = update.effective_user.username or str(user_id)
    game_id = query.message.message_id

    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute())
    if not hasattr(res, 'data') or not res.data:
        return await query.edit_message_text("❌ Game ini sudah selesai atau dibatalkan.")

    game = res.data[0]
    players = game['players']

    thread_id = query.message.message_thread_id
    group_link = await get_discussion_link(game_id, thread_id)
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Kembali ke Grup", url=group_link)]])

    if query.data == "uc_join":
        if game['status'] != 'lobby':
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text=f"⚠️ {user_name}, game sudah dimulai!", reply_to_message_id=game_id)
        if str(user_id) in players:
            return

        players[str(user_id)] = {"name": user_name, "username": username, "current_word": "", "missed_turns": 0}
        await db(lambda: supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute())

        player_list = "\n".join([f"{i+1}. {p['name']} (@{p['username']})" for i, p in enumerate(players.values())])
        keyboard = [[InlineKeyboardButton("🎮 Gabung", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai", callback_data="uc_start")]]
        await query.edit_message_text(f"🕵️‍♂️ *GAME UNDERCOVER*\n\n👥 *Pemain Terdaftar:*\n{player_list}\n\n*(Minimal 3 pemain)*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "uc_start":
        if user_id != game['creator_id']:
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text="⚠️ Hanya Room Master yang bisa memulai game ini!", reply_to_message_id=game_id)

        if len(players) < 3:
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text="⚠️ Minimal butuh 3 orang untuk mulai!", reply_to_message_id=game_id)

        words_res = await db(lambda: supabase.table("uc_words").select("*").execute())
        if not words_res.data:
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text="❌ Kata di database kosong!")
        word_pair = random.choice(words_res.data)

        player_ids = list(players.keys())
        random.shuffle(player_ids)
        undercover_id = player_ids[0]

        await db(lambda: supabase.table("uc_active_games").update({
            "status": "playing", "undercover_id": int(undercover_id),
            "civilian_word": word_pair['word1'], "undercover_word": word_pair['word2']
        }).eq("game_id", game_id).execute())

        for pid in player_ids:
            kata = word_pair['word2'] if pid == undercover_id else word_pair['word1']
            try:
                await context.bot.send_message(chat_id=int(pid), text=f"🕵️‍♂️ *Peranmu:* ???\n🤫 *Katamu:* *{kata}*", reply_markup=btn_grup, parse_mode="Markdown")
            except Exception:
                pass

        urutan = "\n".join([f"{i+1}. {players[pid]['name']} (@{players[pid]['username']})" for i, pid in enumerate(player_ids)])
        cara_main = (
            "📜 *TATA CARA MAIN:*\n"
            "1. Cek kata rahasia kamu di DM bot.\n"
            "2. Tiap ronde, ketik `/vote [deskripsi]` di grup.\n"
            "3. *Dilarang menyebut kata secara langsung.*\n"
            "4. Setelah 5 ronde, vote siapa yang mencurigakan dengan `/sus @username`."
        )
        await query.edit_message_text(
            f"🎯 *GAME DIMULAI!*\nCek DM bot untuk kata rahasia!\n\n🔄 *Urutan Bermain:*\n{urutan}\n\n{cara_main}\n\n⏳ *Waktu: 10 Menit (2 menit/Ronde)*", 
            parse_mode="Markdown"
        )

        asyncio.create_task(run_game_timer(GROUP_ID_DISKUSI, game_id, thread_id, context))


# === FITUR INPUT KATA (/vote) - WAJIB ADA AGAR PEMAIN TIDAK AFK ===
async def submit_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI:
        return
    if not context.args:
        return await update.message.reply_text("⚠️ Format: `/vote [deskripsi kata kamu]`", parse_mode="Markdown")

    voter_id = str(update.effective_user.id)
    word = " ".join(context.args)

    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("status", "playing").execute())
    if not res.data:
        return await update.message.reply_text("❌ Tidak ada game yang sedang berjalan.")

    game = next((g for g in res.data if voter_id in g['players']), None)
    if not game:
        return await update.message.reply_text("❌ Kamu tidak bermain di game manapun yang aktif.")

    players = game['players']
    if players[voter_id].get('current_word'):
        return await update.message.reply_text("⚠️ Kamu sudah memberikan deskripsi untuk ronde ini!")

    players[voter_id]['current_word'] = word
    await db(lambda: supabase.table("uc_active_games").update({"players": players}).eq("game_id", game['game_id']).execute())
    await update.message.reply_text(f"✅ *{update.effective_user.first_name}* telah mengunci kata!", parse_mode="Markdown", reply_to_message_id=update.message.message_id)


# === LOOP TIMER (DENGAN FIX SELECT "*") ===
# === LOOP TIMER (DENGAN FIX SELECT "*" & NOTIF AFK GAME OVER) ===
async def run_game_timer(chat_id, game_id, thread_id, context, start_round=1):
    for i in range(start_round, 6):
        await asyncio.sleep(120)  # Ganti jadi 15 saat mau testing biar cepat
        
        res = await db(lambda: supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute())
        if not hasattr(res, 'data') or not res.data:
            return
        game = res.data[0]
        if game['status'] != 'playing':
            return

        players = game['players']
        recap = f"⏱️ *Ronde {i} Selesai!*\n\n*Rekap Kata Pemain:*\n"
        dead_players = []
        
        # Simpan daftar ID semua pemain sebelum ada yang dihapus karena AFK
        # agar semuanya tetap dapat notif DM dan koin.
        all_players_id = list(players.keys())
        
        for pid, pdata in list(players.items()):
            word = pdata.get('current_word', "")
            if not word:
                pdata['missed_turns'] = pdata.get('missed_turns', 0) + 1
                if pdata['missed_turns'] >= 2:
                    display_word = "💀 *DIEKSEKUSI MATI (AFK 2x)*"
                    dead_players.append(pid)
                else:
                    display_word = "⚠️ *(Tidak ada deskripsi, awas dieksekusi!)*"
            else:
                pdata['missed_turns'] = 0
                display_word = f"*{word}*"
                
            recap += f"- {pdata['name']}: {display_word}\n"
            if pid not in dead_players:
                players[pid]['current_word'] = ""

        # Eksekusi mati (hapus dari dict players)
        for pid in dead_players:
            del players[pid]

        await db(lambda: supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute())

        # CEK KONDISI GAME OVER KARENA AFK
        if dead_players:
            under_id = str(game['undercover_id'])
            is_game_over = False
            hasil_text = ""
            is_undercover_caught = False

            if under_id in dead_players:
                hasil_text = f"{recap}\n🎉 *GAME OVER!* Undercover tewas dieksekusi karena AFK! **CIVILIAN MENANG!**\n\n💰 Hadiah:\n- Civilian: +200 Coins\n- Undercover: +100 Coins"
                is_game_over = True
                is_undercover_caught = True
            elif len(players) <= 2 and under_id in players:
                hasil_text = f"{recap}\n😈 *GAME OVER!* Terlalu banyak Civilian mati AFK! **UNDERCOVER MENANG!**\n\n💰 Hadiah:\n- Undercover: +200 Coins\n- Civilian: +100 Coins"
                is_game_over = True
                is_undercover_caught = False

            # Jika game berakhir karena AFK, bagikan koin dan kirim DM!
            if is_game_over:
                result_msg = await context.bot.send_message(chat_id, hasil_text, reply_to_message_id=game_id, parse_mode="Markdown")
                result_link = await get_discussion_link(result_msg.message_id, thread_id)
                btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Lihat Hasil Diskusi", url=result_link)]])

                for pid in all_players_id:
                    # Logika bagi koin
                    koin = 100 if pid == under_id else 200 if is_undercover_caught else (200 if pid == under_id else 100)
                    await add_kith_coins(int(pid), koin)
                    
                    # Kirim DM
                    try:
                        await context.bot.send_message(int(pid), f"🏁 *GAME OVER (EKSEKUSI AFK)!*\n\n{hasil_text}", reply_markup=btn_grup, parse_mode="Markdown")
                    except Exception:
                        pass
                
                await db(lambda: supabase.table("uc_active_games").delete().eq("game_id", game_id).execute())
                return
                
        if i < 5:
            pesan_ronde = f"{recap}\n🔔 Masuk *Ronde {i+1}*! Silakan diskusi dan ketik `/vote [deskripsi]`!"
            round_msg = await context.bot.send_message(chat_id, pesan_ronde, reply_to_message_id=game_id, parse_mode="Markdown")
            round_link = await get_discussion_link(round_msg.message_id, thread_id)
            btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Ke Ronde Terbaru", url=round_link)]])

            for pid in players.keys():
                try:
                    await context.bot.send_message(int(pid), f"🔔 {pesan_ronde}", reply_markup=btn_grup, parse_mode="Markdown")
                except Exception:
                    pass

    # Fase Voting
    await db(lambda: supabase.table("uc_active_games").update({"status": "voting"}).eq("game_id", game_id).execute())
    pesan_vote = "🚨 *WAKTU HABIS!*\n\nSesi VOTE dimulai selama 2 menit.\nKetik: `/sus @username` untuk menuduh Undercover!"
    vote_msg = await context.bot.send_message(chat_id, pesan_vote, reply_to_message_id=game_id, parse_mode="Markdown")
    vote_link = await get_discussion_link(vote_msg.message_id, thread_id)
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Ke Sesi Vote", url=vote_link)]])

    for pid in players.keys():
        try:
            await context.bot.send_message(int(pid), f"🔔 {pesan_vote}", reply_markup=btn_grup, parse_mode="Markdown")
        except Exception:
            pass

    await asyncio.sleep(120)
    await tally_votes(chat_id, game_id, thread_id, context)


# === VOTE COMMAND (/sus) ===
async def sus_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI:
        return
    if not context.args:
        return await update.message.reply_text("Format: `/sus @username`", parse_mode="Markdown")

    voter_id = str(update.effective_user.id)
    target_username = context.args[0].replace("@", "").lower()

    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("status", "voting").execute())
    if not res.data:
        return await update.message.reply_text("❌ Tidak ada sesi voting yang aktif.")

    game = next((g for g in res.data if voter_id in g['players']), None)
    if not game:
        return await update.message.reply_text("❌ Kamu tidak bermain di sesi voting manapun.")

    players = game['players']
    target_id = None
    for pid, pdata in players.items():
        if pdata['username'].lower() == target_username:
            target_id = pid
            break

    if not target_id:
        return await update.message.reply_text(f"❌ Pemain @{target_username} tidak ditemukan di game ini.")

    votes = game.get('votes', {})
    votes[voter_id] = target_id
    await db(lambda: supabase.table("uc_active_games").update({"votes": votes}).eq("game_id", game['game_id']).execute())
    await update.message.reply_text(f"✅ {update.effective_user.first_name} menuduh @{target_username}!", reply_to_message_id=update.message.message_id)


# === TALLY & REWARDS ===
async def tally_votes(chat_id, game_id, thread_id, context):
    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute())
    if not hasattr(res, 'data') or not res.data:
        return
    game = res.data[0]
    if game['status'] != "voting":
        return

    votes = game.get('votes', {})
    players = game['players']
    under_id = str(game['undercover_id'])

    vote_counts = {}
    for target in votes.values():
        vote_counts[target] = vote_counts.get(target, 0) + 1

    if vote_counts:
        suspect_id = max(vote_counts, key=vote_counts.get)
        suspect_name = players[suspect_id]['name']
    else:
        suspect_id = None
        suspect_name = "Tidak ada"

    is_undercover_caught = (suspect_id == under_id)
    undercover_name = players[under_id]['name']

    hasil_text = f"⚖️ *HASIL VOTING*\n\nTerbanyak divote: *{suspect_name}*\nIdentitas Undercover asli: *{undercover_name}*\n\n"

    if is_undercover_caught:
        hasil_text += "🎉 *CIVILIAN MENANG!* Undercover berhasil ditangkap!\n\n💰 Hadiah:\n- Civilian: +200 Coins\n- Undercover: +100 Coins"
    else:
        hasil_text += "😈 *UNDERCOVER MENANG!* Kalian salah tangkap!\n\n💰 Hadiah:\n- Undercover: +200 Coins\n- Civilian: +100 Coins"

    result_msg = await context.bot.send_message(chat_id, hasil_text, reply_to_message_id=game_id, parse_mode="Markdown")
    result_link = await get_discussion_link(result_msg.message_id, thread_id)
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Lihat Hasil Diskusi", url=result_link)]])

    for pid in players.keys():
        koin = 100 if pid == under_id else 200 if is_undercover_caught else (200 if pid == under_id else 100)
        await add_kith_coins(int(pid), koin)
        try:
            await context.bot.send_message(int(pid), f"🏁 *GAME OVER!*\n\n{hasil_text}", reply_markup=btn_grup, parse_mode="Markdown")
        except Exception:
            pass

    await db(lambda: supabase.table("uc_active_games").delete().eq("game_id", game_id).execute())


# === CONTINUE GAME (/continue) ===
async def continue_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI:
        return await update.message.reply_text("🎮 Command ini hanya bisa digunakan di dalam Grup Diskusi!")

    # Cek format argumen
    if len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ Format salah!\nGunakan: `/continue [id_game] [ronde]`\nContoh: `/continue 123456789 3`", 
            parse_mode="Markdown"
        )

    try:
        game_id = int(context.args[0])
        start_round = int(context.args[1])
    except ValueError:
        return await update.message.reply_text("⚠️ ID Game dan Ronde harus berupa angka!")

    if start_round < 1 or start_round > 5:
        return await update.message.reply_text("⚠️ Ronde harus berada di antara 1 sampai 5!")

    # Ambil data game dari database
    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute())
    if not res.data:
        return await update.message.reply_text(f"❌ Game dengan ID {game_id} tidak ditemukan atau sudah selesai.")

    game = res.data[0]
    players = game['players']
    undercover_id = str(game['undercover_id'])
    civilian_word = game['civilian_word']
    undercover_word = game['undercover_word']
    
    # Set status kembali ke playing
    await db(lambda: supabase.table("uc_active_games").update({"status": "playing"}).eq("game_id", game_id).execute())

    thread_id = update.message.message_thread_id
    group_link = await get_discussion_link(game_id, thread_id)
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Kembali ke Grup", url=group_link)]])
    
    # ==== PROSES KIRIM NOTIFIKASI KE DM MASING-MASING PEMAIN ====
    for pid in players.keys():
        # Kirim ulang kata rahasia mereka biar kalau lupa tidak perlu scroll grup/DM lama
        kata_rahasia = undercover_word if pid == undercover_id else civilian_word
        
        pesan_dm = (
            f"▶️ *GAME UNDERCOVER DILANJUTKAN!*\n\n"
            f"🔄 Game masuk kembali ke *Ronde {start_round}*.\n"
            f"🤫 *Pengingat Katamu:* *{kata_rahasia}*\n\n"
            f"⏳ Yuk balik ke grup buat diskusi dan ketik `/vote [deskripsi]`!"
        )
        try:
            await context.bot.send_message(
                chat_id=int(pid), 
                text=pesan_dm, 
                reply_markup=btn_grup, 
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Gagal kirim DM ke player {pid}: {e}")
            pass
    # ============================================================

    # Kirim konfirmasi di Grup Diskusi
    await update.message.reply_text(
        f"▶️ *MELANJUTKAN GAME*\n"
        f"ID Game: `{game_id}`\n"
        f"Melanjutkan dari: *Ronde {start_round}*\n\n"
        f"📢 Notifikasi kelanjutan game dan pengingat kata rahasia sudah dikirim ke DM seluruh pemain yang terdaftar!", 
        parse_mode="Markdown"
    )

    # Picu ulang task background timer
    asyncio.create_task(run_game_timer(GROUP_ID_DISKUSI, game_id, thread_id, context, start_round=start_round))

async def set_profile(update: Update, context: CallbackContext):
    context.user_data["keyboard_state"] = "ANON_AGE"
    
    keyboard = [
        [KeyboardButton("Legal (≥ 18)"), KeyboardButton("Minor (< 18)")],
        [KeyboardButton("❌ Cancel")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "👤 *SETUP PROFIL ANONIM*\n\n1️⃣ Pilih kategori umur kamu:", 
        parse_mode="Markdown", 
        reply_markup=reply_markup
    )

async def search_anon(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    TOPIC_ID_ANON_LOG = 5417 
    
    res = await db(lambda: supabase.table("users").select("chat_state, age_group, gender, orientation").eq("user_id", user_id).execute())
    user_data = res.data[0] if res.data else None
    
    if not user_data: return await update.message.reply_text("Data belum tersimpan, ketik /start dulu ya.")
    if not user_data.get('age_group') or not user_data.get('gender') or not user_data.get('orientation'):
        return await update.message.reply_text("Isi profil dulu yuk sebelum mencari partner menggunakan command /setprofile.")
    if user_data.get('chat_state') != 'menfess':
        return await update.message.reply_text("Kamu sedang dalam antrean atau obrolan. Ketik /stop untuk membatalkan.")

    search_res = await db(lambda: supabase.table("users").select("user_id, age_group, gender, orientation").eq("chat_state", "searching").neq("user_id", user_id).execute())
    potential_partners = search_res.data if hasattr(search_res, 'data') and search_res.data else []
    
    matched_partner = None
    matched_partner_data = None
    
    for p in potential_partners:
        if p.get('age_group') != user_data.get('age_group'): continue
        
        a_gender = user_data.get('gender')
        a_oris = user_data.get('orientation', '').split(',')
        
        b_gender = p.get('gender')
        b_oris = p.get('orientation', '').split(',')
        
        is_match = False
        
        # Logika kecocokan orientasi seksual
        if a_gender == 'male' and b_gender == 'male':
            if 'bxb' in a_oris and 'bxb' in b_oris: is_match = True
        elif a_gender == 'female' and b_gender == 'female':
            if 'gxg' in a_oris and 'gxg' in b_oris: is_match = True
        elif (a_gender == 'male' and b_gender == 'female') or (a_gender == 'female' and b_gender == 'male'):
            if 'bxg' in a_oris and 'bxg' in b_oris: is_match = True
        else:
            if 'nbxnb' in a_oris and 'nbxnb' in b_oris: is_match = True
            
        if is_match:
            matched_partner = p['user_id']
            matched_partner_data = p
            break
            
    if matched_partner:
        await db(lambda: supabase.table("users").update({"chat_state": "chatting", "partner_id": matched_partner}).eq("user_id", user_id).execute())
        await db(lambda: supabase.table("users").update({"chat_state": "chatting", "partner_id": user_id}).eq("user_id", matched_partner).execute())
        
        # LOG KE TOPIC 5417
        log_text = (f"🔍 *Anon Match Found*\n\n👤 User 1: `{user_id}` (G:{user_data.get('gender')}, O:{user_data.get('orientation')})\n👤 User 2: `{matched_partner}` (G:{matched_partner_data.get('gender')}, O:{matched_partner_data.get('orientation')})\n━━━━━━━━━━━━━━━━\nStatus: Berhasil terhubung")
        try:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=TOPIC_ID_ANON_LOG, text=log_text, parse_mode="Markdown")
        except:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=log_text, parse_mode="Markdown")
        
        success_text = "🎉 Partner ditemukan! Silakan mulai menyapa.\n\n*(Ketik /stop atau tekan tombol di bawah untuk mengakhiri obrolan dan kembali ke mode menfess)*"
        await update.message.reply_text(success_text, parse_mode="Markdown", reply_markup=get_stop_anon_keyboard())
        await context.bot.send_message(chat_id=matched_partner, text=success_text, parse_mode="Markdown", reply_markup=get_stop_anon_keyboard())
    else:
        await db(lambda: supabase.table("users").update({"chat_state": "searching"}).eq("user_id", user_id).execute())
        await update.message.reply_text("🔍 Mencari partner yang cocok... (Maksimal tunggu 10 menit)")
        # Kirim context ke fallback agar bisa mengirim pesan ke user
        asyncio.create_task(wait_for_partner_timeout(user_id, context))

async def wait_for_partner_timeout(user_id: int, context: CallbackContext):
    await asyncio.sleep(600)  # Menunggu selama 10 Menit
    
    res = await db(lambda: supabase.table("users").select("chat_state").eq("user_id", user_id).execute())
    if res.data and res.data[0].get("chat_state") == "searching":
        # Kembalikan ke mode menfess
        await db(lambda: supabase.table("users").update({"chat_state": "menfess"}).eq("user_id", user_id).execute())

        fail_text = "Maaf yaa, ga ada partner yang sesuai dengan kriteria kamu saat ini. Coba cari lagi nanti ya!"
        await context.bot.send_message(chat_id=user_id, text=fail_text, reply_markup=get_main_keyboard())

async def stop_anon(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    res = await db(lambda: supabase.table("users").select("chat_state, partner_id").eq("user_id", user_id).execute())
    
    if res.data:
        state = res.data[0].get("chat_state")
        partner_id = res.data[0].get("partner_id")
        
        if state in ["searching", "chatting", "chatting_admin"]:
            await db(lambda: supabase.table("users").update({"chat_state": "menfess", "partner_id": None}).eq("user_id", user_id).execute())
            
            # Mengembalikan keyboard utama untuk user yang menekan tombol
            await update.message.reply_text(
                "🔴 Kamu telah meninggalkan obrolan. (Kembali ke mode menfess)", 
                reply_markup=get_main_keyboard()
            )
            
            if state == "chatting_admin":
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID, 
                    text=f"🔴 Sesi #AnonFallback dengan ID `{user_id}` telah diakhiri oleh user.", 
                    parse_mode="Markdown"
                )
            elif state == "chatting" and partner_id:
                await db(lambda: supabase.table("users").update({"chat_state": "menfess", "partner_id": None}).eq("user_id", partner_id).execute())
                
                # Mengembalikan keyboard utama untuk partner
                await context.bot.send_message(
                    chat_id=partner_id, 
                    text="🔴 Partner kamu telah meninggalkan obrolan. (Kembali ke mode menfess)", 
                    reply_markup=get_main_keyboard()
                )

# === SISTEM LIVE PHOTO ===
def _get_video_file_from_message(msg):
    """Ambil video dari pesan langsung, dokumen video, animation/GIF, atau pesan yang di-reply."""
    if not msg:
        return None

    for attr in ("video", "document", "animation"):
        obj = getattr(msg, attr, None)
        if not obj:
            continue
        if attr == "document":
            mime_type = getattr(obj, "mime_type", "") or ""
            if not mime_type.startswith("video/"):
                continue
        return obj

    if getattr(msg, "reply_to_message", None):
        return _get_video_file_from_message(msg.reply_to_message)

    return None


async def _run_cmd(cmd, timeout=120):
    """Jalankan command async supaya bot tidak freeze saat FFmpeg memproses video."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise RuntimeError(f"Command timed out after {timeout} seconds.")

    if proc.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"Command failed:\n{detail[-1800:]}")

    return stdout, stderr


async def _safe_edit_text(message, text, **kwargs):
    """Edit pesan status tanpa membuat proses utama gagal kalau Telegram menolak edit."""
    if not message:
        return None
    try:
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        logger.warning(f"Gagal edit pesan status Live Photo, proses tetap dilanjutkan: {e}")
        return None


async def _safe_delete_message(message):
    """Hapus pesan status tanpa membuat proses utama gagal."""
    if not message:
        return None
    try:
        return await message.delete()
    except Exception:
        return None


async def _send_live_photo_direct(bot_token, chat_id, video_path, photo_path, message_thread_id=None, reply_to_message_id=None):
    """Kirim native Live Photo langsung ke Bot API."""
    if not bot_token:
        raise RuntimeError("BOT_TOKEN belum tersedia di environment variables.")

    url = f"{TELEGRAM_API_BASE.rstrip('/')}/bot{bot_token}/sendLivePhoto"
    data = {"chat_id": str(chat_id)}

    if message_thread_id:
        data["message_thread_id"] = str(message_thread_id)

    if reply_to_message_id:
        data["reply_parameters"] = json.dumps({
            "message_id": reply_to_message_id,
            "allow_sending_without_reply": True,
        })

    timeout = httpx.Timeout(180.0, connect=30.0, read=180.0, write=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with open(video_path, "rb") as vf, open(photo_path, "rb") as pf:
            files = {
                "live_photo": ("live_photo.mp4", vf, "video/mp4"),
                "photo": ("photo.jpg", pf, "image/jpeg"),
            }
            resp = await client.post(url, data=data, files=files)

    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(f"Telegram API tidak mengembalikan JSON. HTTP {resp.status_code}: {resp.text[:1000]}")

    if resp.status_code >= 400 or not payload.get("ok"):
        description = payload.get("description") or resp.text[:1000]
        error_code = payload.get("error_code", resp.status_code)
        raise RuntimeError(f"Telegram sendLivePhoto gagal ({error_code}): {description}")

    return payload.get("result")


async def live_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    video = _get_video_file_from_message(msg)

    if not video:
        return await msg.reply_text(
            "Silakan kirim video terlebih dahulu.",
            reply_markup=get_main_keyboard(),
        )

    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        return await msg.reply_text(
            "❌ FFmpeg belum kebaca di server.",
            reply_markup=get_main_keyboard(),
        )

    file_size = getattr(video, "file_size", 0) or 0
    if file_size > (LIVE_MAX_INPUT_FILE_SIZE_MB * 1024 * 1024):
        return await msg.reply_text(
            f"❌ Video terlalu besar (Max {LIVE_MAX_INPUT_FILE_SIZE_MB}MB).",
            reply_markup=get_main_keyboard(),
        )

    current_balance = 0
    charged = False
    try:
        res = await db(lambda: supabase.table("users").select("kith_coins").eq("user_id", user_id).execute())
        current_balance = res.data[0].get("kith_coins") if res.data and res.data[0].get("kith_coins") is not None else 0

        if LIVE_PHOTO_PRICE > 0 and current_balance < LIVE_PHOTO_PRICE:
            return await msg.reply_text(
                f"❌ Kith-Coins kurang (Biaya: {LIVE_PHOTO_PRICE} Coins). Saldo: {current_balance}",
                reply_markup=get_main_keyboard(),
            )

        if LIVE_PHOTO_PRICE > 0:
            await db(lambda: supabase.table("users").update({"kith_coins": current_balance - LIVE_PHOTO_PRICE}).eq("user_id", user_id).execute())
            charged = True
    except Exception as e:
        logger.error(f"Gagal mengecek/memotong saldo Live Photo: {e}")
        return await msg.reply_text("❌ Gagal mengecek saldo.", reply_markup=get_main_keyboard())

    charge_text = f" *(Saldo dipotong {LIVE_PHOTO_PRICE} Coins)*" if LIVE_PHOTO_PRICE > 0 else ""
    status_msg = await msg.reply_text(
        f"⏳ Memproses Live Photo...{charge_text}\nTahap 1/4: download video",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )

    asset_id = str(uuid.uuid4()).upper()
    input_path = f"in_{asset_id}.mp4"
    output_live_photo = f"out_{asset_id}.mp4"
    output_photo = f"pic_{asset_id}.jpg"

    try:
        telegram_file = await video.get_file()
        await telegram_file.download_to_drive(input_path)

        await _safe_edit_text(status_msg, "⏳ Tahap 2/4: convert ke format Live Photo")

        max_output_bytes = LIVE_MAX_OUTPUT_FILE_SIZE_MB * 1024 * 1024
        attempts = [
            {"width": 720, "vb": "3500k", "mr": "4200k", "bs": "8400k", "ab": "96k"},
            {"width": 540, "vb": "2200k", "mr": "2600k", "bs": "5200k", "ab": "80k"},
            {"width": 480, "vb": "1400k", "mr": "1700k", "bs": "3400k", "ab": "64k"},
        ]
        last_error = ""
        encoded_ok = False

        for attempt in attempts:
            try:
                if os.path.exists(output_live_photo):
                    os.remove(output_live_photo)

                vf = f"scale={attempt['width']}:-2:force_original_aspect_ratio=decrease,setsar=1,fps=30"
                await _run_cmd([
                    ffmpeg_exe,
                    "-hide_banner", "-loglevel", "error",
                    "-i", input_path,
                    "-t", str(LIVE_MAX_DURATION),
                    "-map", "0:v:0",
                    "-map", "0:a?",
                    "-vf", vf,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-b:v", attempt["vb"],
                    "-maxrate", attempt["mr"],
                    "-bufsize", attempt["bs"],
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", attempt["ab"],
                    "-movflags", "+faststart",
                    "-y", output_live_photo,
                ], timeout=240)

                if not os.path.exists(output_live_photo) or os.path.getsize(output_live_photo) == 0:
                    last_error = "FFmpeg tidak menghasilkan file video."
                    continue

                output_size = os.path.getsize(output_live_photo)
                if output_size <= max_output_bytes:
                    encoded_ok = True
                    break

                last_error = f"hasil masih {output_size / (1024 * 1024):.2f} MB, melebihi batas {LIVE_MAX_OUTPUT_FILE_SIZE_MB} MB"
            except Exception as e:
                last_error = str(e)

        if not encoded_ok:
            raise RuntimeError(f"Gagal membuat video Live Photo <= {LIVE_MAX_OUTPUT_FILE_SIZE_MB} MB. {last_error}")

        await _safe_edit_text(status_msg, "⏳ Tahap 3/4: extract static photo")
        await _run_cmd([
            ffmpeg_exe,
            "-hide_banner", "-loglevel", "error",
            "-ss", "0.2",
            "-i", output_live_photo,
            "-vframes", "1",
            "-q:v", "2",
            "-y", output_photo,
        ], timeout=120)

        if not os.path.exists(output_photo) or os.path.getsize(output_photo) == 0:
            raise RuntimeError("Gagal membuat static photo untuk Live Photo.")

        await _safe_edit_text(status_msg, "⏳ Tahap 4/4: upload Live Photo")
        await _send_live_photo_direct(
            BOT_TOKEN,
            msg.chat_id,
            output_live_photo,
            output_photo,
            getattr(msg, "message_thread_id", None),
            msg.message_id,
        )

        await _safe_delete_message(status_msg)

    except Exception as e:
        logger.exception("Gagal live photo")
        refund_note = ""
        if charged:
            try:
                await db(lambda: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute())
                ulang_note = f"- silahkan coba kembali."
                refund_note = f"\n\nKoin kamu telah di-Refund {LIVE_PHOTO_PRICE} Coins."
            except Exception as refund_err:
                logger.error(f"Gagal refund Live Photo untuk {user_id}: {refund_err}")

        await msg.reply_text(
            f"❌ Gagal memproses Live Photo:\n{str(e)[:1500]}{ulang_note}{refund_note}",
            reply_markup=get_main_keyboard(),
        )
    finally:
        for p in [input_path, output_live_photo, output_photo]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        await _safe_delete_message(status_msg)

async def handle_del_menfess(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer() # Harus dipanggil pertama kali
    
    data = query.data.split("_")
    if len(data) < 3: return
    
    user_id = int(data[1])
    post_id = int(data[2])

    try:
        # Hapus dari channel
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=post_id)
        
        # Kirim DM teguran
        await context.bot.send_message(
            chat_id=user_id, 
            text="❌ *Pesan kamu dihapus admin karena tidak sesuai ketentuan base. Silakan baca rules kembali.*", 
            parse_mode="Markdown"
        )
        
        # Update log
        await query.edit_message_text(f"{query.message.text_markdown}\n\n✅ *Status: Dihapus & User ditegur.*", parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Gagal hapus menfess ID {post_id}: {e}")
        await query.answer("Gagal! Pastikan bot admin di channel & pesan masih ada.", show_alert=True)

async def settings(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    channels_text = "\n".join([f"𔐼 {c}" for c in required_channels]) if required_channels else "–"
    hashtags_text = "\n".join([f"𔐼 `{h}`" for h in CACHE_HASHTAGS]) if CACHE_HASHTAGS else "–"
    global MENFESS_MODE
    try:
        response = await db(lambda: supabase.table("commands").select("name, content").execute())
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


async def handle_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not update.message or not update.message.text:
        return
    command_name = update.message.text.split()[0]
    try:
        response = await db(lambda: supabase.table("commands").select("content").eq("name", command_name).execute())
        if hasattr(response, 'data') and response.data:
            await update.message.reply_text(response.data[0]["content"], parse_mode="Markdown", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Gagal menjalankan custom command {command_name}: {e}")


async def refresh_total_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    try:
        res = await db(lambda: supabase.table("users").select("user_id, kith_coins, total_kith_coins").execute())
        updated = 0

        for user in res.data:
            total = user.get("total_kith_coins")
            current = user.get("kith_coins", 0)

            if total is None or total == 0:
                uid = user["user_id"]
                await db(lambda u=uid, c=current: supabase.table("users").update({"total_kith_coins": c}).eq("user_id", u).execute())
                updated += 1

        await update.message.reply_text(f"✅ Refresh total coin selesai!\nUser diupdate: {updated}")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def break_all_anon(update: Update, context: CallbackContext):
    # Hanya bisa dijalankan di grup admin
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    status_msg = await update.message.reply_text("⏳ Sedang memutus sesi anonim dan me-reset data profil semua user...")

    try:
        # 1. Tarik user yang statusnya bukan 'menfess' (untuk dikirimi notif putus obrolan)
        res_active = await db(lambda: supabase.table("users").select("user_id").neq("chat_state", "menfess").execute())
        affected_users = [row["user_id"] for row in res_active.data] if res_active and hasattr(res_active, 'data') and res_active.data else []
        
        # 2. Reset status obrolan bagi mereka yang nyangkut di chatting/searching
        if affected_users:
            await db(lambda: supabase.table("users").update({
                "chat_state": "menfess", 
                "partner_id": None
            }).neq("chat_state", "menfess").execute())
        
        # 3. WIPE OUT data profil (umur, gender, orientasi) UNTUK SEMUA USER
        # Pakai filter neq("user_id", 0) sebagai trik Supabase untuk meng-update semua baris
        await db(lambda: supabase.table("users").update({
            "age_group": None,
            "gender": None,
            "orientation": None
        }).neq("user_id", 0).execute())
        
        # 4. Kirim notifikasi putus obrolan HANYA ke user yang tadinya lagi chatting/searching
        berhasil, gagal = 0, 0
        for uid in affected_users:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text="🔴 Sesi obrolan anonim dihentikan oleh admin. Data profil anonim juga telah di-reset untuk pembaruan sistem.\n\n(Kembali ke mode menfess. Silakan ketik /setprofile untuk mengisi ulang data kamu).",
                    reply_markup=get_main_keyboard()
                )
                berhasil += 1
            except Exception as e:
                logger.error(f"Gagal kirim notif break_anon ke {uid}: {e}")
                gagal += 1
            
            await asyncio.sleep(0.1)  # Jeda aman Telegram API
        
        # 5. Laporan akhir ke admin
        await status_msg.edit_text(
            f"✅ *Break Anon & Reset Profil Selesai!*\n\n"
            f"👥 Sesi obrolan yang diputus: {len(affected_users)}\n"
            f"🔄 Seluruh data profil anon user di database berhasil dikosongkan.\n\n"
            f"✅ Notif terkirim: {berhasil}\n"
            f"❌ Notif gagal: {gagal}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error di break_all_anon: {e}")
        await status_msg.edit_text(f"❌ Terjadi kesalahan saat eksekusi: `{e}`", parse_mode="Markdown")


async def refresh_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    await update.message.reply_text("⏳ Memulai kalkulasi Kith-Coins retroaktif... Mohon tunggu, proses ini butuh waktu.")
    try:
        response = await db(lambda: supabase.table("menfess_map").select("sender_user_id").execute())
        if not hasattr(response, 'data') or not response.data:
            return await update.message.reply_text("❌ Data menfess map masih kosong.")

        # Hitung jumlah menfess per user
        user_counts = {}
        for row in response.data:
            uid = row.get("sender_user_id")
            if uid:
                user_counts[uid] = user_counts.get(uid, 0) + 1

        # FIX: Batch semua DB updates dalam satu langkah, bukan N kali select+update per user.
        # Ambil semua data user yang relevan sekaligus.
        all_uids = list(user_counts.keys())
        users_res = await db(lambda: supabase.table("users").select("user_id, kith_coins, total_kith_coins").in_("user_id", all_uids).execute())
        users_map = {row["user_id"]: row for row in (users_res.data or [])}

        batch_updates = []
        for uid, count in user_counts.items():
            reward_coins = count * 50
            row = users_map.get(uid, {})
            current_coins = row.get("kith_coins") or 0
            current_total = row.get("total_kith_coins") or current_coins
            batch_updates.append({
                "user_id": uid,
                "kith_coins": current_coins + reward_coins,
                "total_kith_coins": current_total + reward_coins,
                "_reward": reward_coins,
                "_count": count,
                "_new_balance": current_coins + reward_coins,
            })

        # Upsert semua sekaligus
        upsert_data = [{"user_id": u["user_id"], "kith_coins": u["kith_coins"], "total_kith_coins": u["total_kith_coins"]} for u in batch_updates]
        await db(lambda: supabase.table("users").upsert(upsert_data).execute())

        # Kirim notifikasi ke tiap user (tetap satu per satu karena Telegram rate limit)
        berhasil, gagal = 0, 0
        for u in batch_updates:
            notif_text = (
                f"🎉 *Kejutan Kith-Coins Retroaktif!*\n\n"
                f"Terima kasih atas loyalitas kamu! Karena kamu sudah pernah mengirim *{u['_count']} menfess* di Kitheons sebelumnya, "
                f"kamu berhak mendapatkan kompensasi sebesar *{u['_reward']} Kith-Coins*!\n\n"
                f"🪙 Saldo Koin kamu sekarang: *{u['_new_balance']}*\n\n"
                f"Koin ini bisa kamu tukarkan ke berbagai fitur mendatang seperti *Custom Title Loyalty* dan lain-lain. Pantengin terus update dari admin ya!"
            )
            try:
                await context.bot.send_message(chat_id=u["user_id"], text=notif_text, parse_mode="Markdown")
                berhasil += 1
            except Exception as e:
                logger.error(f"Gagal kirim notif coin untuk user {u['user_id']}: {e}")
                gagal += 1
            await asyncio.sleep(0.1)

        await update.message.reply_text(f"✅ *Refresh Coin Selesai!*\n\n👤 User berhasil diproses: {berhasil}\n❌ Gagal kirim notif: {gagal}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error refresh coin: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat memproses data database.")


def main():
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

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

    # Commands admin
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('unblock', unblock_user))
    application.add_handler(CommandHandler('auto', set_mode_auto))
    application.add_handler(CommandHandler('manual', set_mode_manual))
    application.add_handler(CommandHandler('break_anon', break_all_anon))
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', menu))
    application.add_handler(CommandHandler('open', open_bot))
    application.add_handler(CommandHandler('close', close_bot))
    application.add_handler(CommandHandler('grupid', get_group_id))
    application.add_handler(CommandHandler('setrequired', set_required_channels))
    application.add_handler(CommandHandler('refresh_totalkoin', refresh_total_coin))
    application.add_handler(CommandHandler('refreshcoin', refresh_coin))

    # Fitur Profil & Leaderboard
    application.add_handler(CommandHandler('profile', cek_profile))
    application.add_handler(CommandHandler(['leaderboard', 'leadboard'], leaderboard))  # Sengaja support typo

    # Fitur Game
    application.add_handler(CommandHandler('adducword', add_uc_word))
    application.add_handler(CommandHandler('undercover', start_undercover))
    application.add_handler(CommandHandler('vote', submit_word))
    application.add_handler(CommandHandler('sus', sus_vote))
    application.add_handler(CommandHandler("continue", continue_game))

    # Tangkap klik Gabung / Mulai game
    application.add_handler(CallbackQueryHandler(handle_uc_callback, pattern="^uc_"))

    # Fitur Roleplay
    application.add_handler(CommandHandler('buytitle', buy_title))

    # Command /live untuk convert video
    application.add_handler(CommandHandler('live', live_photo_handler))

    # Conversation Handler untuk Menfess (Hanya masuk sini kalau AUTO)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_pesan)],
        states={
            WAITING_USERNAME: [MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_username)]
        },
        fallbacks=[CommandHandler('cancel', cancel_menfess)]
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('cancel', cancel_menfess, filters.ChatType.PRIVATE))

    # Handler Grup (Admin & Diskusi)
    application.add_handler(CallbackQueryHandler(handle_callback_review, pattern="^mf\|"))
    application.add_handler(CallbackQueryHandler(handle_del_menfess, pattern="^del_"))
    
    application.add_handler(MessageHandler(filters.ALL & filters.Chat([ADMIN_GROUP_ID, LOG_GROUP_ID]), handle_admin_reply))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    application.add_handler(MessageHandler(filters.Chat(GROUP_ID_DISKUSI), handle_discussion))

    # --- Handler Fitur Anon Chat ---
    application.add_handler(CommandHandler('setprofile', set_profile, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('search', search_anon, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('stop', stop_anon, filters.ChatType.PRIVATE))

    # Message handler untuk file, media dll (diluar conversation handler)
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.TEXT, handle_pesan))

    # Fallback untuk custom command yang disimpan lewat /addcommand.
    application.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.PRIVATE, handle_custom_command))

    logger.info("✅ Membangun bot selesai. Menjalankan polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)


if __name__ == '__main__':
    main()
