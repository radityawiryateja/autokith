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
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LinkPreviewOptions, MessageEntity
from supabase import create_client

# Tarik data dari Environment Variables (Heroku)
try:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    CHANNEL_ID = os.environ.get('CHANNEL_ID')
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
MENFESS_MODE = "auto"
TITLE_PRICE = 500

# Konfigurasi Live Photo Native
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

# === FUNGSI CACHE & STARTUP ===
async def update_settings_cache():
    global MENFESS_MODE
    try:
        res = supabase.table("bot_settings").select("value").eq("key", "menfess_mode").execute()
        MENFESS_MODE = res.data[0]["value"] if res.data else "auto"
    except Exception: pass

async def update_badwords_cache():
    global CACHE_BAD_WORDS
    try:
        res = supabase.table("bad_words").select("word").execute()
        CACHE_BAD_WORDS = {row["word"].lower() for row in res.data} if res.data else set()
    except Exception: pass

async def update_hashtags_cache():
    global CACHE_HASHTAGS
    try:
        response = supabase.table("triggered_hashtags").select("hashtag").eq("active", True).execute()
        CACHE_HASHTAGS = [row["hashtag"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception: pass

async def update_required_channels_cache():
    global required_channels
    try:
        res = supabase.table('required_channels').select("channel_username").execute()
        required_channels = [row["channel_username"] for row in res.data] if res.data else []
    except Exception: pass

async def update_banned_users_cache():
    global CACHE_BANNED_USERS
    try:
        res = supabase.table('banned_users').select("user_id").execute()
        CACHE_BANNED_USERS = [row["user_id"] for row in res.data] if res.data else []
    except Exception: pass

async def check_system_tools():
    if not shutil.which("ffmpeg"): logger.warning("⚠️ FFmpeg TIDAK ditemukan! Fitur /live bakal error.")

async def on_startup(application: Application):
    try:
        me = await application.bot.get_me()
        logger.info(f"✅ Bot siap: @{me.username} (id={me.id})")
        await check_system_tools()
        await update_settings_cache()
        await update_badwords_cache()
        await update_hashtags_cache()
        await update_required_channels_cache()
        await update_banned_users_cache()
    except Exception as e:
        logger.error(f"⚠️ Gagal on_startup: {e}")

async def check_subscription(user_id, context: CallbackContext):
    if not required_channels: return True
    for channel in required_channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']: return False
        except Exception: return False
    return True

async def add_kith_coins(user_id: int, amount: int):
    try:
        res = supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute()
        row = res.data[0] if res.data else {}
        current_balance = row.get("kith_coins", 0) or 0
        current_total = row.get("total_kith_coins", current_balance) or current_balance
        new_balance, new_total = current_balance + amount, current_total + amount
        supabase.table("users").upsert({"user_id": user_id, "kith_coins": new_balance, "total_kith_coins": new_total}).execute()
        return new_balance
    except Exception: return None

async def save_user(user_id, username):
    try:
        supabase.table("users").upsert({"user_id": user_id, "username": username}, on_conflict=["user_id"]).execute()
    except Exception: pass

async def get_all_users():
    try:
        res = supabase.table("users").select("user_id").execute()
        return [row["user_id"] for row in res.data] if res.data else []
    except Exception: return []

# === FITUR KEYBOARD & MENU BAWAH ===
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💬 Beli title"), KeyboardButton("💌 Menfess")],
        [KeyboardButton("👤 Profile"), KeyboardButton("📸 Photo live")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Cancel")]], resize_keyboard=True, is_persistent=True)

async def start(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private": return
    user_id = update.effective_user.id
    if user_id in CACHE_BANNED_USERS: return await update.message.reply_text("❌ Akses kamu diblokir.")
    await save_user(user_id, update.effective_user.username)

    if await check_subscription(user_id, context):
        text = (
            "Halo Kens, selamat datang di *Kitheons*! ☕️\n\n"
            "𔐼 *Kitheons:* [@kitheons](https://t.me/kitheons)\n"
            "𔐼 *Ch Arsip:* [@kithives](https://t.me/kithives)\n\n"
            "Silakan gunakan tombol menu di bawah untuk bernavigasi!"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        inline_kb = [[InlineKeyboardButton("Join Channels", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(inline_kb) if inline_kb else None)

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("👋 *Navigasi Bot*\n\nSilakan pilih menu di bawah:", parse_mode="Markdown", reply_markup=get_main_keyboard())

async def get_group_id(update: Update, context: CallbackContext):
    await update.message.reply_text(f"🆔 ID: `{update.effective_chat.id}`\n🏷️ Nama: {update.effective_chat.title or 'Private'}", parse_mode="Markdown")

# === FITUR LEADERBOARD ===
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        res = supabase.table("users").select("user_id, kith_coins, total_kith_coins").order("total_kith_coins", desc=True).limit(10).execute()
        if not res.data: return await update.message.reply_text("Belum ada data pemain.")
        text = "🏆 *LEADERBOARD TOTAL KITH-COINS* 🏆\n\n"
        for i, row in enumerate(res.data):
            user_id, coins = row.get("user_id"), row.get("total_kith_coins", row.get("kith_coins", 0))
            try:
                chat = await context.bot.get_chat(user_id)
                display_name = f"@{chat.username}" if chat.username else f"{chat.first_name}"
            except Exception: display_name = f"👤 User ID: {user_id}"
            text += f"{i+1}. {display_name} - *{coins}* Coins\n"
        await update.message.reply_text(text + "\nLeaderboard dihitung dari total koin yang pernah diperoleh.", parse_mode="Markdown")
    except Exception: await update.message.reply_text("❌ Gagal mengambil data leaderboard.")

# === FITUR ADMIN (BROADCAST, BADWORDS, COMMANDS) ===
async def broadcast_text(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("⚠️ Format: `/broadcast <Pesan kamu>`", parse_mode="Markdown")
    pesan = update.message.text.split(maxsplit=1)[1]
    users = await get_all_users()
    if not users: return await update.message.reply_text("❌ Data user kosong.")
    
    berhasil, gagal, total_users = 0, 0, len(users)
    status_msg = await update.message.reply_text(f"⏳ Memulai broadcast teks ke *{total_users}* users...", parse_mode="Markdown")
    for i, target_id in enumerate(users):
        try: await context.bot.send_message(chat_id=target_id, text=pesan, parse_mode="Markdown"); berhasil += 1
        except Exception: gagal += 1
        if (i + 1) % 20 == 0 or (i + 1) == total_users:
            try: await status_msg.edit_text(f"⏳ *BROADCAST...*\n📈 Progress: {i+1}/{total_users}\n✅ Berhasil: {berhasil}\n❌ Gagal: {gagal}", parse_mode="Markdown")
            except Exception: pass
            await asyncio.sleep(0.2)
    await status_msg.edit_text(f"✅ *BROADCAST SELESAI!*\n👥 Target: {total_users}\n✅ Sukses: {berhasil}\n❌ Gagal: {gagal}", parse_mode="Markdown")

async def broadcast_forward(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("⚠️ Format: `/broadcastfw <Link Publik Postingan Channel>`", parse_mode="Markdown")
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", context.args[0])
    if not match: return await update.message.reply_text("❌ Link tidak valid!")
    channel_username, message_id = match.groups()
    users = await get_all_users()
    berhasil, gagal, total_users = 0, 0, len(users)
    status_msg = await update.message.reply_text(f"⏳ Memulai forward ke *{total_users}* users...", parse_mode="Markdown")
    for i, target_id in enumerate(users):
        try: await context.bot.forward_message(chat_id=target_id, from_chat_id=f"@{channel_username}", message_id=int(message_id)); berhasil += 1
        except Exception: gagal += 1
        if (i + 1) % 20 == 0 or (i + 1) == total_users:
            try: await status_msg.edit_text(f"⏳ *BROADCAST FW...*\n📈 Progress: {i+1}/{total_users}\n✅ Berhasil: {berhasil}\n❌ Gagal: {gagal}", parse_mode="Markdown")
            except Exception: pass
            await asyncio.sleep(0.2)
    await status_msg.edit_text(f"✅ *BROADCAST FORWARD SELESAI!*\n👥 Target: {total_users}\n✅ Sukses: {berhasil}\n❌ Gagal: {gagal}", parse_mode="Markdown")

async def add_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    words = [w.strip().lower() for w in update.message.text.split(maxsplit=1)[1].split(',')] if len(update.message.text.split()) > 1 else []
    for w in words:
        if w:
            try: supabase.table("bad_words").upsert({"word": w}).execute()
            except Exception: pass
    await update_badwords_cache()
    await update.message.reply_text("✅ Kata terlarang ditambahkan!")

async def remove_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    words = [w.strip().lower() for w in update.message.text.split(maxsplit=1)[1].split(',')] if len(update.message.text.split()) > 1 else []
    for w in words:
        if w:
            try: supabase.table("bad_words").delete().eq("word", w).execute()
            except Exception: pass
    await update_badwords_cache()
    await update.message.reply_text("✅ Kata terlarang dihapus!")

async def list_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    await update.message.reply_text(f"🚫 *Daftar Kata Terlarang:*\n\n{', '.join(sorted(CACHE_BAD_WORDS))}" if CACHE_BAD_WORDS else "Kosong.", parse_mode="Markdown")

async def block_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return
    try:
        supabase.table("banned_users").upsert({"user_id": int(context.args[0])}).execute()
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{context.args[0]}` diblokir.", parse_mode="Markdown")
    except Exception: pass

async def unblock_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return
    try:
        supabase.table("banned_users").delete().eq("user_id", int(context.args[0])).execute()
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{context.args[0]}` di-unblock.", parse_mode="Markdown")
    except Exception: pass

# --- Custom Commands ---
async def add_command_admin(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if len(context.args) < 2: return await update.message.reply_text("Format: /addcommand nama_cmd balasan")
    cmd_name, cmd_reply = context.args[0].lower(), " ".join(context.args[1:])
    try:
        supabase.table("custom_commands").upsert({"command": cmd_name, "reply": cmd_reply}).execute()
        await update.message.reply_text(f"✅ Command /{cmd_name} ditambahkan!")
    except Exception: await update.message.reply_text("❌ Gagal.")

async def delete_command_admin(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Format: /deletecommand nama_cmd")
    try:
        supabase.table("custom_commands").delete().eq("command", context.args[0].lower()).execute()
        await update.message.reply_text(f"✅ Command /{context.args[0].lower()} dihapus!")
    except Exception: await update.message.reply_text("❌ Gagal.")

async def settings_admin(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    text = (
        "⚙️ *PENGATURAN BOT*\n\n"
        f"Mode Menfess: *{MENFESS_MODE.upper()}*\n"
        f"Channel Wajib: {', '.join(required_channels) if required_channels else 'Tidak ada'}\n"
        f"Total Banned Users: {len(CACHE_BANNED_USERS)}\n"
        f"Total Badwords: {len(CACHE_BAD_WORDS)}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# === GAME UNDERCOVER ===
async def start_undercover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context): return await update.message.reply_text("❌ Join channel terlebih dahulu!")
    if update.effective_chat.id != GROUP_ID_DISKUSI: return await update.message.reply_text("❌ Fitur Undercover hanya di grup diskusi!")
    
    user_name, username, game_id = update.effective_user.first_name, update.effective_user.username or update.effective_user.first_name, str(update.message.message_id)
    if supabase.table("uc_active_games").select("*").eq("group_id", GROUP_ID_DISKUSI).execute().data: return await update.message.reply_text("⚠️ Masih ada game yang berjalan!")

    players = {str(user_id): {"name": user_name, "username": username, "current_word": ""}}
    supabase.table("uc_active_games").insert({"game_id": game_id, "group_id": GROUP_ID_DISKUSI, "creator_id": user_id, "status": "lobby", "players": players, "round_number": 0, "votes": {}, "target_word_civil": "", "target_word_under": "", "undercover_id": ""}).execute()
    kb = [[InlineKeyboardButton("🎮 Gabung", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai", callback_data="uc_start")]]
    await update.message.reply_text(f"🕵️‍♂️ *GAME UNDERCOVER*\n\nRoom Master: {user_name}\n\n👥 *Pemain Terdaftar:*\n1. {user_name} (@{username})\n\n*(Minimal 3 pemain)*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def handle_uc_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id, user_name, username = query.from_user.id, query.from_user.first_name, query.from_user.username or query.from_user.first_name
    game_id = str(query.message.reply_to_message.message_id if query.message.reply_to_message else query.message.message_id)
    res = supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute()
    if not res.data: return
    game = res.data[0]; players = game['players']

    if query.data == "uc_join" and game['status'] == 'lobby' and str(user_id) not in players:
        players[str(user_id)] = {"name": user_name, "username": username, "current_word": ""}
        supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute()
        player_list = "\n".join([f"{i+1}. {p['name']} (@{p['username']})" for i, p in enumerate(players.values())])
        kb = [[InlineKeyboardButton("🎮 Gabung", callback_data="uc_join")], [InlineKeyboardButton("▶️ Mulai", callback_data="uc_start")]]
        await query.edit_message_text(f"🕵️‍♂️ *GAME UNDERCOVER*\n\n👥 *Pemain Terdaftar:*\n{player_list}\n\n*(Minimal 3 pemain)*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        
    elif query.data == "uc_start" and user_id == game['creator_id'] and len(players) >= 3:
        words_res = supabase.table("uc_words").select("*").execute()
        if not words_res.data: return
        word_pair = random.choice(words_res.data)
        word1, word2 = word_pair["word1"], word_pair["word2"]
        civil_word, under_word = (word1, word2) if random.choice([True, False]) else (word2, word1)
        undercover_id = random.choice(list(players.keys()))

        supabase.table("uc_active_games").update({"status": "playing", "target_word_civil": civil_word, "target_word_under": under_word, "undercover_id": undercover_id}).eq("game_id", game_id).execute()
        await query.edit_message_text(f"🚀 *GAME DIMULAI!*\nCek Private Message (DM) bot untuk melihat kata milikmu.\nSilakan diskusikan dan kirim deskripsi dengan `/vote [kata]` di grup.", parse_mode="Markdown")
        for pid in players.keys():
            try: await context.bot.send_message(int(pid), f"🕵️‍♂️ *Peran Kamu*\n\nKata kamu: *{under_word if pid == undercover_id else civil_word}*", parse_mode="Markdown")
            except: pass

async def submit_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.effective_chat.id != GROUP_ID_DISKUSI: return
    if not context.args: return
    res = supabase.table("uc_active_games").select("*").eq("status", "playing").execute()
    game = next((g for g in res.data if str(user_id) in g['players']), None) if res.data else None
    if not game: return
    players = game['players']
    players[str(user_id)]['current_word'] = " ".join(context.args)
    supabase.table("uc_active_games").update({"players": players}).eq("game_id", game['game_id']).execute()
    await update.message.reply_text(f"✅ Deskripsi diterima dari {update.effective_user.first_name}!")

async def sus_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.effective_chat.id != GROUP_ID_DISKUSI: return
    res = supabase.table("uc_active_games").select("*").eq("status", "voting").execute()
    game = next((g for g in res.data if str(user_id) in g['players']), None) if res.data else None
    if not game or not update.message.entities: return
    
    mentioned_username = next((update.message.text[ent.offset+1:ent.offset+ent.length] for ent in update.message.entities if ent.type == "mention"), None)
    if not mentioned_username: return
    target_id = next((pid for pid, p in game['players'].items() if p.get('username') == mentioned_username), None)
    if not target_id: return await update.message.reply_text("⚠️ User tidak ditemukan dalam game aktif!")
    
    votes = game['votes']
    if str(user_id) in votes: return await update.message.reply_text("⚠️ Kamu sudah vote!")
    votes[str(user_id)] = target_id
    supabase.table("uc_active_games").update({"votes": votes}).eq("game_id", game['game_id']).execute()
    await update.message.reply_text(f"✅ {update.effective_user.first_name} menuduh @{mentioned_username}!")

async def reveal_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = supabase.table("uc_active_games").select("*").eq("status", "playing").execute()
    game = next((g for g in res.data if str(user_id) in g['players']), None) if res.data else None
    if not game: return await update.message.reply_text("❌ Kamu tidak bermain di game aktif.")
    koin_res = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
    if not koin_res.data or koin_res.data[0]['kith_coins'] < 100: return await update.message.reply_text("❌ Butuh 100 Kith-Coins.")
    supabase.table("users").update({"kith_coins": koin_res.data[0]['kith_coins'] - 100}).eq("user_id", user_id).execute()
    await update.message.reply_text(f"🤫 *REVEAL ROLE PREMIUM*\nPeran kamu: *{'UNDERCOVER' if game['undercover_id'] == str(user_id) else 'CIVILIAN'}*\n*(Saldo dipotong 100 Coins)*", parse_mode="Markdown")

# === SISTEM LIVE PHOTO ===
def _get_video_file_from_message(msg):
    for attr in ('video', 'document', 'animation'):
        obj = getattr(msg, attr, None)
        if obj and (attr != 'document' or (obj.mime_type and obj.mime_type.startswith("video/"))): return obj
    if msg.reply_to_message: return _get_video_file_from_message(msg.reply_to_message)
    return None

async def _run_cmd(cmd, timeout=120):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0: raise RuntimeError(f"Command failed:\n{stderr.decode('utf-8', errors='ignore')}")
    except asyncio.TimeoutError:
        proc.kill(); raise RuntimeError("Timeout.")
    return stdout, stderr

async def _send_live_photo_direct(bot_token, chat_id, video_path, photo_path, message_thread_id=None, reply_to_message_id=None):
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendLivePhoto"
    data = {"chat_id": chat_id}
    if message_thread_id: data["message_thread_id"] = message_thread_id
    if reply_to_message_id: data["reply_to_message_id"] = reply_to_message_id
    with open(video_path, "rb") as vf, open(photo_path, "rb") as pf:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, data=data, files={"live_photo": vf, "photo": pf})
    if not resp.json().get("ok"): raise RuntimeError(resp.json().get('description'))
    return resp.json().get("result")

async def live_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user_id = update.message, update.effective_user.id
    video = _get_video_file_from_message(msg)
    if not video: return await msg.reply_text("Silakan kirim video terlebih dahulu.", reply_markup=get_main_keyboard())
    if not shutil.which("ffmpeg"): return await msg.reply_text("❌ FFmpeg belum kebaca di server.", reply_markup=get_main_keyboard())

    if (getattr(video, "file_size", 0) or 0) > (LIVE_MAX_INPUT_FILE_SIZE_MB * 1024 * 1024):
        return await msg.reply_text(f"❌ Video terlalu besar (Max {LIVE_MAX_INPUT_FILE_SIZE_MB}MB).", reply_markup=get_main_keyboard())

    try:
        res = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
        current_balance = res.data[0].get("kith_coins", 0) if res.data else 0
        if current_balance < 100: return await msg.reply_text(f"❌ Kith-Coins kurang (Biaya: 100 Coins). Saldo: {current_balance}", reply_markup=get_main_keyboard())
        supabase.table("users").update({"kith_coins": current_balance - 100}).eq("user_id", user_id).execute()
    except Exception: return await msg.reply_text("❌ Gagal mengecek saldo.", reply_markup=get_main_keyboard())

    status_msg = await msg.reply_text("⏳ Memproses Live Photo... *(Saldo dipotong 100 Coins)*\nTahap 1/4: download video", parse_mode="Markdown", reply_markup=get_main_keyboard())
    asset_id = str(uuid.uuid4()).upper()
    input_path, output_live_photo, output_photo = f"in_{asset_id}.mp4", f"out_{asset_id}.mp4", f"pic_{asset_id}.jpg"
    
    try:
        telegram_file = await video.get_file()
        await telegram_file.download_to_drive(input_path)
        await status_msg.edit_text("⏳ Tahap 2/4: convert ke format Live Photo")

        for attempt in [{"width": 720, "vb": "3500k", "mr": "4200k", "bs": "8400k", "ab": "96k"}, {"width": 540, "vb": "2200k", "mr": "2600k", "bs": "5200k", "ab": "80k"}]:
            try:
                if os.path.exists(output_live_photo): os.remove(output_live_photo)
                vf = f"scale={attempt['width']}:-2:force_original_aspect_ratio=decrease,setsar=1,fps=30"
                await _run_cmd([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", input_path, "-t", str(LIVE_MAX_DURATION), "-map", "0:v:0", "-map", "0:a?", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-b:v", attempt["vb"], "-maxrate", attempt["mr"], "-bufsize", attempt["bs"], "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", attempt["ab"], "-movflags", "+faststart", "-y", output_live_photo], timeout=240)
                if os.path.getsize(output_live_photo) / (1024 * 1024) <= LIVE_MAX_OUTPUT_FILE_SIZE_MB: break
            except Exception: pass
                
        await status_msg.edit_text("⏳ Tahap 3/4: extract static photo")
        await _run_cmd([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", output_live_photo, "-vframes", "1", "-q:v", "2", "-y", output_photo])

        await status_msg.edit_text("⏳ Tahap 4/4: upload Live Photo")
        await _send_live_photo_direct(BOT_TOKEN, msg.chat_id, output_live_photo, output_photo, msg.message_thread_id, msg.message_id)
        await status_msg.delete()
        
    except Exception as e:
        logger.exception("Gagal live photo")
        try: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute()
        except Exception: pass
        await status_msg.edit_text(f"❌ Gagal memproses:\n{e}\n\n*(Koin kamu telah di-Refund 100 Coins)*", parse_mode="Markdown")
    finally:
        for p in [input_path, output_live_photo, output_photo]:
            if os.path.exists(p): os.remove(p)


# === HANDLER PESAN & STATE MACHINE UTAMA ===
async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE
    if update.effective_chat.type != "private": return
    if not bot_active: return await update.message.reply_text("Bot sedang dipause oleh admin.")

    user_id, username, first_name = update.effective_user.id, update.effective_user.username, update.effective_user.first_name
    if user_id in CACHE_BANNED_USERS: return await update.message.reply_text("❌ Pesan ditolak. Akses diblokir.")

    # Balasan Anonim (Dari Notif Komentar Sender)
    if update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        match = re.search(r"#ID:(\d+)", replied_text)
        if match:
            try:
                comment_msg_id = int(match.group(1))
                if update.message.text: await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text=f"🗣️ *Balasan Sender:*\n\n{update.message.text}", reply_to_message_id=comment_msg_id, parse_mode="Markdown")
                else: await context.bot.copy_message(chat_id=GROUP_ID_DISKUSI, from_chat_id=user_id, message_id=update.message.message_id, reply_to_message_id=comment_msg_id, caption=f"🗣️ *Balasan Sender:*\n\n{update.message.caption or ''}", parse_mode="Markdown")
                return await update.message.reply_text("✅ Balasan anonim dikirim!")
            except Exception: return await update.message.reply_text("❌ Gagal membalas.")

    if not await check_subscription(user_id, context):
        inline_kb = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        return await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(inline_kb) if inline_kb else None)

    pesan_teks = update.message.text or update.message.caption or ""
    state = context.user_data.get('state')

    # FASE 1: TANGKAP TOMBOL
    if pesan_teks == "❌ Cancel":
        context.user_data['state'] = None
        return await update.message.reply_text("✅ Aksi dibatalkan. Kembali ke menu utama.", reply_markup=get_main_keyboard())

    if pesan_teks == "👤 Profile":
        context.user_data['state'] = None
        try:
            res = supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute()
            row = res.data[0] if res.data else {}
            return await update.message.reply_text(f"👤 *PROFIL KAMU*\n\n🆔 ID: `{user_id}`\n🏷️ Username: @{username or first_name}\n🪙 Saldo Kith-Coins: *{row.get('kith_coins', 0)}*\n🏆 Total Koin Diperoleh: *{row.get('total_kith_coins', row.get('kith_coins', 0))}*", parse_mode="Markdown", reply_markup=get_main_keyboard())
        except Exception: return await update.message.reply_text("❌ Gagal mengambil profil.")

    elif pesan_teks == "💬 Beli title":
        context.user_data['state'] = 'WAITING_TITLE'
        return await update.message.reply_text(f"🛒 *Beli Custom Title*\nHarga: **{TITLE_PRICE} Kith-Coins**.\n\nKetik **Nama Title Barumu** (Maks 16 karakter):", parse_mode="Markdown", reply_markup=get_cancel_keyboard())

    elif pesan_teks == "📸 Photo live":
        context.user_data['state'] = 'WAITING_LIVE_VIDEO'
        return await update.message.reply_text("📸 *Buat Photo Live*\nBiaya: **100 Kith-Coins**\n\nKirim/forward videonya ke sini (Maks 10 detik, < 10MB).", parse_mode="Markdown", reply_markup=get_cancel_keyboard())

    elif pesan_teks == "💌 Menfess":
        context.user_data['state'] = 'WAITING_MENFESS'
        return await update.message.reply_text("💌 *Kirim Menfess*\n\nSilakan ketik pesan menfess kamu sekarang!", parse_mode="Markdown", reply_markup=get_main_keyboard())

    # FASE 2: PROSES STATE
    if state == 'WAITING_TITLE':
        context.user_data['state'] = None 
        if len(pesan_teks) > 16: return await update.message.reply_text("❌ Nama title maksimal 16 karakter.", reply_markup=get_main_keyboard())
        try:
            res = supabase.table("users").select("kith_coins").eq("user_id", user_id).execute()
            current_balance = res.data[0].get("kith_coins", 0) if res.data else 0
            if current_balance < TITLE_PRICE: return await update.message.reply_text(f"❌ Kith-Coins kurang. Saldo: {current_balance}", reply_markup=get_main_keyboard())
            supabase.table("users").update({"kith_coins": current_balance - TITLE_PRICE}).eq("user_id", user_id).execute()
            try:
                await context.bot.set_chat_member_tag(chat_id=GROUP_ID_DISKUSI, user_id=user_id, tag=pesan_teks)
                await update.message.reply_text(f"✅ Berhasil!\n🏷️ Title: `{pesan_teks}`\n🪙 Sisa Kith-Coins: {current_balance - TITLE_PRICE}", parse_mode="Markdown", reply_markup=get_main_keyboard())
            except Exception as e:
                supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute()
                await update.message.reply_text("❌ Gagal set title di grup. Pastikan sudah join grup diskusi. Koin di-refund.", reply_markup=get_main_keyboard())
        except Exception: await update.message.reply_text("❌ Terjadi kesalahan saat beli title.", reply_markup=get_main_keyboard())
        return

    elif state == 'WAITING_LIVE_VIDEO':
        context.user_data['state'] = None 
        if not _get_video_file_from_message(update.message): return await update.message.reply_text("❌ Itu bukan video! Aksi dibatalkan.", reply_markup=get_main_keyboard())
        return await live_photo_handler(update, context) 

    # FASE 3: DEFAULT (KIRIM MENFESS)
    context.user_data['state'] = None 
    if any(re.search(rf'\b{re.escape(bw)}\b', pesan_teks.lower()) for bw in CACHE_BAD_WORDS): return await update.message.reply_text("❌ Pesan ditolak karena mengandung kata terlarang.", reply_markup=get_main_keyboard())

    if MENFESS_MODE == "auto":
        if not update.message.text: return await update.message.reply_text("❌ Sesi auto aktif! Kirim teks saja (tanpa media).", reply_markup=get_main_keyboard())
        if len(update.message.text) > 70: return await update.message.reply_text(f"❌ Menfess terlalu panjang (Maks 70, saat ini {len(update.message.text)}).", reply_markup=get_main_keyboard())
        if (update.message.entities and any(ent.type == "mention" for ent in update.message.entities)) or re.search(r'(?:^|\s)@/?\w+', pesan_teks): return await update.message.reply_text("❌ Dilarang mention username!", reply_markup=get_main_keyboard())

        teks_asli, target_username = update.message.text, username or first_name
        invisible_link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=len(teks_asli.encode('utf-16-le')) // 2, length=1, url=f"https://t.me/{target_username}")
        
        try:
            message_sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=teks_asli + "\u200B", entities=list(update.message.entities or []) + [invisible_link], link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True))
            CACHE_COMSECT_OFF.add(message_sent.message_id)
            new_balance = await add_kith_coins(user_id, 50)
            
            kb = [[InlineKeyboardButton("Lihat Pesan", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
            await update.message.reply_text(f"✅ Terkirim! 🪶" + (f"\n💰 *+50 Coins* (Saldo: {new_balance})" if new_balance else ""), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            try: supabase.table("menfess_map").insert({"post_id": message_sent.message_id, "sender_user_id": user_id}).execute()
            except Exception: pass
        except Exception: await update.message.reply_text("❌ Gagal mengirim menfess.", reply_markup=get_main_keyboard())
    else:
        # Manual Review
        try:
            fw_msg = await context.bot.copy_message(chat_id=ADMIN_GROUP_ID, from_chat_id=user_id, message_id=update.message.message_id)
            kb = [[InlineKeyboardButton("✅ Acc (CS ON)", callback_data=f"mf|A_ON|{user_id}|{update.message.message_id}"), InlineKeyboardButton("🔕 Acc (CS OFF)", callback_data=f"mf|A_OFF|{user_id}|{update.message.message_id}")], [InlineKeyboardButton("❌ Tolak", callback_data=f"mf|R|{user_id}|{update.message.message_id}")]]
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"🚨 *REVIEW MENFESS*\n👤 Pengirim: @{username or first_name}\n🆔 `{user_id}`", reply_to_message_id=fw_msg.message_id, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            await update.message.reply_text("⏳ Masuk ke antrean admin review.", reply_markup=get_main_keyboard())
        except Exception: await update.message.reply_text("❌ Gagal antre review.", reply_markup=get_main_keyboard())


# === HANDLER GRUP & ADMIN YANG SEBELUMNYA HILANG ===

# 1. NOTIF KOMENTAR & HAPUS COMSECT OFF
async def handle_discussion(update: Update, context: CallbackContext):
    msg = update.effective_message
    if not msg: return
    
    if msg.is_automatic_forward:
        post_id = getattr(msg, 'forward_from_message_id', getattr(msg.forward_origin, 'message_id', None))
        if post_id in CACHE_COMSECT_OFF:
            try: await msg.delete(); CACHE_COMSECT_OFF.remove(post_id)
            except Exception: pass
        return

    # Notif Komentar
    if msg.reply_to_message and msg.reply_to_message.is_automatic_forward:
        post_id = getattr(msg.reply_to_message, 'forward_from_message_id', getattr(msg.reply_to_message.forward_origin, 'message_id', None))
        if post_id:
            try:
                res = supabase.table("menfess_map").select("sender_user_id").eq("post_id", post_id).execute()
                if res.data and not update.effective_user.is_bot:
                    sender_id = res.data[0]["sender_user_id"]
                    link = f"https://t.me/c/{str(GROUP_ID_DISKUSI).replace('-100', '')}/{msg.message_id}"
                    notif_text = (f"💬 *Ada komentar baru di Menfess kamu!*\n\n📝 *Komentar:* {msg.text or msg.caption or '(Media)'}\n\n[➡️ Klik untuk melihat komentar]({link})\n\n_Untuk membalas secara anonim, balas (reply) pesan ini dan ketik balasanmu._\n`#ID:{msg.message_id}`")
                    await context.bot.send_message(chat_id=sender_id, text=notif_text, parse_mode="Markdown")
            except Exception: pass

# 2. REVIEW MANUAL ACC / TOLAK
async def handle_callback_review(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data.split('|')
    if len(data) < 4: return
    action, user_id, msg_id = data[1], int(data[2]), int(data[3])
    admin_name = query.from_user.first_name
    await query.answer()

    if action == 'R':
        await query.edit_message_text(f"{query.message.text}\n\n❌ *Ditolak* oleh {admin_name}", parse_mode="Markdown")
        try: await context.bot.send_message(chat_id=user_id, text="❌ Menfess kamu ditolak oleh admin.")
        except Exception: pass
        return

    is_comsect_off = (action == 'A_OFF')
    try:
        sent_msg = await context.bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=user_id, message_id=msg_id)
        if is_comsect_off: CACHE_COMSECT_OFF.add(sent_msg.message_id)
        try: supabase.table("menfess_map").insert({"post_id": sent_msg.message_id, "sender_user_id": user_id}).execute()
        except Exception: pass

        await context.bot.send_message(chat_id=user_id, text=f"✅ Menfess kamu telah di-Acc!\n[Lihat Menfess](https://t.me/{CHANNEL_ID[1:]}/{sent_msg.message_id})", parse_mode="Markdown")
        status = "🔕 (Comsect OFF)" if is_comsect_off else "✅ (Comsect ON)"
        await query.edit_message_text(f"{query.message.text}\n\n{status} di-Acc oleh {admin_name}", parse_mode="Markdown")
    except Exception as e:
        await query.message.reply_text(f"❌ Gagal mengirim: {e}")

# 3. BALASAN ADMIN KE SENDER VIA LOG
async def handle_admin_reply(update: Update, context: CallbackContext):
    if update.effective_chat.id not in [ADMIN_GROUP_ID, LOG_GROUP_ID] or not update.message.reply_to_message: return
    match = re.search(r"🆔 (?:ID: )?`?(\d+)`?", update.message.reply_to_message.text or "")
    if match:
        user_id = int(match.group(1))
        try:
            if update.message.text: await context.bot.send_message(chat_id=user_id, text=f"👨‍✈️ *Pesan dari Admin:*\n\n{update.message.text}", parse_mode="Markdown")
            else: await context.bot.copy_message(chat_id=user_id, from_chat_id=update.effective_chat.id, message_id=update.message.message_id, caption=f"👨‍✈️ *Pesan dari Admin:*\n\n{update.message.caption or ''}", parse_mode="Markdown")
            await update.message.reply_text("✅ Pesan admin terkirim ke user!")
        except Exception: await update.message.reply_text("❌ Gagal mengirim pesan ke user. Mungkin bot diblokir.")

# 4. CUSTOM COMMAND LISTENER (Fall-back)
async def handle_custom_command(update: Update, context: CallbackContext):
    if not update.message or not update.message.text: return
    cmd_name = update.message.text.split()[0][1:].lower()
    try:
        res = supabase.table("custom_commands").select("reply").eq("command", cmd_name).execute()
        if res.data: await update.message.reply_text(res.data[0]['reply'])
    except Exception: pass


# === FUNGSI SETTINGS LAINNYA ===
async def set_mode_auto(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "auto"
    try: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "auto"}).execute()
    except Exception: pass
    await update.message.reply_text("✅ Mode diubah ke *AUTO*.", parse_mode="Markdown")

async def set_mode_manual(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "manual"
    try: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "manual"}).execute()
    except Exception: pass
    await update.message.reply_text("⏸️ Mode diubah ke *MANUAL*.", parse_mode="Markdown")

# === MAIN PROCESS RUNNER ===
def main():
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # Commands Dasar
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', cmd_menu))
    application.add_handler(CommandHandler('live', live_photo_handler))
    application.add_handler(CommandHandler(['leaderboard', 'leadboard'], leaderboard))
    
    # Game Undercover
    application.add_handler(CommandHandler('undercover', start_undercover))
    application.add_handler(CommandHandler('vote', submit_word))
    application.add_handler(CommandHandler('sus', sus_vote))
    application.add_handler(CommandHandler('revealrole', reveal_role))
    application.add_handler(CallbackQueryHandler(handle_uc_callbacks, pattern="^uc_"))
    
    # Command Admin
    application.add_handler(CommandHandler('auto', set_mode_auto))
    application.add_handler(CommandHandler('manual', set_mode_manual))
    application.add_handler(CommandHandler('broadcast', broadcast_text))
    application.add_handler(CommandHandler('broadcastfw', broadcast_forward))
    application.add_handler(CommandHandler('addbadwords', add_badwords))
    application.add_handler(CommandHandler('removebadwords', remove_badwords))
    application.add_handler(CommandHandler('listbadwords', list_badwords))
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('unblock', unblock_user))
    application.add_handler(CommandHandler('setrequired', set_required_channels))
    application.add_handler(CommandHandler('addcommand', add_command_admin))
    application.add_handler(CommandHandler('deletecommand', delete_command_admin))
    application.add_handler(CommandHandler('grupid', get_group_id))
    application.add_handler(CommandHandler('settings', settings_admin))
    
    # Handler State Machine Private Message
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_pesan))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.TEXT, handle_pesan))

    # Handler Grup Comsect & Admin
    application.add_handler(MessageHandler(filters.Chat(GROUP_ID_DISKUSI), handle_discussion))
    application.add_handler(CallbackQueryHandler(handle_callback_review, pattern="^mf\|"))
    application.add_handler(MessageHandler(filters.ALL & filters.Chat([ADMIN_GROUP_ID, LOG_GROUP_ID]), handle_admin_reply))

    # Fall-back Custom Command Handler
    application.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.PRIVATE, handle_custom_command))

    logger.info("✅ Membangun bot selesai. Menjalankan polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__ == '__main__':
    main()
