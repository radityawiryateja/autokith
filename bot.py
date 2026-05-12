# -*- coding: utf-8 -*-
import json
import logging
import re
import markdown
import os
import random
import asyncio

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes, CallbackContext
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, MessageEntity, ChatMemberUpdated, ChatMember
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
TITLE_PRICE = 500 # Harga Custom Title

WAITING_USERNAME = 1

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error(f"Gagal koneksi ke Supabase: {e}")

CACHE_HASHTAGS = []
required_channels = []
CACHE_BANNED_USERS = []
CACHE_COMSECT_OFF = set() 
CACHE_BAD_WORDS = set() 

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

# === HELPER: MANAJEMEN KOIN ===
async def add_kith_coins(user_id: int, amount: int):
    try:
        response = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
        current_balance = response.data[0].get("kith_coins") if hasattr(response, 'data') and response.data and response.data[0].get("kith_coins") is not None else 0
        new_balance = current_balance + amount
        supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", user_id).execute()
        return new_balance
    except Exception as e:
        logger.error(f"Gagal tambah koin untuk {user_id}: {e}")
        return None

# === FITUR PROFIL & LEADERBOARD ===
async def cek_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    try:
        res = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
        coins = res.data[0].get("kith_coins") if res.data else 0
        
        text = (
            f"👤 *PROFIL KAMU*\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"🏷️ Username: @{username}\n"
            f"🪙 Kith-Coins: *{coins}*\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("❌ Gagal mengambil data profil.")

# === FITUR LEADERBOARD (UPDATE) ===
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Panggil user_id aja dari database, nggak perlu username-nya
        res = supabase.table("users").select("user_id, kith_coins").order("kith_coins", desc=True).limit(10).execute()
        if not res.data:
            return await update.message.reply_text("Belum ada data pemain.")
        
        text = "🏆 *LEADERBOARD KITH-COINS* 🏆\n\n"
        
        # Looping untuk ngecek ke server Telegram satu per satu
        for i, row in enumerate(res.data):
            user_id = row.get("user_id")
            coins = row.get("kith_coins", 0)
            
            try:
                # get_chat() ini fungsinya nangkep profil terbaru langsung dari API Telegram
                chat = await context.bot.get_chat(user_id)
                
                # Cek apakah dia punya username
                if chat.username:
                    display_name = f"@{chat.username}"
                else:
                    # Kalau nggak punya username, pakai nama depannya aja
                    display_name = f"{chat.first_name}"
            except Exception:
                # Kalau gagal (misalnya akunnya udah dihapus/delete account)
                display_name = f"👤 User ID: {user_id}"
            
            text += f"{i+1}. {display_name} - *{coins}* Coins\n"
        
        text += "\nTerus aktif dan kumpulkan koin sebanyak-banyaknya!"
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
        response = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
        current_balance = response.data[0].get("kith_coins") if hasattr(response, 'data') and response.data and response.data[0].get("kith_coins") is not None else 0

        if current_balance < TITLE_PRICE:
            return await update.message.reply_text(f"❌ Kith-Coins kamu tidak cukup.\nSaldo kamu: {current_balance} Coins\nHarga Title: {TITLE_PRICE} Coins")

        new_balance = current_balance - TITLE_PRICE
        supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", user_id).execute()

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
            supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute()
            logger.error(f"Gagal set title Telegram: {telegram_err}")
            
            if "User_Not_Participant" in str(telegram_err) or "user is not a member" in str(telegram_err):
                await update.message.reply_text("❌ Gagal menerapkan title. Pastikan kamu sudah join ke grup diskusi terlebih dahulu!\n\nKoin kamu telah dikembalikan (Refund).")
            else:
                await update.message.reply_text("❌ Gagal menerapkan title di grup. Pastikan bot memiliki izin 'Manage Tags' (Kelola Peran Anggota).\n\nKoin kamu telah dikembalikan (Refund).")

    except Exception as db_err:
        logger.error(f"Error Database saat beli title: {db_err}")
        await update.message.reply_text("❌ Terjadi kesalahan pada database. Silakan coba lagi nanti.")

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

# === ALUR MENFESS ===
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

    for bw in CACHE_BAD_WORDS:
        if re.search(rf'\b{re.escape(bw)}\b', pesan_teks_lower):
            await update.message.reply_text("❌ Menfess ditolak karena mengandung kata-kata yang dilarang oleh base.")
            return ConversationHandler.END

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
                if ent.type == "mention": ada_mention = True; break
        
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
    
    target_username = update.message.text.strip().replace("@", "")
    teks_asli = context.user_data.get('teks_menfess', "")
    original_entities = context.user_data.get('entities', [])

    final_text = teks_asli + "\u200B"
    offset = len(teks_asli.encode('utf-16-le')) // 2
    
    invisible_link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=offset, length=1, url=f"https://t.me/{target_username}")
    final_entities = original_entities + [invisible_link]

    try:
        message_sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=final_text, entities=final_entities, link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True))
        CACHE_COMSECT_OFF.add(message_sent.message_id)
        new_balance = await add_kith_coins(user_id, 50)

        coin_msg = f"\n💰 *+50 Kith-Coins!* (Saldo: {new_balance})" if new_balance is not None else ""
        keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
        await update.message.reply_text(f"Pesan kamu telah dikirim ke channel! 🪶{coin_msg}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        try:
            supabase.table("menfess_map").insert({"post_id": message_sent.message_id, "sender_user_id": user_id}).execute()
        except Exception as e: logger.error(f"DB Error Auto: {e}")

        log_msg = f"📌 Log Menfess (AUTO):\n🕰️ Waktu: {update.message.date}\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`\n🔗 Username Target: @{target_username}\n💬 Pesan: {teks_asli}"
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error direct forward: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat mengirim menfess.")
            
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

                if not comsect_on: CACHE_COMSECT_OFF.add(sent_msg.message_id)

                log_msg = f"📌 Log Menfess (Manual Approved):\n🆔 Pengirim ID: `{user_id}`\n⚙️ Comsect: {'ON' if comsect_on else 'OFF'}"
                await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, parse_mode="Markdown")

                new_balance = await add_kith_coins(user_id, earned_coins)

                try:
                    supabase.table("menfess_map").insert({"post_id": sent_msg.message_id, "sender_user_id": user_id}).execute()
                except Exception as e: logger.error(f"DB Error Map: {e}")

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
    if update.effective_chat.id not in [ADMIN_GROUP_ID, LOG_GROUP_ID] or not update.message.reply_to_message: return

    match = re.search(r"ID(?:\s*Pengguna)?:?\s*[`]*(\d+)", update.message.reply_to_message.text or update.message.reply_to_message.caption or "")
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

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

