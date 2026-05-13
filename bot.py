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

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, CallbackContext
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

# === KONFIGURASI LIVE PHOTO TELEGRAM NATIVE ===
LIVE_MAX_DURATION = min(float(os.environ.get("LIVE_MAX_DURATION", "9.8")), 9.8)
LIVE_MAX_INPUT_FILE_SIZE_MB = int(os.environ.get("LIVE_MAX_INPUT_FILE_SIZE_MB", "50"))
LIVE_MAX_OUTPUT_FILE_SIZE_MB = min(int(os.environ.get("LIVE_MAX_OUTPUT_FILE_SIZE_MB", "10")), 10)
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

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

async def check_system_tools():
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
        response = supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute()
        row = response.data[0] if hasattr(response, 'data') and response.data else {}

        current_balance = row.get("kith_coins") if row.get("kith_coins") is not None else 0
        current_total = row.get("total_kith_coins") if row.get("total_kith_coins") is not None else current_balance

        new_balance = current_balance + amount
        new_total = current_total + amount

        supabase.table("users").update({
            "kith_coins": new_balance,
            "total_kith_coins": new_total
        }).eq("user_id", user_id).execute()

        return new_balance
    except Exception as e:
        logger.error(f"Gagal tambah koin untuk {user_id}: {e}")
        return None