async def handle_discussion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return

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
                supabase.table("menfess_map").update({"discussion_message_id": msg.message_id}).eq("post_id", post_id).execute()
            except Exception: pass
        return

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

                await context.bot.send_message(chat_id=sender_user_id, text=notif_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Lihat Balasan", url=link)]]), parse_mode="Markdown")
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
    user_id = update.effective_user.id
    try:
        response = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
        balance = response.data[0].get("kith_coins") if hasattr(response, 'data') and response.data and response.data[0].get("kith_coins") is not None else 0
    except:
        balance = 0

    menu_text = (
        "𔐼 *Kitheons:* [@kitheons](https://t.me/kitheons)\n"
        "𔐼 *Ch Arsip:* [@kithives](https://t.me/kithives)\n\n"
        f"🪙 *Kith-Coins Kamu:* {balance}\n\n"
        "Gunakan `/buytitle <nama>` untuk beli Custom Title seharga 500 Koin!"
    )
    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📜 Info Kitheons", url="https://t.me/kithives")]]))

async def broadcast_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcastfw <link>")
    link = context.args[0]
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", link)
    if not match: return await update.message.reply_text("❌ Link tidak valid! Pastikan formatnya t.me/username_channel/angka")
    channel_username, message_id = match.groups()
    if channel_username == "c": return await update.message.reply_text("❌ Tidak bisa forward menggunakan link dari channel private!")
    user_list = await get_all_user_ids()
    sc, fc = 0, 0
    for user_id in user_list:
        try:
            await context.bot.forward_message(chat_id=user_id, from_chat_id=f"@{channel_username}", message_id=int(message_id))
            sc += 1
        except Exception as e: fc += 1
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

# === FITUR TAMBAH KATA UNDERCOVER (ADMIN) ===
async def add_uc_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    text = " ".join(context.args)
    if "-" not in text: return await update.message.reply_text("❌ Format salah! Gunakan: `/adducword Nasi Goreng - Mie Goreng`", parse_mode="Markdown")
    words = text.split("-")
    w1, w2 = words[0].strip(), words[1].strip()
    try:
        supabase.table("uc_words").insert({"word1": w1, "word2": w2}).execute()
        await update.message.reply_text(f"✅ Berhasil menambahkan kata: *{w1}* vs *{w2}*", parse_mode="Markdown")
    except Exception as e: await update.message.reply_text(f"❌ Gagal masuk database: {e}")

# === INPUT KATA PEMAIN (COMMAND /vote) ===
async def submit_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI: return
    if not context.args: 
        return await update.message.reply_text("Format salah! Ketik: `/vote [kata/kalimat deskripsi]`", parse_mode="Markdown")
    
    user_id = str(update.effective_user.id)
    desc_word = " ".join(context.args)
    
    res = supabase.table("uc_active_games").select("*").eq("status", "playing").execute()
    if not res.data: return
    
    game = next((g for g in res.data if user_id in g['players']), None)
    if not game: return
    
    players = game['players']
    
    # Menyimpan kata yang disubmit ke JSON players
    players[user_id]['current_word'] = desc_word
    supabase.table("uc_active_games").update({"players": players}).eq("game_id", game['game_id']).execute()
    
    await update.message.reply_text(f"✅ Deskripsi diterima dari {update.effective_user.first_name}!", reply_to_message_id=update.message.message_id)

# === LOBBY & CALLBACK GAME ===
async def start_undercover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI: return await update.message.reply_text("🎮 Game ini hanya bisa dimainkan di dalam Grup Diskusi!")

    creator_id = update.effective_user.id
    creator_name = update.effective_user.first_name
    creator_username = update.effective_user.username or str(creator_id)

    keyboard = [[InlineKeyboardButton("🎮 Gabung Game", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai Game", callback_data="uc_start")]]
    msg = await update.message.reply_text(
        f"🕵️‍♂️ *GAME UNDERCOVER*\n\n👑 Room Master: {creator_name}\n\n👥 *Pemain Terdaftar:*\n1. {creator_name} (@{creator_username})\n\n*(Minimal 3 pemain)*",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

    # Tambahkan field 'current_word' kosong agar siap diisi pemain
    players_data = {str(creator_id): {"name": creator_name, "username": creator_username, "current_word": ""}}
    try:
        supabase.table("uc_active_games").insert({
            "game_id": msg.message_id, "chat_id": update.effective_chat.id, "status": "lobby",
            "creator_id": creator_id, "players": players_data, "undercover_id": 0, 
            "civilian_word": "", "undercover_word": "", "votes": {}
        }).execute()
    except Exception as e: logger.error(f"DB Error: {e}")

async def handle_uc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query.data.startswith("uc_"): return
    await query.answer()

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    username = update.effective_user.username or str(user_id)
    game_id = query.message.message_id

    res = supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute()
    if not hasattr(res, 'data') or not res.data: return await query.edit_message_text("❌ Game ini sudah selesai atau dibatalkan.")
    
    game = res.data[0]
    players = game['players']

    # === LOGIKA UBAH LINK JADI PUBLIC ===
    thread_id = query.message.message_thread_id
    group_link = f"https://t.me/c/{str(GROUP_ID_DISKUSI).replace('-100', '')}/{game_id}" # Fallback Private
    
    if thread_id:
        try:
            # Cari post_id channel dari tabel menfess_map
            map_res = supabase.table("menfess_map").select("post_id").eq("discussion_message_id", thread_id).execute()
            if hasattr(map_res, 'data') and map_res.data:
                post_id = map_res.data[0]['post_id']
                channel_username = CHANNEL_ID.replace('@', '')
                group_link = f"https://t.me/{channel_username}/{post_id}?comment={game_id}"
        except Exception as e:
            pass # Kalau error, bakal tetep pake Fallback Private
            
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Kembali ke Grup", url=group_link)]])

    if query.data == "uc_join":
        if game['status'] != 'lobby': 
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text=f"⚠️ {user_name}, game sudah dimulai!", reply_to_message_id=game_id)
        if str(user_id) in players: return 

        players[str(user_id)] = {"name": user_name, "username": username, "current_word": ""}
        supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute()
        
        player_list = "\n".join([f"{i+1}. {p['name']} (@{p['username']})" for i, p in enumerate(players.values())])
        keyboard = [[InlineKeyboardButton("🎮 Gabung", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai", callback_data="uc_start")]]
        await query.edit_message_text(f"🕵️‍♂️ *GAME UNDERCOVER*\n\n👥 *Pemain Terdaftar:*\n{player_list}\n\n*(Minimal 3 pemain)*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "uc_start":
        if user_id != game['creator_id']: 
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text=f"⚠️ Hanya Room Master yang bisa memulai game ini!", reply_to_message_id=game_id)
            
        if len(players) < 3: 
            return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text="⚠️ Minimal butuh 3 orang untuk mulai!", reply_to_message_id=game_id)

        words_res = supabase.table("uc_words").select("*").execute()
        if not words_res.data: return await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text="❌ Kata di database kosong!")
        word_pair = random.choice(words_res.data)
        
        player_ids = list(players.keys())
        random.shuffle(player_ids)
        undercover_id = player_ids[0]
        
        supabase.table("uc_active_games").update({
            "status": "playing", "undercover_id": int(undercover_id), 
            "civilian_word": word_pair['word1'], "undercover_word": word_pair['word2']
        }).eq("game_id", game_id).execute()

        for pid in player_ids:
            role = "Undercover" if pid == undercover_id else "Civilian"
            kata = word_pair['word2'] if pid == undercover_id else word_pair['word1']
            try:
                await context.bot.send_message(chat_id=int(pid), text=f"🕵️‍♂️ *Peranmu:* `{role}`\n🤫 *Katamu:* *{kata}*", reply_markup=btn_grup, parse_mode="Markdown")
            except Exception: pass

        urutan = "\n".join([f"{i+1}. {players[pid]['name']} (@{players[pid]['username']})" for i, pid in enumerate(player_ids)])
        await query.edit_message_text(
            f"🎯 *GAME DIMULAI!*\nCek DM bot untuk kata rahasia!\n\n🔄 *Urutan Bermain:*\n{urutan}\n\n"
            f"❗️ *TUGASMU:* Ketik `/vote [katamu]` di grup ini secara bergiliran!\n\n"
            f"⏳ *Waktu: 5 Menit (5 Ronde @1 menit)*", parse_mode="Markdown"
        )
        
        # Lempar group_link ke dalam Timer
        asyncio.create_task(run_game_timer(GROUP_ID_DISKUSI, game_id, group_link, context))

# === LOOP TIMER & NOTIF DM ===
async def run_game_timer(chat_id, game_id, group_link, context):
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Ke TKP Diskusi", url=group_link)]])
    
    for i in range(1, 6):
        await asyncio.sleep(60)
        res = supabase.table("uc_active_games").select("status, players").eq("game_id", game_id).execute()
        if not res.data: return 
        game = res.data[0]
        if game['status'] != 'playing': return
        
        players = game['players']
        
        # Buat Rekap ronde berjalan
        recap = f"⏱️ *Ronde {i} Selesai!*\n\n*Rekap Kata Pemain:*\n"
        for pid, pdata in players.items():
            word = pdata.get('current_word', "")
            display_word = f"*{word}*" if word else "(Tidak menyebutkan kata)"
            recap += f"- {pdata['name']}: {display_word}\n"
            
            # Reset word untuk siap-siap ronde berikutnya
            players[pid]['current_word'] = ""
            
        supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute()
        
        if i < 5: 
            pesan_ronde = f"{recap}\n🔔 Masuk *Ronde {i+1}*! Silakan diskus dan ketik `/vote [katamu]` lagi!"
            await context.bot.send_message(chat_id, pesan_ronde, reply_to_message_id=game_id, parse_mode="Markdown")
            
            # BC Notif ke DM pakai tombol public link
            for pid in players.keys():
                try: await context.bot.send_message(int(pid), f"🔔 {pesan_ronde}", reply_markup=btn_grup, parse_mode="Markdown")
                except: pass
    
    # Fase Voting
    supabase.table("uc_active_games").update({"status": "voting"}).eq("game_id", game_id).execute()
    pesan_vote = "🚨 *WAKTU HABIS!*\n\nSesi VOTE dimulai selama 90 Detik.\nKetik: `/sus @username` di komentar ini untuk menuduh Undercover!"
    await context.bot.send_message(chat_id, pesan_vote, reply_to_message_id=game_id, parse_mode="Markdown")
    
    for pid in players.keys():
        try: await context.bot.send_message(int(pid), f"🔔 {pesan_vote}", reply_markup=btn_grup, parse_mode="Markdown")
        except: pass
        
    await asyncio.sleep(90)
    # Lempar link-nya lagi ke fungsi hasil akhir
    await tally_votes(chat_id, game_id, group_link, context)

# === VOTE COMMAND ===
async def sus_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID_DISKUSI: return
    if not context.args: return await update.message.reply_text("Format: `/sus @username`", parse_mode="Markdown")
    
    voter_id = str(update.effective_user.id)
    target_username = context.args[0].replace("@", "").lower()
    
    res = supabase.table("uc_active_games").select("*").eq("status", "voting").execute()
    if not res.data: return await update.message.reply_text("❌ Tidak ada sesi voting yang aktif untukmu.")
    
    game = res.data[0]
    players = game['players']
    
    if voter_id not in players: return await update.message.reply_text("❌ Kamu tidak bermain di game ini.")
    
    target_id = None
    for pid, pdata in players.items():
        if pdata['username'].lower() == target_username:
            target_id = pid
            break
            
    if not target_id: return await update.message.reply_text(f"❌ Pemain @{target_username} tidak ditemukan di game ini.")
    
    votes = game['votes']
    votes[voter_id] = target_id
    supabase.table("uc_active_games").update({"votes": votes}).eq("game_id", game['game_id']).execute()
    
    await update.message.reply_text(f"✅ {update.effective_user.first_name} menuduh @{target_username}!", reply_to_message_id=update.message.message_id)

# === TALLY & REWARDS ===
async def tally_votes(chat_id, game_id, group_link, context):
    res = supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute()
    if not res.data: return
    game = res.data[0]
    if game['status'] != "voting": return
    
    votes = game['votes']
    players = game['players']
    under_id = str(game['undercover_id'])
    
    vote_counts = {}
    for target in votes.values(): vote_counts[target] = vote_counts.get(target, 0) + 1
    
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
        
    # Kirim ke Diskusi dengan reply
    await context.bot.send_message(chat_id, hasil_text, reply_to_message_id=game_id, parse_mode="Markdown")
    
    # Pakai public link untuk tombol hasil diskusi
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Lihat Hasil Diskusi", url=group_link)]])
    
    # Distribusi Koin dan Kirim Notif Game Over ke DM
    for pid in players.keys():
        if is_undercover_caught:
            koin = 100 if pid == under_id else 200
        else:
            koin = 200 if pid == under_id else 100
            
        await add_kith_coins(int(pid), koin)
        
        try:
            await context.bot.send_message(int(pid), f"🏁 *GAME OVER!*\n\n{hasil_text}", reply_markup=btn_grup, parse_mode="Markdown")
        except: pass
        
    supabase.table("uc_active_games").delete().eq("game_id", game_id).execute()

# === REVEAL ROLE (PREMIUM) ===
async def reveal_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    res = supabase.table("uc_active_games").select("*").eq("status", "playing").execute()
    if not res.data: return await update.message.reply_text("❌ Tidak ada game yang sedang berjalan.")
    
    game = next((g for g in res.data if user_id in g['players']), None)
    if not game: return await update.message.reply_text("❌ Kamu tidak bermain di game aktif manapun.")
    
    koin_res = supabase.table("users").select("kith_coins").eq("user_id", int(user_id)).execute()
    saldo = koin_res.data[0]['kith_coins'] if koin_res.data else 0
    if saldo < 500: return await update.message.reply_text(f"❌ Koin tidak cukup. Butuh 500 Coins (Saldomu: {saldo}).")
    
    supabase.table("users").update({"kith_coins": saldo - 500}).eq("user_id", int(user_id)).execute()
    
    undercover_name = game['players'][str(game['undercover_id'])]['name']
    civ_word = game['civilian_word']
    und_word = game['undercover_word']
    
    await update.message.reply_text(
        f"🔮 *REVEAL ROLE (Premium)* 🔮\n\n"
        f"🤫 Undercover: *{undercover_name}*\n"
        f"📝 Kata Civilian: *{civ_word}*\n"
        f"📝 Kata Undercover: *{und_word}*\n\n"
        f"*(Ssstt.. Saldo Koinmu dipotong 500)*", parse_mode="Markdown"
    )

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

async def refresh_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID: 
        return
    
    await update.message.reply_text("⏳ Memulai kalkulasi Kith-Coins retroaktif... Mohon tunggu, proses ini butuh waktu.")
    try:
        response = supabase.table("menfess_map").select("sender_user_id").execute()
        if not hasattr(response, 'data') or not response.data:
            return await update.message.reply_text("❌ Data menfess map masih kosong.")
            
        user_counts = {}
        for row in response.data:
            uid = row.get("sender_user_id")
            if uid:
                user_counts[uid] = user_counts.get(uid, 0) + 1
                
        berhasil, gagal = 0, 0
        for uid, count in user_counts.items():
            reward_coins = count * 50
            try:
                user_res = supabase.table("users").select("kith_coins").eq("user_id", uid).execute()
                current_coins = user_res.data[0].get("kith_coins") if hasattr(user_res, 'data') and user_res.data and user_res.data[0].get("kith_coins") is not None else 0
                new_balance = current_coins + reward_coins
                supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", uid).execute()
                
                notif_text = (
                    f"🎉 *Kejutan Kith-Coins Retroaktif!*\n\n"
                    f"Terima kasih atas loyalitas kamu! Karena kamu sudah pernah mengirim *{count} menfess* di Kitheons sebelumnya, "
                    f"kamu berhak mendapatkan kompensasi sebesar *{reward_coins} Kith-Coins*!\n\n"
                    f"🪙 Saldo Koin kamu sekarang: *{new_balance}*\n\n"
                    f"Koin ini bisa kamu tukarkan ke berbagai fitur mendatang seperti *Custom Title Loyalty* dan lain-lain. Pantengin terus update dari admin ya!"
                )
                await context.bot.send_message(chat_id=uid, text=notif_text, parse_mode="Markdown")
                berhasil += 1
            except Exception as e:
                logger.error(f"Gagal refresh coin untuk user {uid}: {e}")
                gagal += 1
            await asyncio.sleep(0.1) 
            
        await update.message.reply_text(f"✅ *Refresh Coin Selesai!*\n\n👤 User berhasil diproses: {berhasil}\n❌ Gagal kirim: {gagal}")
    except Exception as e:
        logger.error(f"Error refresh coin: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat memproses data database.")

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
    application.add_handler(CommandHandler('refreshcoin', refresh_coin))

    # Fitur Profil & Leaderboard
    application.add_handler(CommandHandler('profile', cek_profile))
    application.add_handler(CommandHandler(['leaderboard', 'leadboard'], leaderboard)) # Sengaja support typo kamu hahaha

    # Fitur Game
    application.add_handler(CommandHandler('adducword', add_uc_word))
    application.add_handler(CommandHandler('undercover', start_undercover))
    application.add_handler(CommandHandler('vote', submit_word)) # Pemain submit kata dengan /vote
    application.add_handler(CommandHandler('sus', sus_vote))     # Pemain menuduh target dengan /sus
    application.add_handler(CommandHandler('revealrole', reveal_role))
    
    # Tangkap klik Gabung / Mulai game
    application.add_handler(CallbackQueryHandler(handle_uc_callback, pattern="^uc_"))
    
    # Fitur Roleplay
    application.add_handler(CommandHandler('buytitle', buy_title))
    
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