# === FITUR TAMPILKAN MENU ===
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private": return
    
    keyboard = [
        [InlineKeyboardButton("💌 Kirim Menfess Auto", callback_data="btn_menfess")],
        [InlineKeyboardButton("📸 Buat Photo Live", callback_data="btn_photolive"), InlineKeyboardButton("👤 Check Profile", callback_data="btn_profile")],
        [InlineKeyboardButton("🏷️ Beli Title", callback_data="btn_buytitle")]
    ]
    
    await update.message.reply_text(
        "👋 *Navigasi Bot*\n\nPilih informasi atau fitur yang ingin kamu akses dari tombol di bawah:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# --- HANDLER UNTUK MENGELOLA KLIK TOMBOL MENU ---
async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "btn_menfess":
        await query.message.reply_text("💌 *Cara Send Menfess Auto*\n\nKamu cukup mengirimkan teks secara langsung di ruang chat (DM) ini! Menfess akan otomatis dikirim tanpa kamu perlu mengetik username-mu lagi karena sistem telah menangkapnya secara otomatis.")
    
    elif query.data == "btn_photolive":
        await query.message.reply_text("📸 *Cara Buat Photo Live*\n\n1. Kirim video ke chat ini (maksimal 10 detik dan ukuran < 10MB)\n2. Tambahkan caption `/live` sebelum dikirim.\n\nAtau jika video sudah terkirim, kamu bisa *reply/balas* pesan video tersebut dengan command `/live`.")
        
    elif query.data == "btn_profile":
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        try:
            res = supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute()
            row = res.data[0] if res.data else {}
            coins = row.get("kith_coins", 0)
            total_coins = row.get("total_kith_coins", coins)
            
            text = f"👤 *PROFIL KAMU*\n\n🆔 ID: `{user_id}`\n🏷️ Username: @{username}\n🪙 Saldo Kith-Coins: *{coins}*\n🏆 Total Koin Diperoleh: *{total_coins}*"
            await query.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text("❌ Gagal mengambil data profil dari database.")
            
    elif query.data == "btn_buytitle":
        await query.message.reply_text(f"🛒 *Beli Custom Title*\n\nHarga per title adalah **{TITLE_PRICE} Kith-Coins**.\nKetikkan command berikut di chat ini:\n\n`/buytitle <Nama Title Barumu>`\nContoh: `/buytitle Kith Pro`", parse_mode="Markdown")

# === FITUR LEADERBOARD ===
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        res = supabase.table("users").select("user_id, kith_coins, total_kith_coins").order("total_kith_coins", desc=True).limit(10).execute()
        if not res.data:
            return await update.message.reply_text("Belum ada data pemain.")
        
        text = "🏆 *LEADERBOARD TOTAL KITH-COINS* 🏆\n\n"
        for i, row in enumerate(res.data):
            user_id = row.get("user_id")
            coins = row.get("total_kith_coins") if row.get("total_kith_coins") is not None else row.get("kith_coins", 0)
            
            try:
                chat = await context.bot.get_chat(user_id)
                display_name = f"@{chat.username}" if chat.username else f"{chat.first_name}"
            except Exception:
                display_name = f"👤 User ID: {user_id}"
            text += f"{i+1}. {display_name} - *{coins}* Coins\n"
        
        text += "\nLeaderboard dihitung dari total koin yang pernah diperoleh, bukan saldo saat ini."
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
        return await update.message.reply_text(f"⚠️ Format salah!\nGunakan: `/buytitle <nama_title>`\nContoh: `/buytitle Kith Pro`\n\n*Harga: {TITLE_PRICE} Kith-Coins*", parse_mode="Markdown")
    
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
            await context.bot.set_chat_member_tag(chat_id=GROUP_ID_DISKUSI, user_id=user_id, tag=new_title)
            await update.message.reply_text(
                f"✅ Transaksi Berhasil!\n\n🏷️ Title barumu: `{new_title}`\n🪙 Sisa saldo Kith-Coins: {new_balance}\n\nSilakan kirim pesan di grup diskusi untuk melihat title barumu!",
                parse_mode="Markdown"
            )

        except Exception as telegram_err:
            supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute()
            logger.error(f"Gagal set title Telegram: {telegram_err}")
            
            if "User_Not_Participant" in str(telegram_err) or "user is not a member" in str(telegram_err):
                keyboard = [[InlineKeyboardButton("Masuk Comsect", url="https://t.me/kitheons")]]
                await update.message.reply_text(
                    "❌ Gagal menerapkan title. Transaksi dibatalkan!\n\n"
                    "Penyebab: Kamu belum bergabung di *Grup Comsect*. Silakan join via tombol di bawah terlebih dahulu, lalu ulangi pembelian. Koin kamu telah di-refund utuh.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("❌ Gagal menerapkan title di grup. Pastikan bot memiliki izin 'Manage Tags' (Kelola Peran Anggota).\n\nKoin kamu telah dikembalikan (Refund).")

    except Exception as db_err:
        logger.error(f"Error Database saat beli title: {db_err}")
        await update.message.reply_text("❌ Terjadi kesalahan pada database. Silakan coba lagi nanti.")

# === SISTEM LIVE PHOTO ===
def _get_video_file_from_message(msg):
    if msg.video: return msg.video
    if msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"): return msg.document
    if msg.animation: return msg.animation
    if msg.reply_to_message:
        reply_msg = msg.reply_to_message
        if reply_msg.video: return reply_msg.video
        if reply_msg.document and reply_msg.document.mime_type and reply_msg.document.mime_type.startswith("video/"): return reply_msg.document
        if reply_msg.animation: return reply_msg.animation
    return None

async def _run_cmd(cmd, timeout=120):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0: raise RuntimeError(f"Command failed:\n{stderr.decode('utf-8', errors='ignore')}")
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command timed out after {timeout} seconds.")
    return stdout, stderr

async def _send_live_photo_direct(bot_token, chat_id, video_path, photo_path, message_thread_id=None, reply_to_message_id=None):
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendLivePhoto"
    data = {"chat_id": chat_id}
    if message_thread_id: data["message_thread_id"] = message_thread_id
    if reply_to_message_id: data["reply_to_message_id"] = reply_to_message_id
    
    with open(video_path, "rb") as vf, open(photo_path, "rb") as pf:
        files = {"live_photo": vf, "photo": pf}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, data=data, files=files)
            
    payload = resp.json()
    if not payload.get("ok"):
        error_code = payload.get("error_code")
        description = payload.get("description")
        raise RuntimeError(f"Telegram sendLivePhoto gagal ({error_code}): {description}")
    return payload.get("result")

async def live_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("❌ Akses ditolak. Silakan join channel terlebih dahulu untuk menggunakan fitur ini!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return

    msg = update.message
    video = _get_video_file_from_message(msg)
    if not video:
        await msg.reply_text("Silakan kirim video dengan caption /live, atau reply/balas video dengan /live.")
        return

    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        await msg.reply_text("❌ FFmpeg belum kebaca di server.\nPastikan Aptfile berisi `ffmpeg`, lalu deploy ulang.", parse_mode="Markdown")
        return

    file_size = getattr(video, "file_size", 0) or 0
    max_input_bytes = LIVE_MAX_INPUT_FILE_SIZE_MB * 1024 * 1024
    if file_size and file_size > max_input_bytes:
        await msg.reply_text(f"❌ Videonya terlalu besar untuk diproses di Heroku. Maksimal input {LIVE_MAX_INPUT_FILE_SIZE_MB} MB.")
        return

    status_msg = await msg.reply_text("⏳ Memproses Live Photo native Telegram...\nTahap 1/4: download video")
    asset_id = str(uuid.uuid4()).upper()
    input_path = f"input_{asset_id}.mp4"
    output_live_photo = f"live_photo_{asset_id}.mp4"
    output_photo = f"photo_{asset_id}.jpg"
    
    try:
        telegram_file = await video.get_file()
        await telegram_file.download_to_drive(input_path)
        await status_msg.edit_text("⏳ Tahap 2/4: convert ke format Live Photo")

        attempts = [
            {"width": 720, "video_bitrate": "3500k", "maxrate": "4200k", "bufsize": "8400k", "audio_bitrate": "96k"},
            {"width": 540, "video_bitrate": "2200k", "maxrate": "2600k", "bufsize": "5200k", "audio_bitrate": "80k"},
            {"width": 480, "video_bitrate": "1400k", "maxrate": "1700k", "bufsize": "3400k", "audio_bitrate": "64k"},
        ]
        
        last_detail = ""
        for attempt in attempts:
            try:
                if os.path.exists(output_live_photo): os.remove(output_live_photo)
                vf = f"scale={attempt['width']}:-2:force_original_aspect_ratio=decrease,setsar=1,fps=30"
                await _run_cmd([
                    ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-i", input_path,
                    "-t", str(LIVE_MAX_DURATION), "-map", "0:v:0", "-map", "0:a?", "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-b:v", attempt["video_bitrate"],
                    "-maxrate", attempt["maxrate"], "-bufsize", attempt["bufsize"],
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", attempt["audio_bitrate"],
                    "-movflags", "+faststart", "-y", output_live_photo
                ], timeout=240)
                
                if not os.path.exists(output_live_photo) or os.path.getsize(output_live_photo) == 0:
                    raise RuntimeError("File output FFmpeg kosong atau tidak dibuat.")
                
                out_size_mb = os.path.getsize(output_live_photo) / (1024 * 1024)
                if out_size_mb <= LIVE_MAX_OUTPUT_FILE_SIZE_MB: break
            except Exception as e:
                last_detail = str(e)
                logger.warning(f"Attempt convert ke {attempt['width']} gagal: {e}")
                
        await status_msg.edit_text("⏳ Tahap 3/4: extract static photo")
        await _run_cmd([
            ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-i", output_live_photo,
            "-vframes", "1", "-q:v", "2", "-y", output_photo
        ])

        await status_msg.edit_text("⏳ Tahap 4/4: upload Live Photo native ke Telegram")
        
        # Kirim live photo via HTTP Direct Telegram API
        sent_result = await _send_live_photo_direct(
            BOT_TOKEN,
            chat_id=msg.chat_id,
            video_path=output_live_photo,
            photo_path=output_photo,
            message_thread_id=msg.message_thread_id,
            reply_to_message_id=msg.message_id
        )
        
        await status_msg.delete()
        
    except Exception as e:
        logger.exception("Gagal live photo via API")
        await status_msg.edit_text(f"❌ Gagal memproses Live Photo:\n{e}")
    finally:
        for p in [input_path, output_live_photo, output_photo]:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass


# === FITUR ADMIN (Banned Users, Mode Menfess, dll) ===
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
            "*(Semua pesan teks yang kamu kirim otomatis diajukan sebagai menfess)*", parse_mode="Markdown"
        )
    else:
        keyboard = [[InlineKeyboardButton("Join Channels", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

# === ALUR MENFESS (REVISI AUTO) ===
async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE
    if update.effective_chat.type != "private": return
    if not bot_active: 
        await update.message.reply_text("Bot sedang dipause oleh admin.")
        return

    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    display_name = f"@{username}" if username else first_name

    if user_id in CACHE_BANNED_USERS:
        await update.message.reply_text("❌ Pesan ditolak. Akses kamu ke bot ini telah diblokir.")
        return

    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return

    pesan_teks = update.message.text or update.message.caption or ""
    pesan_teks_lower = pesan_teks.lower()

    for bw in CACHE_BAD_WORDS:
        if re.search(rf'\b{re.escape(bw)}\b', pesan_teks_lower):
            await update.message.reply_text("❌ Menfess ditolak karena mengandung kata-kata yang dilarang oleh base.")
            return

    if MENFESS_MODE == "auto":
        if not update.message.text:
            await update.message.reply_text("❌ Sesi /auto sedang aktif! Kamu hanya diperbolehkan mengirim pesan teks saja (tanpa media).")
            return

        if len(update.message.text) > 70:
            await update.message.reply_text(f"❌ Menfess terlalu panjang! Maksimal 70 karakter ya. (Pesanmu saat ini: {len(update.message.text)} karakter).")
            return

        ada_mention = False
        if update.message.entities:
            for ent in update.message.entities:
                if ent.type == "mention": ada_mention = True; break
        
        if ada_mention or re.search(r'(?:^|\s)@/?\w+', pesan_teks):
            await update.message.reply_text("❌ Menfess dilarang menyertakan mention atau username! (Link URL tetap diperbolehkan).")
            return

        # --- LANGSUNG TANGKAP USERNAME & KIRIM KE CHANNEL ---
        teks_asli = update.message.text
        target_username = update.effective_user.username or update.effective_user.first_name
        original_entities = update.message.entities or []
        
        # Buat link sembunyi (invisible link) di spasi nol selebar (Zero-width space)
        final_text = teks_asli + "\u200B"
        offset = len(teks_asli.encode('utf-16-le')) // 2
        invisible_link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=offset, length=1, url=f"https://t.me/{target_username}")
        final_entities = list(original_entities) + [invisible_link]

        try:
            message_sent = await context.bot.send_message(
                chat_id=CHANNEL_ID, 
                text=final_text, 
                entities=final_entities, 
                link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True)
            )
            CACHE_COMSECT_OFF.add(message_sent.message_id)
            new_balance = await add_kith_coins(user_id, 50)
            coin_msg = f"\n💰 *+50 Kith-Coins!* (Saldo: {new_balance})" if new_balance is not None else ""
            
            keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
            await update.message.reply_text(f"✅ Pesan kamu telah dikirim ke channel! 🪶{coin_msg}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            
            try:
                supabase.table("menfess_map").insert({"post_id": message_sent.message_id, "sender_user_id": user_id}).execute()
            except Exception: pass
            
        except Exception as e:
            logger.error(f"Gagal kirim auto menfess: {e}")
            await update.message.reply_text("❌ Gagal mengirim pesan ke channel.")

        return

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


# === FITUR GAME: UNDERCOVER ===
async def start_undercover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("❌ Akses ditolak. Silakan join channel terlebih dahulu untuk menggunakan fitur ini!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return

    if update.effective_chat.id != GROUP_ID_DISKUSI:
        return await update.message.reply_text("❌ Fitur Undercover hanya bisa dimainkan di grup diskusi!")
    
    user_name = update.effective_user.first_name
    username = update.effective_user.username or user_name
    game_id = str(update.message.message_id)

    res = supabase.table("uc_active_games").select("*").eq("group_id", GROUP_ID_DISKUSI).execute()
    if res.data:
        return await update.message.reply_text("⚠️ Masih ada game yang sedang berjalan atau menunggu di grup ini!")

    players = {str(user_id): {"name": user_name, "username": username, "current_word": ""}}
    supabase.table("uc_active_games").insert({
        "game_id": game_id,
        "group_id": GROUP_ID_DISKUSI,
        "creator_id": user_id,
        "status": "lobby",
        "players": players,
        "round_number": 0,
        "votes": {},
        "target_word_civil": "",
        "target_word_under": "",
        "undercover_id": ""
    }).execute()

    keyboard = [[InlineKeyboardButton("🎮 Gabung", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai", callback_data="uc_start")]]
    await update.message.reply_text(f"🕵️‍♂️ *GAME UNDERCOVER*\n\nRoom Master: {user_name}\n\n👥 *Pemain Terdaftar:*\n1. {user_name} (@{username})\n\n*(Minimal 3 pemain)*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_uc_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_name = query.from_user.first_name
    username = query.from_user.username or user_name
    game_id = str(query.message.reply_to_message.message_id if query.message.reply_to_message else query.message.message_id)

    res = supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute()
    if not res.data: return
    game = res.data[0]
    players = game['players']

    if query.data == "uc_join":
        if game['status'] != 'lobby': return
        if str(user_id) in players: return
        
        players[str(user_id)] = {"name": user_name, "username": username, "current_word": ""}
        supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute()
        
        player_list = "\n".join([f"{i+1}. {p['name']} (@{p['username']})" for i, p in enumerate(players.values())])
        keyboard = [[InlineKeyboardButton("🎮 Gabung", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai", callback_data="uc_start")]]
        await query.edit_message_text(f"🕵️‍♂️ *GAME UNDERCOVER*\n\n👥 *Pemain Terdaftar:*\n{player_list}\n\n*(Minimal 3 pemain)*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif query.data == "uc_start":
        if user_id != game['creator_id']: return
        if len(players) < 3: return
        
        words_res = supabase.table("uc_words").select("*").execute()
        if not words_res.data: return
        word_pair = random.choice(words_res.data)
        word1, word2 = word_pair["word1"], word_pair["word2"]
        
        if random.choice([True, False]): civil_word, under_word = word1, word2
        else: civil_word, under_word = word2, word1

        undercover_id = random.choice(list(players.keys()))

        supabase.table("uc_active_games").update({
            "status": "playing",
            "target_word_civil": civil_word,
            "target_word_under": under_word,
            "undercover_id": undercover_id
        }).eq("game_id", game_id).execute()

        await query.edit_message_text(f"🚀 *GAME DIMULAI!*\nCek Private Message (DM) bot untuk melihat kata milikmu.\nSilakan diskusikan dan kirim kata pertamamu dengan command `/vote [kata deskripsi]` di grup ini.", parse_mode="Markdown")
        for pid, p in players.items():
            kata = under_word if pid == undercover_id else civil_word
            try: await context.bot.send_message(int(pid), f"🕵️‍♂️ *Game Dimulai*\n\nKata kamu: *{kata}*", parse_mode="Markdown")
            except: pass


async def submit_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("❌ Akses ditolak. Silakan join channel terlebih dahulu untuk menggunakan fitur ini!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return

    if update.effective_chat.id != GROUP_ID_DISKUSI: return
    if not context.args: return await update.message.reply_text("Format salah! Ketik: `/vote [kata/kalimat deskripsi]`", parse_mode="Markdown")
    
    user_id_str = str(user_id)
    desc_word = " ".join(context.args)

    res = supabase.table("uc_active_games").select("*").eq("status", "playing").execute()
    if not res.data: return
    game = next((g for g in res.data if user_id_str in g['players']), None)
    if not game: return

    players = game['players']
    players[user_id_str]['current_word'] = desc_word
    supabase.table("uc_active_games").update({"players": players}).eq("game_id", game['game_id']).execute()
    await update.message.reply_text(f"✅ Deskripsi diterima dari {update.effective_user.first_name}!", reply_to_message_id=update.message.message_id)

async def sus_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("❌ Akses ditolak. Silakan join channel terlebih dahulu untuk menggunakan fitur ini!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return

    if update.effective_chat.id != GROUP_ID_DISKUSI: return
    
    user_id_str = str(user_id)
    res = supabase.table("uc_active_games").select("*").eq("status", "voting").execute()
    if not res.data: return
    game = next((g for g in res.data if user_id_str in g['players']), None)
    if not game: return

    if not update.message.entities: return
    mentioned_username = None
    for ent in update.message.entities:
        if ent.type == "mention":
            mentioned_username = update.message.text[ent.offset+1:ent.offset+ent.length]
            break

    if not mentioned_username: return await update.message.reply_text("⚠️ Harus mention/tag user yang ingin divote! (Contoh: `/sus @jake`)", parse_mode="Markdown")

    players = game['players']
    target_id = next((pid for pid, p in players.items() if p.get('username') == mentioned_username), None)

    if not target_id: return await update.message.reply_text("⚠️ User tidak ditemukan dalam game aktif ini!")

    votes = game['votes']
    if user_id_str in votes: return await update.message.reply_text("⚠️ Kamu sudah vote!")
    
    votes[user_id_str] = target_id
    supabase.table("uc_active_games").update({"votes": votes}).eq("game_id", game['game_id']).execute()
    await update.message.reply_text(f"✅ {update.effective_user.first_name} telah menuduh @{mentioned_username}!", reply_to_message_id=update.message.message_id)


async def reveal_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("❌ Akses ditolak. Silakan join channel terlebih dahulu untuk menggunakan fitur ini!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
        return

    user_id_str = str(user_id)
    res = supabase.table("uc_active_games").select("*").eq("status", "playing").execute()
    if not res.data: return await update.message.reply_text("❌ Tidak ada game yang sedang berjalan.")
    
    game = next((g for g in res.data if user_id_str in g['players']), None)
    if not game: return await update.message.reply_text("❌ Kamu tidak bermain di game aktif manapun.")

    koin_res = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
    if not koin_res.data or koin_res.data[0]['kith_coins'] < 100:
        return await update.message.reply_text("❌ Koin kamu kurang. Butuh 100 Kith-Coins untuk menggunakan fitur ini.")

    supabase.table("users").update({"kith_coins": koin_res.data[0]['kith_coins'] - 100}).eq("user_id", user_id).execute()

    role = "UNDERCOVER" if game['undercover_id'] == user_id_str else "CIVILIAN"
    await update.message.reply_text(f"🤫 *REVEAL ROLE PREMIUM*\n\nPeran kamu adalah: *{role}*\n*(Saldo telah dikurangi 100 Kith-Coins)*", parse_mode="Markdown")


# === MAIN PROCESS RUNNER ===
def main():
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', show_menu))
    application.add_handler(CallbackQueryHandler(handle_menu_callback, pattern="^btn_"))
    
    application.add_handler(CommandHandler('live', live_photo_handler))
    application.add_handler(CommandHandler('setrequired', set_required_channels))
    application.add_handler(CommandHandler('buytitle', buy_title))
    application.add_handler(CommandHandler(['leaderboard', 'leadboard'], leaderboard))
    
    # Fitur Game
    application.add_handler(CommandHandler('undercover', start_undercover))
    application.add_handler(CommandHandler('vote', submit_word))
    application.add_handler(CommandHandler('sus', sus_vote))
    application.add_handler(CommandHandler('revealrole', reveal_role))
    application.add_handler(CallbackQueryHandler(handle_uc_callbacks, pattern="^uc_"))
    
    # Handler Pesan Biasa & Media (Termasuk Menfess Auto & Manual)
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_pesan))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.TEXT, handle_pesan))

    logger.info("✅ Membangun bot selesai. Menjalankan polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__ == '__main__':
    main()
