# -*- coding: utf-8 -*-
import json
import logging
import re
import html
import markdown
import os
import random
import uuid
import subprocess
import shutil
import asyncio
import httpx
import concurrent.futures

from dotenv import load_dotenv
load_dotenv()

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes, CallbackContext, TypeHandler
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LinkPreviewOptions, MessageEntity, ChatMemberUpdated, ChatMember
from supabase import create_client
from datetime import datetime, timezone, timedelta
from userbot import start_userbot, kirim_menfess_vip, reload_preview_userbot
from telegram.ext import MessageReactionHandler


# Tarik data dari Environment Variables (Heroku) - HANYA KREDENSIAL UTAMA
try:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

    CHANNEL_ID = os.environ.get('CHANNEL_ID')
    GROUP_ID_DISKUSI = int(os.environ.get('GROUP_ID_DISKUSI', -1002936651839))
    ADMIN_GROUP_ID = int(os.environ.get('ADMIN_GROUP_ID', -1003647473093))
    LOG_GROUP_ID = int(os.environ.get('LOG_GROUP_ID', -1003900893106))
    
    # === TAMBAHAN ID TOPIK UNTUK LOG DI GRUP ADMIN ===
    TOPIC_ID_MENFESS_LOG = int(os.environ.get('TOPIC_ID_MENFESS_LOG', 8630))
    TOPIC_ID_CORT_LOG = int(os.environ.get('TOPIC_ID_CORT_LOG', 8654))
    TOPIC_ID_POLL_LOG = int(os.environ.get('TOPIC_ID_POLL_LOG', 8656))
    TOPIC_ID_ANON_LOG = int(os.environ.get('TOPIC_ID_ANON_LOG', 5417))
    TOPIC_ID_VIP_LOG = int(os.environ.get('TOPIC_ID_VIP_LOG', 15881))

    LIVE_MAX_DURATION = min(float(os.environ.get('LIVE_MAX_DURATION', "9.8")), 9.8)
    LIVE_MAX_INPUT_FILE_SIZE_MB = int(os.environ.get('LIVE_MAX_INPUT_FILE_SIZE_MB', "50"))
    LIVE_MAX_OUTPUT_FILE_SIZE_MB = min(int(os.environ.get('LIVE_MAX_OUTPUT_FILE_SIZE_MB', "10")), 10)
    LIVE_PHOTO_PRICE = int(os.environ.get("LIVE_PHOTO_PRICE", "100"))

except Exception as e:
    print(f"⚠️ Error mengambil Environment Variables: {e}")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

bot_active = True
LAST_VIP_PROMO_TIME = None
MENFESS_MODE = "auto"
TITLE_PRICE = 500  

WAITING_USERNAME = 1
KEYBOARD_STATE_TITLE = "WAITING_TITLE_FROM_KEYBOARD"
KEYBOARD_STATE_LIVE = "WAITING_LIVE_FROM_KEYBOARD"

# Supabase Client Initialization
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error(f"Gagal inisialisasi Supabase: {e}")

CACHE_HASHTAGS = []
required_channels = []
CACHE_BANNED_USERS = []
CACHE_COMSECT_OFF = set()
CACHE_BAD_WORDS = set()
CORT_VOTES = {}
BOARDREP_CACHE = {}
CACHE_USERNAMES = {}
CACHE_PASSIVE_USERS = set()
POLL_LOG_GROUP_ID = int(os.environ.get('POLL_LOG_GROUP_ID', LOG_GROUP_ID))
POLL_DB = {}
POLL_ANON, POLL_BTN_TEXT = range(20, 22)
TOPIC_ID_ANON_LOG = 5417
BROADCAST_DELETE_CACHE = {}
ACTIVE_GA_CACHE = {} 

# ========== TAMBAHAN: VIP CACHE ==========
VIP_CACHE = {}
VIP_CACHE_TTL = timedelta(minutes=5)    # refresh TTL 5 menit
CACHE_REACTION_WARNED = set()          
REACTION_BAD_EMOJIS = {"😢", "👀"}      
REACTION_THRESHOLD = 3                  
GA_TYPE, GA_VALUE, GA_WINNERS, GA_TIME = range(40, 44)

WORDING_DEFAULTS = {
    # === UMUM ===
    "welcome": "Halo Kens, selamat datang di <b>Kitheons</b>! ☕️\n\n𔐼 <b>Kitheons:</b> <a href=\"https://telegram.me/kitheons\">@kitheons</a>\n𔐼 <b>Ch Arsip:</b> <a href=\"https://telegram.me/kithives\">@kithives</a>\n\nKetuk /menu untuk menampilkan navigasi.\n<b>(Semua pesan yang kamu kirim otomatis diajukan sebagai menfess)</b>",
    "join_channel_prompt": "Sebelum lanjut, silakan join channel berikut dulu ya!",

    # === PROFIL & LEADERBOARD ===
    "profile_text": "👤 <b>PROFIL KAMU</b>\n\n🆔 ID: <code>{user_id}</code>\n🏷️ Status: {status_text}\n🪙 Saldo Kith-Coins: <b>{coins}</b>\n🏆 Total Koin Diperoleh: <b>{total_coins}</b>\n",
    "profile_gagal": "❌ Gagal mengambil data profil.",
    "sengaja_gagal": "Dibatalkan.",
    "leaderboard_header": "🏆 <b>LEADERBOARD TOTAL KITH-COINS</b> 🏆\n\n",
    "leaderboard_footer": "\nLeaderboard dihitung dari total koin yang pernah diperoleh, bukan saldo saat ini.",
    "leaderboard_kosong": "Belum ada data pemain.",
    "leaderboard_gagal": "❌ Gagal mengambil data leaderboard.",

    # === VIP ===
    "vip_batal": "✅ Pembelian VIP dibatalkan.",
    "vip_menu_header": "💎 <b>Pilih Paket Premium Kitheons:</b>",
    "vip_menu_desc": "✨ <b>Keuntungan VIP:</b>\n- Cooldown auto menfess cuma 10 menit\n- Diskon 50% buat beli Title &amp; Photo Live\n- Bisa gunain emoji premium di self promote\n- Langsung dapet <b>5000 Kith-Coins</b> saat aktif!",
    "vip_checkout_caption": "🛒 <b>Checkout VIP {dur_text}</b>\n\nTotal yang harus dibayar: <b>Rp {formatted_price}</b>\n\n⚠️ <b>PENTING:</b> Wajib ketik kode <code>{kode_unik}</code> di bagian <b>Catatan/Berita Acara/Pesan</b> saat transfer!\n\n📸 Jika sudah transfer, silakan <b>kirim foto bukti pembayaran</b> ke sini sekarang.",
    "vip_acc_notif": "🎉 <b>SELAMAT! Pembayaran VIP kamu telah disetujui!</b>\n\nStatus Premium kamu sudah aktif dan kamu mendapatkan bonus instan 💰 <b>5000 Kith-Coins</b>!\n\nSelamat menikmati diskon 50% dan cooldown menfess yang jauh lebih singkat! 🚀",
    "vip_acc_gagal": "❌ Database error saat ACC VIP.",
    "vip_reject_notif": "⚠️ <b>Pembelian VIP Ditolak</b>\n\nMaaf, bukti pembayaran kamu tidak valid atau dana belum masuk. Jika merasa ini kesalahan, silakan hubungi admin.",
    "vip_receipt_diterima": "⏳ Bukti pembayaran berhasil dikirim! Silakan tunggu admin memverifikasi. (Kamu sudah kembali ke mode menfess biasa)",
    "vip_receipt_bukan_foto": "❌ Tolong kirimkan FOTO bukti pembayaran ya! (Atau tekan tombol 'Batalkan Pembelian' di atas)",
    "vip_promo_cooldown": "💡 <b>Bosan nunggu lama?</b> Yuk upgrade 💎 VIP biar cooldown-nya cuma 10 menit! Cek paketnya di menu <b>👤 Profile</b>.",
    "vip_promo_title_live": "\n\n💡 <b>Koin kurang?</b> Upgrade 💎 Premium aja biar dapet diskon 50% sekaligus bonus 5000 koin!",
    "vip_promo_title_prompt": "\n\n💡 <b>Tips Hemat:</b> Upgrade ke Premium di menu Profile buat dapet harga diskon 50% (Harga VIP cuma <b>{harga_vip} Coins</b>)",
    "vip_promo_auto_terkirim": "\n\n💡 <b>Tips:</b> Upgrade ke Premium di menu Profile biar nunggu cooldown selanjutnya cuma 30 menit! 🚀",
    "vip_fallback_batal": "✅ Pengiriman dibatalkan. Silakan kirim ulang menfess kamu menggunakan username utama (non-fragment).",
    "vip_fallback_sukses": "✅ Pesan kamu telah dikirim ke channel (Menggunakan mode standar tanpa identitas VIP).{coin_msg}",

    # === TITLE ===
    "title_format_salah": "⚠️ Format salah!\nGunakan: <code>/buytitle <nama_title></code>\nContoh: <code>/buytitle The Undercover Pro</code>\n\n<b>Harga: {title_price} Kith-Coins</b>",
    "title_max_char": "❌ Gagal! Nama title maksimal 16 karakter ya.",
    "title_kosong": "❌ Nama title tidak boleh kosong.",
    "title_koin_kurang": "❌ Kith-Coins kamu tidak cukup.\nSaldo kamu: {saldo} Coins\nHarga Title: {harga} Coins{promo}",
    "title_sukses": "✅ Transaksi Berhasil!\n\n🏷️ Title barumu: <code>{new_title}</code>\n🪙 Sisa saldo Kith-Coins: {new_balance}\n\nSilakan kirim pesan di grup diskusi untuk melihat title barumu!",
    "title_gagal_join_grup": "❌ Gagal menerapkan title. Pastikan kamu sudah join ke grup diskusi terlebih dahulu dan jangan gunakan emoji!\n\nKoin kamu telah dikembalikan (Refund).",
    "title_gagal_umum": "❌ Gagal menerapkan title. Pastikan kamu sudah join ke grup diskusi terlebih dahulu dan jangan gunakan emoji.\n\nKoin kamu telah dikembalikan (Refund).",
    "title_db_error": "❌ Terjadi kesalahan pada database. Silakan coba lagi nanti.",
    "title_prompt_beli": "🛒 <b>Beli Custom Title</b>\nHarga: <b>{harga} Kith-Coins</b>.\n\nKetik <b>Nama Title Barumu</b> (Maks 16 karakter):{promo}",
    "title_only_dm": "🛒 Silakan gunakan command ini di chat pribadi (DM) dengan bot.",
    "title_verified_alert": "✅ Terverifikasi! Silakan masukkan title barumu.",
    "title_verified_prompt": "✅ Sip, kamu sudah terdeteksi join grup diskusi!\n\nSilakan ketik ulang <b>Nama Title Barumu</b> (Maks 16 karakter):",
    "title_belum_join_alert": "❌ Kamu masih belum terdeteksi join grup diskusi. Silakan join dulu ya!",

    # === MENFESS (AUTO / MANUAL / CORT) ===
    "menfess_bad_words": "❌ Menfess ditolak karena mengandung kata-kata yang dilarang oleh base.",
    "menfess_base_closed": "⛔ Sesi menfess saat ini sedang ditutup. Pesanmu telah diteruskan langsung ke admin sebagai pesan biasa.",
    "menfess_base_closed_gagal": "⛔ Sesi menfess saat ini sedang ditutup oleh admin.",
    "menfess_manual_masuk_antrean": "⏳ Menfess kamu sedang masuk ke antrean admin untuk direview. Mohon tunggu ya!",
    "menfess_manual_gagal_kirim": "❌ Gagal mengirim menfess ke admin review.",
    "menfess_disetujui": "✅ Yay! Menfess kamu telah disetujui admin! ({status_text}){coin_msg}",
    "menfess_ditolak": "⚠️ <b>Menfess Ditolak</b>\n\nMaaf, menfess kamu ditolak oleh admin karena belum sesuai dengan rules base. Silakan perbaiki format/isi menfess kamu dan kirim ulang ya!",
    "menfess_teks_diterima": "⏳ Teks diterima! Sekarang kirimkan *<b>username</b><b> kamu untuk di-hyperlink (contoh: jake/@jake).\n\n</b>Ketik /cancel untuk membatalkan.*",
    "menfess_cooldown_reguler": "⏳ Ups! Kamu masih dalam masa cooldown. Sisa waktu: <b>{sisa_menit} menit</b>.\n\n{vip_promo}",
    "menfess_cooldown_vip": "⏳ Ups! Kamu masih dalam masa cooldown 💎 VIP. Silakan kirim menfess lagi dalam {sisa_menit} menit.",
    "menfess_terlalu_panjang": "❌ Menfess terlalu panjang! Maksimal {batas} karakter ya. (Pesanmu saat ini: {panjang} karakter efektif).",
    "menfess_ada_mention": "❌ Menfess dilarang menyertakan mention atau username! (Link URL tetap diperbolehkan).",
    "menfess_fwa_wajib_umur": "❌ <b>Menfess Ditolak!</b>\n\nKarena kamu mengirim pencarian (FWA/Affection), kamu <b>WAJIB</b> menyertakan angka umur atau tahun kelahiran kamu (contoh: 18, 2006, 97, 06).\n\nSilakan perbaiki teksmu dan kirim ulang ya!",
    "menfess_hanya_teks": "❌ Sesi /auto sedang aktif! Kamu hanya diperbolehkan mengirim pesan teks saja (tanpa media).",
    "menfess_target_cooldown": "⏳ Target sedang dalam masa cooldown. Silakan gunakan username lain atau coba lagi dalam {sisa_menit} menit.",
    "menfess_username_invalid": "❌ Gagal! Username tidak boleh lebih dari 1 kata, tidak boleh ada spasi, atau karakter aneh (contoh: jake).\n\nSilakan kirim ulang pesan menfess kamu dari awal.",
    "menfess_username_bukan_teks": "❌ Gagal! Username harus berupa teks biasa. Silakan kirim ulang pesan menfess kamu dari awal.",
    "menfess_target_banned": "❌ Gagal! Pengguna dari username tujuan ini telah diblokir dari bot dan tidak dapat menerima menfess.",
    "menfess_terkirim": "Pesan kamu telah dikirim ke channel!{coin_msg}{vip_promo}",
    "cort_hanya_teks": "❌ Mode Anonymous Court hanya menerima teks cerita.",
    "cort_sukses": "✅ Kasusmu berhasil diajukan ke pengadilan channel!{coin_msg}",
    "cort_gagal": "❌ Gagal mengirim kasus ke channel.",
    "menfess_muted": "🔇 Ups! Kamu sedang dalam masa mute.\nSisa waktu: {sisa_hari} hari, {sisa_jam} jam, {sisa_menit} menit.",
    "menfess_balasan_sukses": "✅ Balasan anonim berhasil dikirim ke pengomentar!",
    "menfess_balasan_gagal": "❌ Gagal mengirim balasan anonim, mungkin komentar aslinya dihapus.",
    "komentar_notif": "📬 {commenter} berkomentar di menfess kamu!\n\n<b>(balas/reply pesan ini jika kamu ingin membalas komentarnya secara anonim)</b>\n\n<code>#ID:{msg_id}</code>",

    # === AUTO PROMOTE ===
    "sched_only_vip": "❌ Fitur <b>Auto Promote (Scheduled Menfess)</b> eksklusif untuk member 💎 VIP!",
    "sched_limit_max": "❌ Kamu sudah mencapai batas maksimal! (Max 3 Auto Promote).\n\nSilakan tunggu sampai jadwal yang tipe 'Sekali Saja' selesai tereksekusi.",
    "sched_cek_gagal": "❌ Terjadi kesalahan saat mengecek kuota Auto Promote kamu.",
    "sched_prompt_teks": "🗓️ <b>SETUP AUTO PROMOTE</b>\n\nSilakan kirimkan teks menfess yang ingin dijadwalkan:\n<b>(Maksimal 70 karakter, dilarang mention, support Premium Emoji!)</b>",
    "sched_terlalu_panjang": "❌ Teks terlalu panjang! Maksimal {batas} karakter (Efektif: {panjang}/{batas}).",
    "sched_dilarang_mention": "❌ Dilarang menyertakan mention atau username!",
    "sched_prompt_username": "Sip! Sekarang kirimkan *<b>username</b>* kamu untuk di-hyperlink (contoh: @jake):",
    "sched_username_invalid": "❌ Format username tidak valid.",
    "sched_prompt_waktu": "⏰ <b>Tentukan Jam Pengiriman</b>\n\nKirim format waktu WIB (contoh: 22:15 atau 07:30).\n⚠️ <b>Hanya berlaku dari jam 22:10 s/d 09:30 WIB!</b>",
    "sched_waktu_invalid": "❌ Format waktu salah. Gunakan HH:MM.",
    "sched_diluar_jam": "❌ Di luar jam operasional. Silakan jadwalkan antara jam *<b>22:10</b><b> sampai </b><b>09:30</b>* WIB.",
    "sched_prompt_freq": "Terakhir, seberapa sering Auto Promote ini dikirim?",
    "sched_pilih_tombol": "⚠️ Silakan pilih menggunakan tombol di bawah!",
    "sched_sukses": "✅ <b>Auto Promote Berhasil Dijadwalkan!</b>\n\nMenfess kamu akan dipromosikan jam <b>{waktu} WIB</b> lewat userbot.",
    "sched_gagal_simpan": "❌ Gagal menyimpan jadwal ke database.",
    "sched_vip_habis": "⚠️ Jadwal Auto Promote kamu dihapus karena masa aktif VIP sudah habis.",
    "sched_terkirim_notif": "✅ Jadwal Auto Promote kamu baru saja mengudara lewat bolehvip!",

    # === ANON CHAT ===
    "anon_pilih_umur": "👤 <b>SETUP PROFIL ANONIM</b>\n\n1️⃣ Pilih kategori umur kamu:",
    "anon_pilih_umur_invalid": "⚠️ Silakan gunakan tombol di bawah untuk memilih umur.",
    "anon_pilih_gender": "2️⃣ Pilih gender kamu:",
    "anon_pilih_gender_invalid": "⚠️ Silakan gunakan tombol di bawah untuk memilih gender.",
    "anon_pilih_orientasi": "3️⃣ Pilih orientasi kamu (maksimal 3 filter).\n\n<b>(Bisa tekan tombol di bawah, atau ketik manual jika lebih dari 1, contoh: bxg, bxb)</b>:",
    "anon_orientasi_invalid": "⚠️ Silakan pilih atau ketik orientasi yang valid (bxg, bxb, gxg, nbxnb).",
    "anon_orientasi_max": "⚠️ Maksimal 3 filter orientasi ya! Silakan ketik ulang.",
    "anon_profil_tersimpan": "✅ Profil tersimpan!\nUmur: <code>{age}</code>\nGender: <code>{gender}</code>\nOrientasi: <code>{ori}</code>\n\nKetik /search untuk mencari partner.",
    "anon_belum_setup": "Data belum tersimpan, ketik /start dulu ya.",
    "anon_belum_isi_profil": "Isi profil dulu yuk sebelum mencari partner menggunakan command /setprofile.",
    "anon_masih_dalam_sesi": "Kamu sedang dalam antrean atau obrolan. Ketik /stop untuk membatalkan.",
    "anon_match_sukses": "🎉 Partner ditemukan! Silakan mulai menyapa.\n\n<b>(Ketik /stop atau tekan tombol di bawah untuk mengakhiri obrolan dan kembali ke mode menfess)</b>",
    "anon_match_massal_sukses": "🎉 Partner massal ditemukan! Silakan mulai menyapa.\n\n<b>(Ketik /stop atau tekan tombol di bawah untuk mengakhiri obrolan dan kembali ke mode menfess)</b>",
    "anon_mencari": "🔍 Mencari partner yang cocok... (Maksimal tunggu 10 menit)",
    "anon_timeout_gagal": "Maaf yaa, ga ada partner yang sesuai dengan kriteria kamu saat ini. Coba cari lagi nanti ya!",
    "anon_stop_confirm": "⚠️ <b>Akhiri sesi dengan user ini?</b>",
    "anon_stop_none": "Kamu tidak sedang dalam sesi anonim.",
    "anon_stop_lanjut": "✅ Lanjut chatting!",
    "anon_left_self": "🔴 Kamu telah meninggalkan obrolan. (Kembali ke mode menfess)",
    "anon_left_partner": "🔴 Partner kamu telah meninggalkan obrolan. (Kembali ke mode menfess)",

    # === PHOTO LIVE ===
    "live_prompt": "📸 <b>Buat Photo Live</b>\nBiaya: <b>{harga} Kith-Coins</b>\n\nKirim/forward videonya ke sini (maks {max_durasi} detik, input maksimal {max_size} MB).{promo}",
    "live_bukan_video": "❌ Itu bukan video! Aksi dibatalkan.",
    "live_kirim_video_dulu": "Silakan kirim video terlebih dahulu.",
    "live_ffmpeg_missing": "❌ File FFmpeg static tidak ditemukan di folder bot.",
    "live_video_terlalu_besar": "❌ Video terlalu besar (Max {max_mb}MB).",
    "live_koin_kurang": "❌ Kith-Coins kurang (Biaya: {harga} Coins). Saldo: {saldo}{promo}",
    "live_cek_saldo_gagal": "❌ Terjadi kesalahan saat mengecek saldo. Silakan coba lagi nanti.",
    "live_processing": "⏳ Memproses Live Photo...{charge_text}\nTahap 1/4: download video",
    "live_sukses": "✅ Live Photo berhasil dibuat!",
    "live_gagal": "❌ Gagal memproses Live Photo:\n{error}{ulang_note}{refund_note}",

    # === EMOJI PACK ===
    "emoji_only_vip": "⛔ <b>Akses Ditolak!</b>\n\nMaaf, fitur cek ID Premium Custom Emoji ini eksklusif hanya untuk member 💎 VIP.\nSilakan upgrade ke Premium melalui menu <b>👤 Profile</b>.",
    "emoji_format_salah": "⚠️ <b>Format salah!</b>\nGunakan: <code>/cekemoji https://t.me/addemoji/NamaPack</code>",
    "emoji_pack_kosong": "❌ Pack ini kosong atau tidak ditemukan.",
    "emoji_bukan_pack": "❌ Pack ini tidak mengandung Premium Custom Emoji, hanya stiker biasa.",

    # === POLLING & GIVEAWAY ===
    "poll_sudah_isi": "⚠️ Kamu sudah mengisi polling ini. Setiap orang hanya bisa vote 1 kali!",
    "poll_sudah_isi_2": "⚠️ Kamu sudah mengisi polling ini.",
    "poll_tidak_ditemukan": "❌ Polling tidak ditemukan atau sudah ditutup.",
    "poll_tidak_aktif": "❌ Polling sudah tidak aktif.",
    "poll_prompt_isi": "✏️ Silakan masukkan pesan / isian untuk polling ini (Maksimal 150 karakter):",
    "poll_bukan_teks": "❌ Isian polling harus berupa teks biasa. Silakan kirim ulang:",
    "poll_terlalu_panjang": "❌ Pesan terlalu panjang ({panjang}/150 karakter). Silakan persingkat:",
    "poll_sukses": "✅ Isian polling kamu berhasil direkam dan sedang diupdate ke channel!",
    "ga_menang_coins": "🎉 <b>SELAMAT!</b>\nKamu menang giveaway dan mendapatkan hadiah sebesar <b>{val} Kith-Coins</b>! Hadiah sudah otomatis masuk ke saldomu. 🥳",
    "ga_menang_vip": "🎉 <b>SELAMAT!</b>\nKamu menang giveaway dan mendapatkan <b>Status Premium VIP selama {val} hari</b>! Nikmati diskon dan berbagai keuntungan lainnya. 🥳",
    "ga_join_sukses": "✅ Berhasil ikutan giveaway!",
    "ga_join_sudah": "Kamu sudah terdaftar di giveaway ini! Semoga beruntung 🍀",
    "ga_tidak_ditemukan": "Airdrop ini sudah berakhir atau tidak ditemukan!",
    "ads_enabled": "OFF",
    "ads_text": "📢 <b>Iklan Base</b>\n\nPasang iklan kamu di sini! Info lebih lanjut hubungi admin.",
}
CACHE_WORDINGS = {}

# Kategori dipakai buat navigasi 2 tingkat di /settings2 (biar gak kepanjangan 1 list doang)
WORDING_CATEGORIES = [
    ("umum", "👋 Umum", [
        ("welcome", "Teks Sambutan /start"),
        ("join_channel_prompt", "Prompt Wajib Join Channel"),
        ("sengaja_gagal", "Pesan Cancel"),
    ]),
    ("profil", "👤 Profil & Leaderboard", [
        ("profile_text", "Isi Kartu Profil"),
        ("profile_gagal", "Gagal Ambil Profil"),
        ("leaderboard_header", "Header Leaderboard"),
        ("leaderboard_footer", "Footer Leaderboard"),
        ("leaderboard_kosong", "Leaderboard Kosong"),
        ("leaderboard_gagal", "Leaderboard Gagal"),
    ]),
    ("vip", "💎 VIP", [
        ("vip_batal", "Pembelian Dibatalkan"),
        ("vip_menu_header", "Header Menu Paket"),
        ("vip_menu_desc", "Deskripsi Keuntungan VIP"),
        ("vip_checkout_caption", "Caption Checkout QRIS"),
        ("vip_acc_notif", "Notif VIP Disetujui"),
        ("vip_acc_gagal", "Gagal Proses ACC VIP"),
        ("vip_reject_notif", "Notif VIP Ditolak"),
        ("vip_receipt_diterima", "Bukti TF Diterima"),
        ("vip_receipt_bukan_foto", "Bukti TF Bukan Foto"),
        ("vip_promo_cooldown", "Promo VIP (saat Cooldown)"),
        ("vip_promo_title_live", "Promo VIP (Koin Kurang Title/Live)"),
        ("vip_promo_title_prompt", "Promo VIP (Prompt Beli Title/Live)"),
        ("vip_promo_auto_terkirim", "Promo VIP (Setelah Menfess Terkirim)"),
        ("vip_fallback_batal", "Fallback VIP Dibatalkan"),
        ("vip_fallback_sukses", "Fallback VIP Terkirim"),
    ]),
    ("title", "🏷️ Beli Title", [
        ("title_format_salah", "Format Command Salah"),
        ("title_max_char", "Title Terlalu Panjang"),
        ("title_kosong", "Title Kosong"),
        ("title_koin_kurang", "Koin Tidak Cukup"),
        ("title_sukses", "Transaksi Berhasil"),
        ("title_gagal_join_grup", "Gagal (Belum Join Grup)"),
        ("title_gagal_umum", "Gagal (Umum)"),
        ("title_db_error", "Error Database"),
        ("title_prompt_beli", "Prompt Beli Title"),
        ("title_only_dm", "Wajib di DM"),
        ("title_verified_alert", "Alert Terverifikasi Join"),
        ("title_verified_prompt", "Prompt Setelah Verifikasi Join"),
        ("title_belum_join_alert", "Alert Belum Join"),
    ]),
    ("menfess", "📨 Menfess", [
        ("menfess_bad_words", "Ditolak (Bad Words)"),
        ("menfess_base_closed", "Base Ditutup (Diteruskan)"),
        ("menfess_base_closed_gagal", "Base Ditutup (Gagal Terusin)"),
        ("menfess_manual_masuk_antrean", "Manual: Masuk Antrean"),
        ("menfess_manual_gagal_kirim", "Manual: Gagal Kirim ke Admin"),
        ("menfess_disetujui", "Manual: Disetujui Admin"),
        ("menfess_ditolak", "Manual: Ditolak Admin"),
        ("menfess_teks_diterima", "Auto: Teks Diterima, Minta Username"),
        ("menfess_cooldown_reguler", "Cooldown Reguler"),
        ("menfess_cooldown_vip", "Cooldown VIP"),
        ("menfess_terlalu_panjang", "Teks Terlalu Panjang"),
        ("menfess_ada_mention", "Ada Mention"),
        ("menfess_fwa_wajib_umur", "FWA Wajib Sertakan Umur"),
        ("menfess_hanya_teks", "Mode Auto Hanya Teks"),
        ("menfess_target_cooldown", "Target Cooldown"),
        ("menfess_username_invalid", "Format Username Invalid"),
        ("menfess_username_bukan_teks", "Username Bukan Teks"),
        ("menfess_target_banned", "Target Diblokir"),
        ("menfess_terkirim", "Menfess Terkirim (Auto)"),
        ("cort_hanya_teks", "CORT: Hanya Teks"),
        ("cort_sukses", "CORT: Sukses Diajukan"),
        ("cort_gagal", "CORT: Gagal Kirim"),
        ("menfess_muted", "Sedang Dimute"),
        ("menfess_balasan_sukses", "Balasan Anonim Sukses"),
        ("menfess_balasan_gagal", "Balasan Anonim Gagal"),
        ("komentar_notif", "Notif Ada Komentar Baru"),
    ]),
    ("autopromote", "🗓️ Auto Promote", [
        ("sched_only_vip", "Eksklusif VIP"),
        ("sched_limit_max", "Limit Maksimal Tercapai"),
        ("sched_cek_gagal", "Gagal Cek Kuota"),
        ("sched_prompt_teks", "Prompt Isi Teks"),
        ("sched_terlalu_panjang", "Teks Terlalu Panjang"),
        ("sched_dilarang_mention", "Dilarang Mention"),
        ("sched_prompt_username", "Prompt Username"),
        ("sched_username_invalid", "Username Invalid"),
        ("sched_prompt_waktu", "Prompt Jam Kirim"),
        ("sched_waktu_invalid", "Format Jam Salah"),
        ("sched_diluar_jam", "Di Luar Jam Operasional"),
        ("sched_prompt_freq", "Prompt Frekuensi"),
        ("sched_pilih_tombol", "Wajib Pilih Tombol"),
        ("sched_sukses", "Berhasil Dijadwalkan"),
        ("sched_gagal_simpan", "Gagal Simpan ke DB"),
        ("sched_vip_habis", "Dihapus (VIP Habis)"),
        ("sched_terkirim_notif", "Notif Terkirim ke Channel"),
    ]),
    ("anon", "🎭 Anon Chat", [
        ("anon_pilih_umur", "Prompt Pilih Umur"),
        ("anon_pilih_umur_invalid", "Pilih Umur Invalid"),
        ("anon_pilih_gender", "Prompt Pilih Gender"),
        ("anon_pilih_gender_invalid", "Pilih Gender Invalid"),
        ("anon_pilih_orientasi", "Prompt Pilih Orientasi"),
        ("anon_orientasi_invalid", "Orientasi Invalid"),
        ("anon_orientasi_max", "Orientasi Maks 3"),
        ("anon_profil_tersimpan", "Profil Tersimpan"),
        ("anon_belum_setup", "Belum /start"),
        ("anon_belum_isi_profil", "Belum Isi Profil"),
        ("anon_masih_dalam_sesi", "Masih Dalam Sesi"),
        ("anon_match_sukses", "Match Ditemukan"),
        ("anon_match_massal_sukses", "Match Massal Ditemukan"),
        ("anon_mencari", "Sedang Mencari Partner"),
        ("anon_timeout_gagal", "Timeout Gagal Dapat Partner"),
        ("anon_stop_confirm", "Konfirmasi Stop"),
        ("anon_stop_none", "Tidak Sedang Sesi"),
        ("anon_stop_lanjut", "Batal Stop (Lanjut Chat)"),
        ("anon_left_self", "Kamu Keluar Obrolan"),
        ("anon_left_partner", "Partner Keluar Obrolan"),
    ]),
    ("live", "📸 Photo Live", [
        ("live_prompt", "Prompt Buat Photo Live"),
        ("live_bukan_video", "Bukan Video"),
        ("live_kirim_video_dulu", "Kirim Video Dulu"),
        ("live_ffmpeg_missing", "FFmpeg Tidak Ditemukan"),
        ("live_video_terlalu_besar", "Video Terlalu Besar"),
        ("live_koin_kurang", "Koin Kurang"),
        ("live_cek_saldo_gagal", "Gagal Cek Saldo"),
        ("live_processing", "Sedang Memproses"),
        ("live_sukses", "Berhasil Dibuat"),
        ("live_gagal", "Gagal Diproses"),
    ]),
    ("emoji", "😀 Cek Emoji Pack", [
        ("emoji_only_vip", "Eksklusif VIP"),
        ("emoji_format_salah", "Format Command Salah"),
        ("emoji_pack_kosong", "Pack Kosong"),
        ("emoji_bukan_pack", "Bukan Pack Emoji Premium"),
    ]),
    # --- TAMBAHKAN BAGIAN INI ---
    ("ads", "📢 Pesan Sponsor / Ads", [
        ("ads_enabled", "Status Iklan (ON / OFF)"),
        ("ads_text", "Teks Iklan (Support HTML)"),
    ]),
    ("pollga", "📊 Polling & Giveaway", [
        ("poll_sudah_isi", "Sudah Vote (Deep Link)"),
        ("poll_sudah_isi_2", "Sudah Vote (Chat)"),
        ("poll_tidak_ditemukan", "Polling Tidak Ditemukan"),
        ("poll_tidak_aktif", "Polling Tidak Aktif Lagi"),
        ("poll_prompt_isi", "Prompt Isi Polling"),
        ("poll_bukan_teks", "Isian Bukan Teks"),
        ("poll_terlalu_panjang", "Isian Terlalu Panjang"),
        ("poll_sukses", "Isian Berhasil Direkam"),
        ("ga_menang_coins", "GA Menang (Koin)"),
        ("ga_menang_vip", "GA Menang (VIP)"),
        ("ga_join_sukses", "GA Berhasil Join"),
        ("ga_join_sudah", "GA Sudah Terdaftar"),
        ("ga_tidak_ditemukan", "GA Tidak Ditemukan"),
    ]),
]
WORDING_CAT_PAGE_SIZE = 8

# Guard flag: mencegah dua broadcast berjalan bersamaan yang bisa menyebabkan flood ban.
_broadcast_running = False


# ==============================================================================
# FIX: Semua panggilan Supabase (sinkron) dibungkus asyncio.to_thread() agar
#      tidak memblokir event loop asyncio. Ini adalah penyebab utama bot "stuck".
#      Retry diperkuat menjadi 3 percobaan dengan backoff progresif.
# ==============================================================================
async def db(fn):
    """
    Menjalankan callable Supabase sinkron di thread pool
    dengan retry + progressive backoff.
    """
    delays = [0.5, 1.0, 2.0]

    for attempt, delay in enumerate(delays):
        try:
            return await asyncio.to_thread(fn)

        except Exception as e:
            is_last_attempt = attempt == len(delays) - 1

            if is_last_attempt:
                logger.error(
                    f"❌ Supabase gagal permanen setelah "
                    f"{len(delays)} percobaan: {e}"
                )
                raise

            logger.warning(
                f"⚠️ Supabase gagal "
                f"(attempt {attempt + 1}/{len(delays)}): {e}. "
                f"Retry dalam {delay}s..."
            )

            await asyncio.sleep(delay)


# ============================================================
# TAMBAHAN: VIP CACHE SENTRALISASI (Step 1–3 dari diagnosis)
# ============================================================
VIP_CACHE = {}
VIP_CACHE_TTL = timedelta(minutes=5)

async def get_vip_status(user_id: int, force_refresh: bool = False):
    """
    Mengembalikan (is_vip, vip_until_str) untuk user_id.
    Menggunakan cache dengan TTL 5 menit dan fail‑safe ke cache lama jika DB error.
    """
    now = datetime.now(timezone.utc)
    cached = VIP_CACHE.get(user_id)
    if not force_refresh and cached and (now - cached["cached_at"]) < VIP_CACHE_TTL:
        return cached["is_vip"], cached["vip_until"]

    try:
        res = await db(lambda: supabase.table("users").select("is_vip, vip_until").eq("user_id", user_id).execute())
        row = res.data[0] if res.data else {}
        is_vip = row.get("is_vip", False)
        vip_until_str = row.get("vip_until")
        if is_vip and vip_until_str:
            vip_until_dt = datetime.fromisoformat(vip_until_str.replace("Z", "+00:00"))
            if now > vip_until_dt:
                is_vip = False
        VIP_CACHE[user_id] = {"is_vip": is_vip, "vip_until": vip_until_str, "cached_at": now}
        return is_vip, vip_until_str
    except Exception as e:
        logger.error(f"Gagal cek VIP {user_id}, pakai cache lama: {e}")
        if cached:
            return cached["is_vip"], cached["vip_until"]
        return False, None

def invalidate_vip_cache(user_id: int):
    """Panggil setelah update status VIP di database."""
    VIP_CACHE.pop(user_id, None)


# === CACHE LOADERS ===

async def update_settings_cache():
    global MENFESS_MODE, bot_active, POLL_DB
    try:
        response = await db(lambda: supabase.table("bot_settings").select("key, value").execute())
        if hasattr(response, 'data') and response.data:
            settings = {row["key"]: row["value"] for row in response.data}
            MENFESS_MODE = settings.get("menfess_mode", "auto")
            bot_active = settings.get("bot_active", "true").lower() != "false"
            
            # --- MUAT ULANG DATA POLLING DARI DATABASE ---
            for k, v in settings.items():
                if k.startswith("poll_"):
                    try:
                        poll_data = json.loads(v)
                        poll_data['voter_ids'] = set(poll_data.get('voter_ids', []))
                        poll_id = k.replace("poll_", "")
                        POLL_DB[poll_id] = poll_data
                    except Exception as e:
                        logger.error(f"Gagal meload poll {k}: {e}")
        else:
            await db(lambda: supabase.table("bot_settings").insert({"key": "menfess_mode", "value": "auto"}).execute())
            await db(lambda: supabase.table("bot_settings").insert({"key": "bot_active", "value": "true"}).execute())
            MENFESS_MODE = "auto"
            bot_active = True
    except Exception as e:
        logger.error(f"Gagal memuat setting bot: {e}")


def invalidate_vip_cache(user_id: int):
    VIP_CACHE.pop(user_id, None)


async def update_wordings_cache():
    global CACHE_WORDINGS
    try:
        response = await db(lambda: supabase.table("bot_settings").select("key, value").like("key", "wording_%").execute())
        CACHE_WORDINGS = {row["key"].replace("wording_", "", 1): row["value"] for row in (response.data or [])}
    except Exception as e:
        logger.error(f"Gagal load cache wording: {e}")

def get_wording(key: str, **kwargs) -> str:
    raw = CACHE_WORDINGS.get(key, WORDING_DEFAULTS.get(key, ""))
    try:
        return raw.format(**kwargs) if kwargs else raw
    except Exception:
        return raw

async def set_wording(key: str, value: str):
    CACHE_WORDINGS[key] = value
    await db(lambda: supabase.table("bot_settings").upsert({"key": f"wording_{key}", "value": value}).execute())

def render_wording(key: str, **kwargs) -> dict:
    text = get_wording(key, **kwargs)
    return {"text": text, "parse_mode": "HTML"}
        
def hitung_panjang_efektif(teks: str) -> int:
    if not teks:
        return 0
    diringkas = re.sub(r'\[\d{15,22}\]', '#', teks)
    return len(diringkas)

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

async def save_poll_to_db(poll_id, poll_data):
    """Simpan state polling ke Supabase agar aman saat restart."""
    try:
        data_to_save = poll_data.copy()
        data_to_save['voter_ids'] = list(data_to_save.get('voter_ids', []))
        
        await db(lambda: supabase.table("bot_settings").upsert({
            "key": f"poll_{poll_id}", 
            "value": json.dumps(data_to_save)
        }).execute())
    except Exception as e:
        logger.error(f"Gagal save poll {poll_id} ke database: {e}")


async def on_startup(application: Application):
    # Perbesar thread pool untuk request Supabase (sudah 50, tetapi kita pastikan)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=50,
            thread_name_prefix="supabase"
        )
    )

    try:
        me = await application.bot.get_me()
        logger.info(
            f"✅ Bot siap: @{me.username} (id={me.id})"
        )

        await check_system_tools()
        await update_settings_cache()
        await update_hashtags_cache()
        await update_wordings_cache()
        await update_badwords_cache()
        await update_required_channels_cache()
        await update_banned_users_cache()

        await start_userbot()

    except Exception as e:
        logger.error(
            f"⚠️ Gagal startup bot: {e}"
        )


async def save_required_channels(channels):
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

    try:
        # Ambil saldo & total coin, status VIP via cache
        is_vip, vip_until_str = await get_vip_status(user_id)
        res = await db(lambda: supabase.table("users").select("kith_coins, total_kith_coins").eq("user_id", user_id).execute())
        row = res.data[0] if res.data else {}
        coins = row.get("kith_coins") or 0
        total_coins = row.get("total_kith_coins") or coins

        # --- LOGIKA PENAMPILAN MASA AKTIF VIP ---
        if is_vip and vip_until_str:
            try:
                vip_until_dt = datetime.fromisoformat(vip_until_str.replace("Z", "+00:00"))
                vip_until_wib = vip_until_dt + timedelta(hours=7)
                if vip_until_wib.year >= 2090:
                    status_text = "💎 <b>VIP Member (Lifetime)</b>"
                else:
                    tanggal_str = vip_until_wib.strftime("%d-%m-%Y %H:%M")
                    status_text = f"💎 <b>VIP Member</b>\nBerakhir: <b>{tanggal_str} WIB</b>"
            except Exception:
                status_text = "💎 <b>VIP Member</b>"
        else:
            status_text = "👤 <b>Regular</b>"

        text = get_wording(
            "profile_text",
            user_id=user_id,
            status_text=status_text,
            coins=coins,
            total_coins=total_coins,
        )

        reply_markup = None
        if not is_vip:
            keyboard = [
                [InlineKeyboardButton("💎 Beli Premium", callback_data="buy_vip_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error cek profil: {e}")
        await update.message.reply_text(get_wording("profile_gagal"), parse_mode="HTML")


async def handle_checkjoin_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    try:
        member = await context.bot.get_chat_member(chat_id=GROUP_ID_DISKUSI, user_id=user_id)
        sudah_join = member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Gagal cek member grup diskusi (checkjoin_title): {e}")
        sudah_join = False

    if not sudah_join:
        await query.answer(get_wording("title_belum_join_alert"), show_alert=True)
        return

    await query.answer(get_wording("title_verified_alert"))
    context.user_data["keyboard_state"] = KEYBOARD_STATE_TITLE

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_wording("title_verified_prompt"),
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )
        

async def handle_vip_menu(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    # --- TANGKAP TOMBOL BATAL ---
    if query.data == "vip_cancel":
        context.user_data.pop("keyboard_state", None)
        context.user_data.pop("vip_pending_days", None)
        
        try:
            await query.message.delete()
        except Exception:
            pass
            
        await context.bot.send_message(
            chat_id=query.message.chat_id, 
            text=get_wording("vip_batal"),
            reply_markup=get_main_keyboard()
        , parse_mode="HTML")
        return

    # --- MENU PILIHAN PAKET ---
    if query.data == "buy_vip_menu":
        keyboard = [
            [InlineKeyboardButton("1 Bulan (Rp 5.000)", callback_data="vip_dur_30")],
            [InlineKeyboardButton("3 Bulan (Rp 12.000)", callback_data="vip_dur_90")],
            [InlineKeyboardButton("Lifetime (Rp 45.000)", callback_data="vip_dur_9999")],
            [InlineKeyboardButton("❌ Batal", callback_data="vip_cancel")]
        ]
        await query.edit_message_text(
            get_wording("vip_menu_header") + "\n\n" + get_wording("vip_menu_desc"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # --- CHECKOUT / QRIS MUNCUL ---
    if query.data.startswith("vip_dur_"):
        days = int(query.data.split("_")[2])
        price = 5000 if days == 30 else 12000 if days == 90 else 45000
        dur_text = "1 Bulan" if days == 30 else "3 Bulan" if days == 90 else "Lifetime"
        
        kode_unik = random.randint(100, 999)
        formatted_price = f"{price:,}".replace(",", ".")

        context.user_data["vip_pending_days"] = days
        context.user_data["keyboard_state"] = "WAITING_VIP_RECEIPT"

        qris_url = "https://kqixzfnndcqgyhkvclsu.supabase.co/storage/v1/object/public/Foto/Kode%20QRIS%20Decavstore,%20Situraja.png"
        
        caption = get_wording(
            "vip_checkout_caption",
            dur_text=dur_text,
            formatted_price=formatted_price,
            kode_unik=kode_unik,
        )
        
        keyboard = [[InlineKeyboardButton("❌ Batalkan Pembelian", callback_data="vip_cancel")]]
        
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=qris_url, 
            caption=caption, 
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# === FITUR LEADERBOARD ===
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        res = await db(lambda: supabase.table("users")
                       .select("user_id, kith_coins, total_kith_coins")
                       .order("total_kith_coins", desc=True)
                       .limit(10)
                       .execute())
        if not res.data:
            return await update.message.reply_text(get_wording("leaderboard_kosong"), parse_mode="HTML")

        text = get_wording("leaderboard_header")
        for i, row in enumerate(res.data):
            user_id = row.get("user_id")
            coins = row.get("total_kith_coins") if row.get("total_kith_coins") is not None else row.get("kith_coins", 0)
            
            try:
                chat = await context.bot.get_chat(user_id)
                display_name = chat.first_name
            except Exception:
                display_name = f"Pemain {user_id}"

            text += f'{i+1}. <a href="tg://user?id={user_id}">{html.escape(str(display_name))}</a> - <b>{coins}</b> Coins\n'

        text += get_wording("leaderboard_footer")
        
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Gagal memuat leaderboard: {e}")
        await update.message.reply_text(get_wording("leaderboard_gagal"), parse_mode="HTML")


def process_user_emojis(teks_asli: str, original_entities: list):
    import re
    from telegram import MessageEntity
    
    new_entities = []
    if original_entities:
        for ent in original_entities:
            new_entities.append(MessageEntity(
                type=ent.type,
                offset=ent.offset,
                length=ent.length,
                url=getattr(ent, 'url', None),
                user=getattr(ent, 'user', None),
                language=getattr(ent, 'language', None),
                custom_emoji_id=getattr(ent, 'custom_emoji_id', None)
            ))
            
    def utf16_len(s):
        return len(s.encode('utf-16-le')) // 2
        
    final_text = teks_asli
    matches = list(re.finditer(r'\[(\d{15,22})\]', teks_asli))
    matches.reverse()
    
    for match in matches:
        start_idx = match.start()
        end_idx = match.end()
        
        full_tag = match.group(0)
        emoji_id = match.group(1)
        
        prefix = teks_asli[:start_idx]
        match_offset = utf16_len(prefix)
        match_length = utf16_len(full_tag)
        
        placeholder = "✨"
        placeholder_length = utf16_len(placeholder)
        diff = match_length - placeholder_length
        
        final_text = final_text[:start_idx] + placeholder + final_text[end_idx:]
        
        updated_entities = []
        for ent in new_entities:
            new_offset = ent.offset
            new_length = ent.length
            
            if ent.offset > match_offset:
                new_offset -= diff
            elif ent.offset == match_offset and ent.length >= match_length:
                new_length -= diff
                
            updated_entities.append(MessageEntity(
                type=ent.type,
                offset=new_offset,
                length=new_length,
                url=getattr(ent, 'url', None),
                user=getattr(ent, 'user', None),
                language=getattr(ent, 'language', None),
                custom_emoji_id=getattr(ent, 'custom_emoji_id', None)
            ))
        new_entities = updated_entities
        
        new_entities.append(MessageEntity(
            type="custom_emoji",
            offset=match_offset,
            length=placeholder_length,
            custom_emoji_id=emoji_id
        ))
        
    return final_text, new_entities


async def cek_emoji_pack(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    # Gunakan get_vip_status
    is_vip, _ = await get_vip_status(user_id)
    if not is_vip:
        return await update.message.reply_text(**render_wording("emoji_only_vip"))

    if not context.args:
        return await update.message.reply_text(**render_wording("emoji_format_salah"))
        
    link = context.args[0]
    
    match = re.search(r'(?:addstickers/|addemoji/|set=)([a-zA-Z0-9_]+)', link, re.IGNORECASE)
    pack_name = match.group(1) if match else link.strip()
    
    status_msg = await update.message.reply_text(f"⏳ Menarik data pack: `{pack_name}`...", parse_mode="Markdown")
    
    def utf16_len(s):
        return len(s.encode('utf-16-le')) // 2
    
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
        
        if not sticker_set.stickers:
            return await status_msg.edit_text(get_wording("emoji_pack_kosong"))
            
        safe_title = re.sub(r'[_*`\[\]]', ' ', sticker_set.title)
        
        chunks = []
        header = f"📦 Pack: {safe_title}\n\n🎯 Daftar Custom Emoji ID:\n\n"
        current_text = header
        current_entities = []
        count = 0
        
        for sticker in sticker_set.stickers:
            emoji_id = getattr(sticker, 'custom_emoji_id', None)
            if not emoji_id:
                continue
            
            count += 1
            placeholder = "✨"
            prefix = f"{count}. "
            
            suffix_awal = " -> "
            suffix_akhir = "\n"
            
            line_len_estimate = len(prefix) + len(placeholder) + len(suffix_awal) + len(str(emoji_id)) + len(suffix_akhir)
            
            if len(current_text) + line_len_estimate > 3800:
                chunks.append((current_text, current_entities))
                current_text = f"(Lanjutan Pack: {safe_title})\n\n"
                current_entities = []
            
            offset_emoji = utf16_len(current_text + prefix)
            placeholder_length = utf16_len(placeholder)
            
            current_entities.append(MessageEntity(
                type=MessageEntity.CUSTOM_EMOJI,
                offset=offset_emoji,
                length=placeholder_length,
                custom_emoji_id=emoji_id
            ))
            
            offset_code = utf16_len(current_text + prefix + placeholder + suffix_awal)
            code_length = utf16_len(str(emoji_id))
            
            current_entities.append(MessageEntity(
                type=MessageEntity.CODE,
                offset=offset_code,
                length=code_length
            ))
            
            current_text += f"{prefix}{placeholder}{suffix_awal}{emoji_id}{suffix_akhir}"
        
        if current_text:
            chunks.append((current_text, current_entities))
            
        if count == 0:
            return await status_msg.edit_text(get_wording("emoji_bukan_pack"))
        
        first_text, first_entities = chunks[0]
        try:
            await status_msg.edit_text(text=first_text, entities=first_entities)
        except Exception as e:
            logger.warning(f"edit_text dengan entities gagal, fallback delete+reply: {e}")
            try:
                await status_msg.delete()
            except Exception:
                pass
            await update.message.reply_text(text=first_text, entities=first_entities)
        
        for chunk_text, chunk_entities in chunks[1:]:
            await update.message.reply_text(text=chunk_text, entities=chunk_entities)
            await asyncio.sleep(0.5)
            
    except Exception as e:
        logger.error(f"Gagal cek emoji pack: {e}")
        await status_msg.edit_text(
            f"❌ Gagal mengambil data: `{e}`\nPastikan link benar dan pack tersebut adalah pack Custom Emoji.",
            parse_mode="Markdown"
        )


# === FITUR BELI TITLE ===
async def kirim_gagal_title_join_grup(update: Update, context: ContextTypes.DEFAULT_TYPE, is_not_participant: bool):
    if is_not_participant:
        group_id_str = str(GROUP_ID_DISKUSI).replace("-100", "", 1)
        invite_link = f"https://t.me/c/{group_id_str}/1"
        keyboard = [
            [InlineKeyboardButton("💬 Join Grup Diskusi", url=invite_link)],
            [InlineKeyboardButton("✅ Sudah Join", callback_data="checkjoin_title")]
        ]
        await update.message.reply_text(
            get_wording("title_gagal_join_grup"),
            reply_markup=InlineKeyboardMarkup(keyboard)
        , parse_mode="HTML")
    else:
        await update.message.reply_text(
            get_wording("title_gagal_umum"),
            reply_markup=get_main_keyboard()
        , parse_mode="HTML")


async def buy_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return await update.message.reply_text(get_wording("title_only_dm"), parse_mode="HTML")

    user_id = update.effective_user.id

    if not context.args:
        return await update.message.reply_text(
            get_wording("title_format_salah", title_price=TITLE_PRICE),
            parse_mode="HTML"
        )

    new_title = " ".join(context.args)

    if len(new_title) > 16:
        return await update.message.reply_text(get_wording("title_max_char"), parse_mode="HTML")

    try:
        # Ambil status VIP via cache
        is_vip, _ = await get_vip_status(user_id)
        response = await db(lambda: supabase.table("users").select("kith_coins").eq("user_id", user_id).execute())
        row = response.data[0] if response.data else {}
        current_balance = row.get("kith_coins") or 0

        actual_price = TITLE_PRICE // 2 if is_vip else TITLE_PRICE

        if current_balance < actual_price:
            promo = get_wording("vip_promo_title_live") if not is_vip else ""
            return await update.message.reply_text(
                get_wording("title_koin_kurang", saldo=current_balance, harga=actual_price, promo=promo),
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

        new_balance = current_balance - actual_price
        await db(lambda: supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", user_id).execute())
        try:
            await context.bot.set_chat_member_tag(
                chat_id=GROUP_ID_DISKUSI,
                user_id=user_id,
                tag=new_title
            )
            await update.message.reply_text(
                get_wording("title_sukses", new_title=new_title, new_balance=new_balance),
                parse_mode="HTML"
            )
            
            try:
                log_text = (
                    f"🏷️ *Log Custom Title*\n"
                    f"👤 Pengguna: {update.effective_user.first_name}\n"
                    f"🆔 ID: `{user_id}`\n"
                    f"🏷️ Title: `{new_title}`\n"
                    f"💰 Biaya: {actual_price} Coins"
                )
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    message_thread_id=TOPIC_ID_ANON_LOG,
                    text=log_text,
                    parse_mode="Markdown"
                )
            except Exception as log_err:
                logger.error(f"Gagal kirim log title: {log_err}")
                
        except Exception as telegram_err:
            await db(lambda: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute())
            logger.error(f"Gagal set title Telegram: {telegram_err}")
            err_str = str(telegram_err).lower()
            is_not_participant = "not_participant" in err_str or "not a member" in err_str or "participant_id_invalid" in err_str
            await kirim_gagal_title_join_grup(update, context, is_not_participant)

    except Exception as db_err:
        logger.error(f"Error Database saat beli title: {db_err}")
        await update.message.reply_text(get_wording("title_db_error"), parse_mode="HTML")


async def _apply_title_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, new_title: str):
    user_id = update.effective_user.id

    if len(new_title) > 16:
        return await update.message.reply_text(get_wording("title_max_char"), reply_markup=get_main_keyboard(), parse_mode="HTML")

    try:
        # Ambil status VIP via cache
        is_vip, _ = await get_vip_status(user_id)
        response = await db(lambda: supabase.table("users").select("kith_coins").eq("user_id", user_id).execute())
        row = response.data[0] if response.data else {}
        current_balance = row.get("kith_coins") or 0

        actual_price = TITLE_PRICE // 2 if is_vip else TITLE_PRICE

        if current_balance < actual_price:
            promo = get_wording("vip_promo_title_live") if not is_vip else ""
            return await update.message.reply_text(
                get_wording("title_koin_kurang", saldo=current_balance, harga=actual_price, promo=promo),
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

        new_balance = current_balance - actual_price
        await db(lambda: supabase.table("users").update({"kith_coins": new_balance}).eq("user_id", user_id).execute())

        try:
            await context.bot.set_chat_member_tag(
                chat_id=GROUP_ID_DISKUSI,
                user_id=user_id,
                tag=new_title
            )
            await update.message.reply_text(
                get_wording("title_sukses", new_title=new_title, new_balance=new_balance),
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )
        except Exception as telegram_err:
            await db(lambda: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute())
            logger.error(f"Gagal set title Telegram: {telegram_err}")
            err_str = str(telegram_err).lower()
            is_not_participant = "not_participant" in err_str or "not a member" in err_str or "participant_id_invalid" in err_str
            await kirim_gagal_title_join_grup(update, context, is_not_participant)

    except Exception as db_err:
        logger.error(f"Error Database saat beli title: {db_err}")
        await update.message.reply_text(get_wording("title_db_error"), reply_markup=get_main_keyboard(), parse_mode="HTML")


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


async def set_mode_cort(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    global MENFESS_MODE
    MENFESS_MODE = "cort"
    try:
        await db(lambda: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "cort"}).execute())
    except Exception as e:
        logger.error(f"Gagal simpan mode cort ke DB: {e}")
    await update.message.reply_text("⚖️ Mode menfess diubah ke *CORT*. Semua pesan menfess akan langsung terkirim sebagai Anonymous Court.", parse_mode="Markdown")


# === HASHTAG & SETTINGS LAINNYA ===
def _wording_cat_lookup(cat_key):
    for key, label, items in WORDING_CATEGORIES:
        if key == cat_key:
            return label, items
    return None, []

def wording_category_keyboard():
    rows = []
    for i in range(0, len(WORDING_CATEGORIES), 2):
        chunk = WORDING_CATEGORIES[i:i+2]
        rows.append([InlineKeyboardButton(label, callback_data=f"wset|cat|{key}|0") for key, label, _ in chunk])
    rows.append([InlineKeyboardButton("✅ Selesai", callback_data="wset|done")])
    return InlineKeyboardMarkup(rows)

def wording_item_keyboard(cat_key: str, page: int = 0):
    label, items = _wording_cat_lookup(cat_key)
    pages = max(1, (len(items) + WORDING_CAT_PAGE_SIZE - 1) // WORDING_CAT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    page_items = items[page*WORDING_CAT_PAGE_SIZE:(page+1)*WORDING_CAT_PAGE_SIZE]
    rows = [[InlineKeyboardButton(item_label, callback_data=f"wset|edit|{cat_key}|{key}|{page}")] for key, item_label in page_items]
    rows.append([
        InlineKeyboardButton("⬅️", callback_data=f"wset|cat|{cat_key}|{max(0,page-1)}"),
        InlineKeyboardButton(f"{page+1}/{pages}", callback_data="wset|noop"),
        InlineKeyboardButton("➡️", callback_data=f"wset|cat|{cat_key}|{min(pages-1,page+1)}"),
    ])
    rows.append([
        InlineKeyboardButton("↩️ Kategori Lain", callback_data="wset|back"),
        InlineKeyboardButton("✅ Selesai", callback_data="wset|done"),
    ])
    return InlineKeyboardMarkup(rows)

WORDING_HOME_TEXT = (
    "⚙️ <b>PENGATURAN WORDING BOT</b>\n\n"
    "Pilih kategori teks yang mau diubah.\n\n"
    "<i>Tips: pas ngetik teks baru, kamu bisa langsung pakai Bold/Italic/Spoiler dari toolbar format Telegram "
    "(select teks → tekan B/I), dan tempel emoji premium langsung dari keyboard emoji — semuanya otomatis kebawa "
    "karena akun Telegram kamu Premium. Gak perlu ketik simbol markdown atau ID emoji manual.</i>"
)

WORDING_PLACEHOLDER_HINTS = {
    "profile_text": "{user_id} {status_text} {coins} {total_coins}",
    "vip_checkout_caption": "{dur_text} {formatted_price} {kode_unik}",
    "title_format_salah": "{title_price}",
    "title_koin_kurang": "{saldo} {harga} {promo}",
    "title_sukses": "{new_title} {new_balance}",
    "title_prompt_beli": "{harga} {promo}",
    "menfess_disetujui": "{status_text} {coin_msg}",
    "menfess_cooldown_reguler": "{sisa_menit} {vip_promo}",
    "menfess_cooldown_vip": "{sisa_menit}",
    "menfess_terlalu_panjang": "{panjang} {batas}",
    "menfess_target_cooldown": "{sisa_menit}",
    "menfess_terkirim": "{coin_msg} {vip_promo}",
    "cort_sukses": "{coin_msg}",
    "menfess_muted": "{sisa_hari} {sisa_jam} {sisa_menit}",
    "komentar_notif": "{commenter} {msg_id}",
    "sched_terlalu_panjang": "{panjang}",
    "sched_sukses": "{waktu}",
    "anon_profil_tersimpan": "{age} {gender} {ori}",
    "live_prompt": "{harga} {max_durasi} {max_size} {promo}",
    "live_video_terlalu_besar": "{max_mb}",
    "live_koin_kurang": "{harga} {saldo} {promo}",
    "live_processing": "{charge_text}",
    "live_gagal": "{error} {ulang_note} {refund_note}",
    "poll_terlalu_panjang": "{panjang}",
    "ga_menang_coins": "{val}",
    "ga_menang_vip": "{val}",
    "vip_fallback_sukses": "{coin_msg}",
    "vip_promo_title_prompt": "{harga_vip}",
}

async def cmd_settings_wording(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    await update.message.reply_text(
        WORDING_HOME_TEXT, parse_mode="HTML", reply_markup=wording_category_keyboard()
    )

async def handle_wording_settings_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action = parts[1]

    if action == "noop":
        return
    if action == "done":
        context.user_data.pop("editing_wording_key", None)
        return await query.edit_message_text("✅ Pengaturan wording ditutup.")
    if action == "back":
        return await query.edit_message_text(WORDING_HOME_TEXT, parse_mode="HTML", reply_markup=wording_category_keyboard())
    if action == "cat":
        cat_key, page = parts[2], int(parts[3])
        label, _ = _wording_cat_lookup(cat_key)
        return await query.edit_message_text(
            f"⚙️ <b>PENGATURAN WORDING</b> — {html.escape(label)}\n\nPilih teks yang mau diubah:",
            parse_mode="HTML", reply_markup=wording_item_keyboard(cat_key, page)
        )
    if action == "edit":
        cat_key, key, page = parts[2], parts[3], int(parts[4])
        context.user_data["editing_wording_key"] = key
        context.user_data["editing_wording_cat"] = cat_key
        context.user_data["editing_wording_page"] = page
        current_raw = CACHE_WORDINGS.get(key, WORDING_DEFAULTS.get(key, "-"))
        placeholder_hint = WORDING_PLACEHOLDER_HINTS.get(key)
        hint_line = f"\n\n🔧 Placeholder yang tersedia: <code>{html.escape(placeholder_hint)}</code>" if placeholder_hint else ""
        await query.edit_message_text(
            f"✏️ Kirim teks baru untuk <b>{html.escape(key)}</b>.\n\n"
            f"👁️ Preview tampilan sekarang:\n{current_raw}\n\n"
            f"📄 Kode sumbernya:\n<pre>{html.escape(current_raw)}</pre>"
            f"{hint_line}\n\n"
            f"<i>Ketik/tempel teks baru seperti biasa (bold/italic/emoji premium ikut kebawa otomatis). "
            f"Ketik /cancel untuk batal.</i>",
            parse_mode="HTML"
        )

async def handle_wording_text_input(update: Update, context: CallbackContext) -> bool:
    key = context.user_data.get("editing_wording_key")
    if not key or not update.message:
        return False
    new_value = update.message.text_html or update.message.text
    if not new_value:
        return False
    cat_key = context.user_data.get("editing_wording_cat")
    page = context.user_data.get("editing_wording_page", 0)
    await set_wording(key, new_value)
    context.user_data.pop("editing_wording_key", None)
    context.user_data.pop("editing_wording_cat", None)
    context.user_data.pop("editing_wording_page", None)
    reply_markup = wording_item_keyboard(cat_key, page) if cat_key else None
    await update.message.reply_text(
        f"✅ Wording <b>{html.escape(key)}</b> berhasil diperbarui!\n\n👁️ Preview:\n{new_value}",
        parse_mode="HTML", reply_markup=reply_markup
    )
    return True

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


async def bg_save_user(user_id: int, username: str, is_active: bool = True):
    try:
        data_to_upsert = {"user_id": user_id, "is_active": is_active}
        if username:
            data_to_upsert["username"] = username.lower()
            
        await db(lambda: supabase.table("users").upsert(data_to_upsert, on_conflict=["user_id"]).execute())
    except Exception as e:
        logger.error(f"Gagal background save user ID {user_id}: {e}")

async def global_user_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
        
    user_id = update.effective_user.id
    raw_username = update.effective_user.username
    current_username = raw_username.lower() if raw_username else None
    
    if (user_id not in CACHE_USERNAMES or 
        CACHE_USERNAMES.get(user_id) != current_username or 
        user_id in CACHE_PASSIVE_USERS):
        
        CACHE_USERNAMES[user_id] = current_username
        CACHE_PASSIVE_USERS.discard(user_id)
        
        asyncio.create_task(bg_save_user(user_id, current_username, is_active=True))


def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💬 Beli title")],
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

    # --- TANGKAP DEEP LINK POLLING ---
    if context.args and context.args[0].startswith("poll_"):
        poll_id = context.args[0].split("_")[1]
        
        if poll_id not in POLL_DB:
            await update.message.reply_text(get_wording("poll_tidak_ditemukan"), parse_mode="HTML")
            return
            
        if user_id in POLL_DB[poll_id]["voter_ids"]:
            await update.message.reply_text(get_wording("poll_sudah_isi"), parse_mode="HTML")
            return
            
        context.user_data['filling_poll_id'] = poll_id
        await update.message.reply_text(get_wording("poll_prompt_isi"), parse_mode="HTML")
        return

    if await check_subscription(user_id, context):
        await update.message.reply_text(**render_wording("welcome"), reply_markup=get_main_keyboard())
    else:
        keyboard = [[InlineKeyboardButton("Join Channels", url=f"https://telegram.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text(get_wording("join_channel_prompt"), reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="HTML")


# === ALUR MENFESS ===
async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE

    if not update.effective_user or not update.message:
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    user_id = update.effective_user.id
    
    # --- CEGAT PENGISIAN BOARD POLLING ---
    poll_id = context.user_data.get('filling_poll_id')
    if poll_id:
        text = update.message.text
        if not text:
            await update.message.reply_text(get_wording("poll_bukan_teks"), parse_mode="HTML")
            return ConversationHandler.END
            
        if len(text) > 150:
            await update.message.reply_text(get_wording("poll_terlalu_panjang", panjang=len(text)), parse_mode="HTML")
            return ConversationHandler.END
            
        poll = POLL_DB.get(poll_id)
        if not poll:
            await update.message.reply_text(get_wording("poll_tidak_aktif"), parse_mode="HTML")
            context.user_data.pop('filling_poll_id', None)
            return ConversationHandler.END
            
        if user_id in poll['voter_ids']:
            await update.message.reply_text(get_wording("poll_sudah_isi_2"), parse_mode="HTML")
            context.user_data.pop('filling_poll_id', None)
            return ConversationHandler.END
            
        poll['voter_ids'].add(user_id)
        display_name = update.effective_user.first_name if not poll['anon'] else "☁️"
        poll['votes'].append({"user_id": user_id, "name": display_name, "text": text})
        
        await save_poll_to_db(poll_id, poll)
        
        keyboard_log = [[InlineKeyboardButton("🗑️ Hapus Vote Ini", callback_data=f"delvote|{poll_id}|{user_id}")]]
        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,                 
                message_thread_id=TOPIC_ID_POLL_LOG,     
                text=f"📋 *LOG BOARD POLLING*\n"
                     f"**Poll ID:** `{poll_id}`\n"
                     f"**Judul:** {poll['judul']}\n"
                     f"**Pengirim:** [{update.effective_user.first_name}](tg://user?id={user_id}) (`{user_id}`)\n"
                     f"**Isi:** {text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard_log)
            )
        except Exception as e:
            logger.error(f"Gagal kirim log poll: {e}")
            
        await update.message.reply_text(get_wording("poll_sukses"), reply_markup=get_main_keyboard(), parse_mode="HTML")
        context.user_data.pop('filling_poll_id', None)
        
        job_name = f"update_poll_{poll_id}"
        if not context.job_queue.get_jobs_by_name(job_name):
            context.job_queue.run_once(update_poll_board, when=5, data=poll_id, name=job_name)
            
        return ConversationHandler.END
    # ----------------------------------------

    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    display_name = f"@{username}" if username else first_name

    if update.message.text == "🛑 Stop Anon":
        await stop_anon(update, context)
        return ConversationHandler.END
    
    if update.message.text == "❌ Cancel":
        context.user_data.clear()
        await update.message.reply_text(get_wording("sengaja_gagal"), reply_markup=get_main_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    # --- CEGAT LOGIKA ANON CHAT DI SINI ---
    res = await db(lambda: supabase.table("users").select("chat_state, partner_id").eq("user_id", user_id).execute())
    user_state = res.data[0].get("chat_state") if res.data else "menfess"
    
    if user_state == "chatting_admin":
        header_msg = await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"💬 #AnonFallback\nDari ID: `{user_id}`\n*(Reply pesan ini untuk membalas ke user)*",
            parse_mode="Markdown"
        )
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
        
    # --- CEK STATUS MUTE ---
    try:
        res_mute = await db(lambda: supabase.table("users").select("muted_until").eq("user_id", user_id).execute())
        if res_mute.data and res_mute.data[0].get("muted_until"):
            muted_until_str = res_mute.data[0]["muted_until"]
            muted_until_dt = datetime.fromisoformat(muted_until_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            
            if now_utc < muted_until_dt:
                sisa = muted_until_dt - now_utc
                jam, sisa_detik = divmod(sisa.seconds, 3600)
                menit, _ = divmod(sisa_detik, 60)
                await update.message.reply_text(get_wording("menfess_muted", sisa_hari=sisa.days, sisa_jam=jam, sisa_menit=menit), parse_mode="HTML")
                return ConversationHandler.END
            else:
                await db(lambda: supabase.table("users").update({"muted_until": None}).eq("user_id", user_id).execute())
    except Exception as e:
        logger.error(f"Gagal mengecek status mute: {e}")

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
                await update.message.reply_text(get_wording("menfess_balasan_sukses"), parse_mode="HTML")
            except Exception as e:
                logger.error(f"Gagal memproses balasan anonim: {e}")
                await update.message.reply_text(get_wording("menfess_balasan_gagal"), parse_mode="HTML")
            return ConversationHandler.END

    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://telegram.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text(get_wording("join_channel_prompt"), reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="HTML")
        return ConversationHandler.END

    pesan_teks = update.message.text or update.message.caption or ""
    pesan_teks_lower = pesan_teks.lower()
    keyboard_state = context.user_data.get("keyboard_state")

    # --- TANGKAP BUKTI TF VIP ---
    if keyboard_state == "WAITING_VIP_RECEIPT":
        if not update.message.photo and not update.message.document:
            await update.message.reply_text(get_wording("vip_receipt_bukan_foto"), parse_mode="HTML")
            return ConversationHandler.END

        pending_days = context.user_data.get("vip_pending_days", 30)
        
        keyboard_admin = [
            [InlineKeyboardButton("✅ Acc VIP", callback_data=f"vipacc|{user_id}|{pending_days}")],
            [InlineKeyboardButton("❌ Tolak", callback_data=f"viprej|{user_id}")]
        ]
        
        await context.bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            message_thread_id=TOPIC_ID_VIP_LOG,
            from_chat_id=user_id,
            message_id=update.message.message_id,
            caption=f"🚨 *REVIEW BUKTI TF VIP*\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`\n⏳ Paket: {pending_days} Hari",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard_admin)
        )
        
        context.user_data.clear()
        await update.message.reply_text(get_wording("vip_receipt_diterima"), reply_markup=get_main_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    if keyboard_state == KEYBOARD_STATE_TITLE:
        context.user_data.pop("keyboard_state", None)
        new_title = (update.message.text or "").strip()
        if not new_title:
            await update.message.reply_text(get_wording("title_kosong"), reply_markup=get_main_keyboard(), parse_mode="HTML")
            return ConversationHandler.END
        await _apply_title_purchase(update, context, new_title)
        return ConversationHandler.END

    if keyboard_state == KEYBOARD_STATE_LIVE:
        context.user_data.pop("keyboard_state", None)
        if not _get_video_file_from_message(update.message):
            await update.message.reply_text(get_wording("live_bukan_video"), reply_markup=get_main_keyboard(), parse_mode="HTML")
            return ConversationHandler.END
        await live_photo_handler(update, context)
        return ConversationHandler.END

    # --- LOGIKA KEYBOARD STATE ANON PROFILE ---
    if keyboard_state == "ANON_AGE":
        if pesan_teks not in ["Legal (≥ 18)", "Minor (< 18)"]:
            await update.message.reply_text(get_wording("anon_pilih_umur_invalid"), parse_mode="HTML")
            return ConversationHandler.END
            
        context.user_data['anon_age'] = "legal" if "Legal" in pesan_teks else "minor"
        context.user_data["keyboard_state"] = "ANON_GENDER"
        
        keyboard = [
            [KeyboardButton("Male ♂️"), KeyboardButton("Female ♀️")],
            [KeyboardButton("❌ Cancel")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(get_wording("anon_pilih_gender"), reply_markup=reply_markup, parse_mode="HTML")
        return ConversationHandler.END

    if keyboard_state == "ANON_GENDER":
        if pesan_teks not in ["Male ♂️", "Female ♀️"]:
            await update.message.reply_text(get_wording("anon_pilih_gender_invalid"), parse_mode="HTML")
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
            get_wording("anon_pilih_orientasi"),
            reply_markup=reply_markup
        , parse_mode="HTML")
        return ConversationHandler.END

    if keyboard_state == "ANON_ORI":
        valid_oris = ["bxg", "bxb", "gxg", "nbxnb"]
        user_oris = [o for o in valid_oris if o in pesan_teks_lower]
        
        if not user_oris:
            await update.message.reply_text(get_wording("anon_orientasi_invalid"), parse_mode="HTML")
            return ConversationHandler.END
            
        if len(user_oris) > 3:
            await update.message.reply_text(get_wording("anon_orientasi_max"), parse_mode="HTML")
            return ConversationHandler.END
            
        ori = ",".join(user_oris)
        age = context.user_data.get('anon_age')
        gender = context.user_data.get('anon_gen')
        
        await db(lambda: supabase.table("users").update({
            "age_group": age, 
            "gender": gender, 
            "orientation": ori
        }).eq("user_id", user_id).execute())
        
        context.user_data.pop("keyboard_state", None)
        
        await update.message.reply_text(
            get_wording("anon_profil_tersimpan", age=age, gender=gender, ori=ori),
            parse_mode="HTML",
            reply_markup=get_main_keyboard() 
        )
        return ConversationHandler.END
        
    # --- LOGIKA TOMBOL MAIN KEYBOARD ---
    if update.message.text == "👤 Profile":
        await cek_profile(update, context)
        return ConversationHandler.END

    if update.message.text == "💬 Beli title":
        context.user_data["keyboard_state"] = KEYBOARD_STATE_TITLE
        
        is_vip, _ = await get_vip_status(user_id)
        harga = TITLE_PRICE // 2 if is_vip else TITLE_PRICE
        promo = "" if is_vip else get_wording("vip_promo_title_prompt", harga_vip=TITLE_PRICE // 2)

        await update.message.reply_text(
            get_wording("title_prompt_beli", harga=harga, promo=promo),
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
        return ConversationHandler.END

    if update.message.text == "📸 Photo live":
        context.user_data["keyboard_state"] = KEYBOARD_STATE_LIVE
        
        is_vip, _ = await get_vip_status(user_id)
        harga = LIVE_PHOTO_PRICE // 2 if is_vip else LIVE_PHOTO_PRICE
        promo = "" if is_vip else get_wording("vip_promo_title_prompt", harga_vip=LIVE_PHOTO_PRICE // 2)

        await update.message.reply_text(
            get_wording("live_prompt", harga=harga, max_durasi=f"{LIVE_MAX_DURATION:.0f}", max_size=LIVE_MAX_INPUT_FILE_SIZE_MB, promo=promo),
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
        return ConversationHandler.END

    if update.message.text == "🎭 Set Profile Anon":
        await set_profile(update, context)
        return ConversationHandler.END

    if update.message.text == "🔍 Cari Partner Anon":
        await search_anon(update, context)
        return ConversationHandler.END

    if update.message.text == "🗓️ Auto Promote":
        is_vip, _ = await get_vip_status(user_id)
        if not is_vip:
            await update.message.reply_text(get_wording("sched_only_vip"), parse_mode="HTML")
            return ConversationHandler.END

        try:
            res_sched = await db(lambda: supabase.table("scheduled_menfess").select("id").eq("user_id", user_id).execute())
            active_count = len(res_sched.data) if hasattr(res_sched, 'data') and res_sched.data else 0
            
            if active_count >= 3:
                await update.message.reply_text(get_wording("sched_limit_max"), parse_mode="HTML")
                return ConversationHandler.END
        except Exception as e:
            logger.error(f"Gagal cek kuota schedule: {e}")
            await update.message.reply_text(get_wording("sched_cek_gagal"), parse_mode="HTML")
            return ConversationHandler.END

        context.user_data["keyboard_state"] = "WAITING_SCHED_TEXT"
        await update.message.reply_text(get_wording("sched_prompt_teks"), parse_mode="HTML", reply_markup=get_cancel_keyboard())
        return ConversationHandler.END

    if keyboard_state == "WAITING_SCHED_TEXT":
        panjang_efektif = hitung_panjang_efektif(pesan_teks)
        if panjang_efektif > 300:
            await update.message.reply_text(get_wording("sched_terlalu_panjang", panjang=panjang_efektif), parse_mode="HTML")
            return ConversationHandler.END
            
        if re.search(r'(?:^|\s)@/?\w+', pesan_teks):
            await update.message.reply_text(get_wording("sched_dilarang_mention"), parse_mode="HTML")
            return ConversationHandler.END
            
        context.user_data["sched_text"] = update.message.text
        
        ent_list = []
        if update.message.entities:
            for ent in update.message.entities:
                if ent.type not in ["url", "text_link"]:
                    ent_list.append(ent.to_dict())
                    
        context.user_data["sched_entities"] = ent_list
        
        context.user_data["keyboard_state"] = "WAITING_SCHED_USERNAME"
        await update.message.reply_text(get_wording("sched_prompt_username"), parse_mode="HTML")
        return ConversationHandler.END

    if keyboard_state == "WAITING_SCHED_USERNAME":
        target = pesan_teks.strip().replace("@", "")
        if not re.match(r"^[a-zA-Z0-9_]+$", target):
            await update.message.reply_text(get_wording("sched_username_invalid"), parse_mode="HTML")
            return ConversationHandler.END
        
        context.user_data["sched_username"] = target
        context.user_data["keyboard_state"] = "WAITING_SCHED_TIME"
        await update.message.reply_text(get_wording("sched_prompt_waktu"), parse_mode="HTML")
        return ConversationHandler.END

    if keyboard_state == "WAITING_SCHED_TIME":
        time_str = pesan_teks.strip()
        match = re.match(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$", time_str)
        if not match:
            await update.message.reply_text(get_wording("sched_waktu_invalid"), parse_mode="HTML")
            return ConversationHandler.END
            
        h, m = int(match.group(1)), int(match.group(2))
        is_valid = False
        if (h == 22 and m >= 10) or h == 23:
            is_valid = True
        elif 0 <= h <= 8:
            is_valid = True
        elif h == 9 and m <= 30:
            is_valid = True
            
        if not is_valid:
            await update.message.reply_text(get_wording("sched_diluar_jam"), parse_mode="HTML")
            return ConversationHandler.END
            
        context.user_data["sched_time"] = f"{h:02d}:{m:02d}"
        context.user_data["keyboard_state"] = "WAITING_SCHED_FREQ"
        
        kb_freq = [[KeyboardButton("Sekali Saja"), KeyboardButton("Sepanjang VIP")], [KeyboardButton("❌ Cancel")]]
        await update.message.reply_text(get_wording("sched_prompt_freq"), reply_markup=ReplyKeyboardMarkup(kb_freq, resize_keyboard=True, one_time_keyboard=True), parse_mode="HTML")
        return ConversationHandler.END

    if keyboard_state == "WAITING_SCHED_FREQ":
        if pesan_teks not in ["Sekali Saja", "Sepanjang VIP"]:
            await update.message.reply_text(get_wording("sched_pilih_tombol"), parse_mode="HTML")
            return ConversationHandler.END
            
        freq = "once" if pesan_teks == "Sekali Saja" else "daily"
        
        try:
            import json
            entities_json = json.dumps(context.user_data.get("sched_entities", []))
            
            await db(lambda: supabase.table("scheduled_menfess").insert({
                "user_id": user_id,
                "teks": context.user_data["sched_text"],
                "entities": entities_json,
                "target_username": context.user_data["sched_username"],
                "send_time": context.user_data["sched_time"],
                "frequency": freq,
                "is_active": True
            }).execute())
            
            waktu_set = context.user_data.get('sched_time')
            context.user_data.clear()
            await update.message.reply_text(get_wording("sched_sukses", waktu=waktu_set), parse_mode="HTML", reply_markup=get_main_keyboard())
        except Exception as e:
            logger.error(f"Gagal simpan schedule: {e}")
            await update.message.reply_text(get_wording("sched_gagal_simpan"), reply_markup=get_main_keyboard(), parse_mode="HTML")
            
        return ConversationHandler.END

    # --- FILTER BAD WORDS UNTUK MENFESS ---
    for bw in CACHE_BAD_WORDS:
        if re.search(rf'\b{re.escape(bw)}\b', pesan_teks_lower):
            await update.message.reply_text(get_wording("menfess_bad_words"), parse_mode="HTML")
            return ConversationHandler.END

    # --- CEK STATUS BOT (HANYA UNTUK MENFESS) ---
    if not bot_active:
        try:
            header_msg = await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"📥 *PESAN MASUK (BOT CLOSED)*\nDari: {display_name} (`{user_id}`)\n*(Pesan ini diteruskan langsung karena sesi menfess sedang ditutup)*",
                parse_mode="Markdown"
            )
            await context.bot.copy_message(
                chat_id=ADMIN_GROUP_ID,
                from_chat_id=user_id,
                message_id=update.message.message_id,
                reply_to_message_id=header_msg.message_id
            )
            await update.message.reply_text(get_wording("menfess_base_closed"), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Gagal meneruskan pesan saat bot closed: {e}")
            await update.message.reply_text(get_wording("menfess_base_closed_gagal"), parse_mode="HTML")
            
        return ConversationHandler.END
        
    # --- PROSES MENFESS ---
    if MENFESS_MODE == "cort":
        if not update.message.text:
            await update.message.reply_text(get_wording("cort_hanya_teks"), parse_mode="HTML")
            return ConversationHandler.END

        cerita_html = update.message.text_html
        
        display_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
        
        text_channel = f"⚖️ <b>ANONYMOUS COURT</b> ⚖️\n\n📝 <b>Kasus:</b>\n{cerita_html}"
        
        keyboard = [
            [
                InlineKeyboardButton("☠️ (0)", callback_data="cort|guilty"),
                InlineKeyboardButton("😇 (0)", callback_data="cort|innocent"),
                InlineKeyboardButton("🤡 (0)", callback_data="cort|fool")
            ]
        ]
        
        try:
            msg = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text_channel,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            
            CORT_VOTES[msg.message_id] = {
                'users': {}, 
                'counts': {'guilty': 0, 'innocent': 0, 'fool': 0},
                'task_running': False
            }
            
            try:
                await db(lambda: supabase.table("menfess_map").insert({"post_id": msg.message_id, "sender_user_id": user_id}).execute())
            except Exception as e:
                logger.error(f"DB Error Cort Map: {e}")
                
            new_balance = await add_kith_coins(user_id, 50)
            coin_msg = f"\n💰 <b>+50 Kith-Coins!</b> (Saldo: {new_balance})" if new_balance is not None else ""
            
            keyboard_user = [[InlineKeyboardButton("⚖️ Lihat Kasus Kamu", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{msg.message_id}")]]
            await update.message.reply_text(
                get_wording("cort_sukses", coin_msg=coin_msg),
                reply_markup=InlineKeyboardMarkup(keyboard_user), 
                parse_mode="HTML"
            )

            log_msg = f"📌 <b>Log Menfess (CORT):</b>\n🕰️ Waktu: {update.message.date}\n👤 Pengirim: {display_name}\n🆔 ID: <code>{user_id}</code>\n💬 Kasus: {cerita_html}"
            
            keyboard_log = [
                [InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{msg.message_id}")],
                [InlineKeyboardButton("❌ Hapus & Tegur", callback_data=f"del_{user_id}_{msg.message_id}")]
            ]
            
            await context.bot.send_message(
                chat_id=LOG_GROUP_ID, 
                text=log_msg, 
                reply_markup=InlineKeyboardMarkup(keyboard_log), 
                parse_mode="HTML"
            )

        except Exception as e:
            logger.error(f"Gagal kirim cort menfess: {e}")
            await update.message.reply_text(get_wording("cort_gagal"), parse_mode="HTML")
            
        context.user_data.clear()
        return ConversationHandler.END
        
    # --- PROSES MENFESS ---
    elif MENFESS_MODE == "auto":
        # --- 1. CEK VIP SENDER VIA CACHE ---
        is_vip, _ = await get_vip_status(user_id)
        cooldown_limit = 600 if is_vip else 5400
        context.user_data['is_vip'] = is_vip
        
        # --- 2. CEK COOLDOWN SENDER ID VIA SUPABASE ---
        try:
            res_sender = await db(lambda: supabase.table("menfess_map").select("created_at").eq("sender_user_id", user_id).order("created_at", desc=True).limit(1).execute())
            
            if hasattr(res_sender, 'data') and res_sender.data:
                last_sent_str = res_sender.data[0]['created_at']
                last_sent_dt = datetime.fromisoformat(last_sent_str.replace("Z", "+00:00"))
                now_utc = datetime.now(timezone.utc)
                
                selisih_detik = (now_utc - last_sent_dt).total_seconds()
                
                if selisih_detik < cooldown_limit:
                    sisa_menit = int((cooldown_limit - selisih_detik) / 60)
                    if is_vip:
                        await update.message.reply_text(get_wording("menfess_cooldown_vip", sisa_menit=sisa_menit), parse_mode="HTML")
                    else:
                        await update.message.reply_text(
                            get_wording("menfess_cooldown_reguler", sisa_menit=sisa_menit, vip_promo=get_wording("vip_promo_cooldown")),
                            parse_mode="HTML"
                        )
                    return ConversationHandler.END
        except Exception as e:
            logger.error(f"Gagal cek cooldown sender: {e}")
        if not update.message.text:
            await update.message.reply_text(get_wording("menfess_hanya_teks"), parse_mode="HTML")
            return ConversationHandler.END

        panjang_efektif = hitung_panjang_efektif(update.message.text)
        batas_karakter = 300 if is_vip else 100

        if panjang_efektif > batas_karakter:
            await update.message.reply_text(get_wording("menfess_terlalu_panjang", panjang=panjang_efektif, batas=batas_karakter), parse_mode="HTML")
            return ConversationHandler.END

        ada_mention = False
        if update.message.entities:
            for ent in update.message.entities:
                if ent.type == "mention":
                    ada_mention = True
                    break

        if ada_mention or re.search(r'(?:^|\s)@/?\w+', pesan_teks):
            await update.message.reply_text(get_wording("menfess_ada_mention"), parse_mode="HTML")
            return ConversationHandler.END

        pattern_fwa = r'\b(f[\W_]*w[\W_]*a|affections?|b[\W_]*[x×][\W_]*b|b[\W_]*[x×][\W_]*g|g[\W_]*[x×][\W_]*b|w[\W_]*l[\W_]*w|m[\W_]*l[\W_]*m|w[\W_]*l[\W_]*m|m[\W_]*l[\W_]*w)\b'

        if re.search(pattern_fwa, pesan_teks_lower):
            if not re.search(r'\d+', pesan_teks_lower):
                await update.message.reply_text(
                    get_wording("menfess_fwa_wajib_umur"),
                    parse_mode="HTML"
                )
                return ConversationHandler.END

        context.user_data['teks_menfess'] = update.message.text
        
        filtered_entities = []
        if update.message.entities:
            for ent in update.message.entities:
                if ent.type not in ["url", "text_link"]:
                    filtered_entities.append(ent)
                    
        context.user_data['entities'] = filtered_entities

        await update.message.reply_text(get_wording("menfess_teks_diterima"), parse_mode="HTML")
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
            await update.message.reply_text(get_wording("menfess_manual_masuk_antrean"), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error kirim manual review: {e}")
            await update.message.reply_text(get_wording("menfess_manual_gagal_kirim"), parse_mode="HTML")

        return ConversationHandler.END


async def process_scheduled_menfess(context: ContextTypes.DEFAULT_TYPE):
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    current_hm = now_wib.strftime("%H:%M")

    try:
        res = await db(lambda: supabase.table("scheduled_menfess").select("*").eq("send_time", current_hm).execute())
        if not res.data:
            return

        for row in res.data:
            user_id = row['user_id']
            
            # Cek status VIP via cache
            is_vip, _ = await get_vip_status(user_id)

            if not is_vip:
                await db(lambda: supabase.table("scheduled_menfess").delete().eq("id", row['id']).execute())
                try:
                    await context.bot.send_message(user_id, get_wording("sched_vip_habis"), parse_mode="HTML")
                except: pass
                continue

            teks_asli = row['teks']
            target_username = row['target_username']
            target_url = f"https://t.me/{target_username}"
            
            import json
            raw_entities = json.loads(row['entities'])
            original_entities = [MessageEntity(**ent) for ent in raw_entities] if raw_entities else []
            
            try:
                prefix = "@ "
                final_text = prefix + teks_asli
                len_prefix = len(prefix.encode('utf-16-le')) // 2
                
                shifted_entities = []
                if original_entities:
                    for ent in original_entities:
                        shifted_entities.append(MessageEntity(
                            type=ent.type,
                            offset=ent.offset + len_prefix,
                            length=ent.length,
                            url=getattr(ent, 'url', None),
                            user=getattr(ent, 'user', None),
                            language=getattr(ent, 'language', None),
                            custom_emoji_id=getattr(ent, 'custom_emoji_id', None)
                        ))

                target_url = f"https://t.me/{target_username}"
                visible_link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=1, url=target_url)
                final_entities = shifted_entities + [visible_link]

                new_teks, new_entities = process_user_emojis(final_text, final_entities)

                # Kirim via userbot VIP dengan retry
                sent_message_id = None
                for attempt in range(2):
                    try:
                        sent_message_id = await kirim_menfess_vip(new_teks, new_entities, target_url)
                        if sent_message_id:
                            break
                    except Exception as vip_err:
                        logger.error(f"[Attempt {attempt+1}] Gagal kirim VIP (scheduled) user {user_id}: {vip_err}")
                        if attempt == 1:
                            try:
                                await context.bot.send_message(ADMIN_GROUP_ID, f"⚠️ Userbot gagal kirim VIP scheduled user {user_id}: {vip_err}")
                            except Exception: pass
                
                if sent_message_id:
                    global LAST_VIP_PROMO_TIME
                    LAST_VIP_PROMO_TIME = datetime.now(timezone.utc)
                    await db(lambda: supabase.table("menfess_map").insert({
                        "post_id": sent_message_id, 
                        "sender_user_id": user_id, 
                        "target_username": target_username
                    }).execute())
                    
                    try:
                        keyboard_user = [
                            [
                                InlineKeyboardButton("👁️ Lihat", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{sent_message_id}"),
                                InlineKeyboardButton("🔄 Reload", callback_data=f"reload|{sent_message_id}")
                            ],
                            [
                                InlineKeyboardButton("🗑️ Hapus", callback_data=f"userdel|{sent_message_id}")
                            ]
                        ]
                        await context.bot.send_message(user_id, get_wording("sched_terkirim_notif"), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard_user))
                    except: pass
                    
                    if row['frequency'] == 'once':
                        await db(lambda: supabase.table("scheduled_menfess").delete().eq("id", row['id']).execute())
                        
                else:
                    logger.error(f"Gagal kirim via userbot VIP untuk jadwal {row['id']}")

            except Exception as e:
                logger.error(f"Gagal proses userbot kirim untuk jadwal {row['id']}: {e}")

    except Exception as e:
        logger.error(f"Error scheduler auto promote: {e}")


async def handle_username(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    display_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

    is_vip = context.user_data.get('is_vip', False)
    target_cooldown_limit = 1800 if is_vip else 7200

    if not update.message.text:
        await update.message.reply_text(get_wording("menfess_username_bukan_teks"), reply_markup=get_main_keyboard(), parse_mode="HTML")
        context.user_data.clear()
        return ConversationHandler.END
        
    raw_input = update.message.text.strip()
    
    if not re.match(r"^@?[a-zA-Z0-9_]+$", raw_input):
        await update.message.reply_text(get_wording("menfess_username_invalid"), reply_markup=get_main_keyboard(), parse_mode="HTML")
        context.user_data.clear()
        return ConversationHandler.END

    target_username = raw_input.replace("@", "")
    target_user_id = None
    try:
        user_res = await db(lambda: supabase.table("users").select("user_id").eq("username", target_username).execute())
        if user_res.data:
            target_user_id = user_res.data[0]["user_id"]
            
            if target_user_id in CACHE_BANNED_USERS:
                await update.message.reply_text(
                    get_wording("menfess_target_banned"),
                    reply_markup=get_main_keyboard()
                , parse_mode="HTML")
                context.user_data.clear()
                return ConversationHandler.END
                
    except Exception as e:
        logger.error(f"Gagal resolusi username ke ID: {e}")

    # --- 2. CEK COOLDOWN TARGET ---
    try:
        if target_user_id:
            res_target = await db(lambda: supabase.table("menfess_map").select("created_at").eq("target_user_id", target_user_id).order("created_at", desc=True).limit(1).execute())
        else:
            res_target = await db(lambda: supabase.table("menfess_map").select("created_at").eq("target_username", target_username).order("created_at", desc=True).limit(1).execute())

        if hasattr(res_target, 'data') and res_target.data:
            last_used_str = res_target.data[0]['created_at']
            last_used_dt = datetime.fromisoformat(last_used_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            selisih_detik = (now_utc - last_used_dt).total_seconds()

            if selisih_detik < target_cooldown_limit:
                sisa_menit = int((target_cooldown_limit - selisih_detik) / 60)
                await update.message.reply_text(
                    get_wording("menfess_target_cooldown", sisa_menit=sisa_menit),
                    reply_markup=get_main_keyboard()
                , parse_mode="HTML")
                context.user_data.clear()
                return ConversationHandler.END
    except Exception as e:
        logger.error(f"Gagal cek cooldown target: {e}")
        
    teks_asli = context.user_data.get('teks_menfess', "")
    original_entities = list(context.user_data.get('entities', []))

    # --- CEK HOLD 5 MENIT DARI VIP PROMO ---
    global LAST_VIP_PROMO_TIME
    now_utc = datetime.now(timezone.utc)
    delay_seconds = 0

    if LAST_VIP_PROMO_TIME:
        elapsed = (now_utc - LAST_VIP_PROMO_TIME).total_seconds()
        if elapsed < 300:
            delay_seconds = 300 - elapsed

    if delay_seconds > 0:
        job_data = {
            "user_id": user_id,
            "is_vip": is_vip,
            "teks_asli": teks_asli,
            "original_entities": [e.to_dict() for e in original_entities],
            "target_username": target_username,
            "target_user_id": target_user_id,
            "display_name": display_name
        }
        
        context.job_queue.run_once(
            send_queued_menfess,
            when=delay_seconds,
            data=job_data,
            name=f"queue_mf_{user_id}_{int(now_utc.timestamp())}"
        )

        menit = int(delay_seconds // 60)
        detik = int(delay_seconds % 60)
        await update.message.reply_text(
            f"⏳ <b>Menfess Ditahan Sementara!</b>\n\nSaat ini sedang ada user VIP yang promote. Menfess kamu masuk antrean dan akan otomatis terkirim dalam <b>{menit} menit {detik} detik</b>.\n\n<i>Kamu akan mendapat notifikasi saat pesan berhasil terkirim.</i>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    # --- JIKA TIDAK ADA HOLD, KIRIM LANGSUNG ---
    await execute_send_menfess(
        context=context,
        user_id=user_id,
        is_vip=is_vip,
        teks_asli=teks_asli,
        original_entities=original_entities,
        target_username=target_username,
        target_user_id=target_user_id,
        display_name=display_name
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel_menfess(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text("✅ Pengiriman menfess dibatalkan.")
    return ConversationHandler.END


# ================== PERBAIKAN execute_send_menfess ==================
async def execute_send_menfess(context: ContextTypes.DEFAULT_TYPE, user_id: int, is_vip: bool, teks_asli: str, original_entities: list, target_username: str, target_user_id, display_name: str):
    """Fungsi helper terpusat untuk mengirim menfess, simpan ke DB, dan kasih koin/notif."""
    global LAST_VIP_PROMO_TIME
    
    # --- FIX: Jadikan simbol @ di awal kalimat sebagai hyperlink ---
    prefix = "@ "
    final_text = prefix + teks_asli

    len_prefix = len(prefix.encode('utf-16-le')) // 2
    
    shifted_entities = []
    if original_entities:
        for ent in original_entities:
            shifted_entities.append(MessageEntity(
                type=ent.type,
                offset=ent.offset + len_prefix,
                length=ent.length,
                url=getattr(ent, 'url', None),
                user=getattr(ent, 'user', None),
                language=getattr(ent, 'language', None),
                custom_emoji_id=getattr(ent, 'custom_emoji_id', None)
            ))

    target_url = f"https://t.me/{target_username}"
    visible_link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=1, url=target_url)
    
    final_entities = shifted_entities + [visible_link]

    sent_message_id = None
    
    # ---- KIRIM VIA VIP dengan retry ----
    if is_vip:
        for attempt in range(2):
            try:
                new_teks, new_entities = process_user_emojis(final_text, final_entities)
                sent_message_id = await kirim_menfess_vip(new_teks, new_entities, target_url)
                if sent_message_id:
                    LAST_VIP_PROMO_TIME = datetime.now(timezone.utc)
                    break
            except Exception as vip_err:
                logger.error(f"[Attempt {attempt+1}] Gagal kirim VIP user {user_id}: {vip_err}")
                if attempt == 1:
                    try:
                        await context.bot.send_message(ADMIN_GROUP_ID, f"⚠️ Userbot gagal kirim VIP user {user_id}: {vip_err}")
                    except Exception: pass

    # FALLBACK: bot biasa (jika VIP gagal atau bukan VIP)
    if sent_message_id is None:
        await asyncio.sleep(random.uniform(0.1, 0.5))
        try:
            message_sent = await context.bot.send_message(
                chat_id=CHANNEL_ID, 
                text=final_text, 
                entities=final_entities, 
                link_preview_options=LinkPreviewOptions(is_disabled=False, url=target_url)
            )
        except Exception as e:
            logger.warning(f"Preview gagal untuk {target_url}, mencoba jalur darurat: {e}")
            message_sent = await context.bot.send_message(
                chat_id=CHANNEL_ID, 
                text=final_text, 
                entities=final_entities, 
                link_preview_options=LinkPreviewOptions(is_disabled=False)
            )
        
        sent_message_id = message_sent.message_id

    CACHE_COMSECT_OFF.add(sent_message_id)

    # --- Tambah Koin ---
    new_balance = await add_kith_coins(user_id, 50)
    coin_msg = f"\n💰 <b>+50 Kith-Coins!</b> (Saldo: {new_balance})" if new_balance is not None else ""

    # --- Reply ke user ---
    vip_promo = "" if is_vip else get_wording("vip_promo_auto_terkirim")
    keyboard_user = [
        [
            InlineKeyboardButton("👁️ Lihat", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{sent_message_id}"),
            InlineKeyboardButton("🔄 Reload", callback_data=f"reload|{sent_message_id}")
        ],
        [
            InlineKeyboardButton("🗑️ Hapus", callback_data=f"userdel|{sent_message_id}")
        ]
    ]
    await context.bot.send_message(
        chat_id=user_id,
        text=get_wording("menfess_terkirim", coin_msg=coin_msg, vip_promo=vip_promo),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard_user)
    )

    # --- Ads ---
    ads_enabled = get_wording("ads_enabled").strip().upper()
    if ads_enabled == "ON":
        try:
            await context.bot.send_message(chat_id=user_id, text=get_wording("ads_text"), parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass

    # --- Simpan ke DB ---
    try:
        await db(lambda: supabase.table("menfess_map").insert({"post_id": sent_message_id, "sender_user_id": user_id, "target_username": target_username, "target_user_id": target_user_id}).execute())
    except Exception as e:
        logger.error(f"DB Error Auto: {e}")

    # --- Log ke Admin ---
    log_msg = f"📌 Log Menfess (AUTO):\n🕰️ Waktu: {datetime.now(timezone.utc)}\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`\n🔗 Username Target: @{target_username}\n💬 Pesan: {teks_asli}"
    keyboard_log = [
        [InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{sent_message_id}")],
        [InlineKeyboardButton("❌ Hapus & Tegur", callback_data=f"del_{user_id}_{sent_message_id}")]
    ]
    await context.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=TOPIC_ID_MENFESS_LOG, text=log_msg, reply_markup=InlineKeyboardMarkup(keyboard_log), parse_mode="Markdown")

    # --- AUTO CHECK PREVIEW (Step 7) ---
    # Schedule pengecekan preview otomatis setelah 6 detik
    try:
        # Hanya jika userbot tersedia dan memiliki has_preview
        from userbot import has_preview
        context.job_queue.run_once(auto_check_preview, when=6, data=sent_message_id)
    except ImportError:
        # Jika has_preview tidak tersedia, tetap lakukan reload preview via userbot
        context.job_queue.run_once(lambda ctx: reload_preview_userbot(ctx.job.data), when=8, data=sent_message_id)


async def send_queued_menfess(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    original_entities = [MessageEntity(**e) for e in data["original_entities"]]
    
    await execute_send_menfess(
        context=context,
        user_id=data["user_id"],
        is_vip=data["is_vip"],
        teks_asli=data["teks_asli"],
        original_entities=original_entities,
        target_username=data["target_username"],
        target_user_id=data["target_user_id"],
        display_name=data["display_name"]
    )


# --- Auto preview check job (Step 7) ---
async def auto_check_preview(context: ContextTypes.DEFAULT_TYPE):
    from userbot import has_preview, reload_preview_userbot
    post_id = context.job.data
    try:
        # Jika fungsi has_preview tersedia, cek dulu
        if await has_preview(post_id):
            return
        # Jika tidak ada preview, reload
        await reload_preview_userbot(post_id)
        logger.info(f"Auto reload preview for {post_id} triggered.")
    except Exception as e:
        logger.error(f"Auto preview check gagal {post_id}: {e}")


# ================== AKHIR PERBAIKAN execute_send_menfess ==================


async def handle_userdel_menfess(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("|")
    if len(data) < 2: return
    
    post_id = int(data[1])
    
    try:
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=post_id)
        try:
            await db(lambda: supabase.table("menfess_map").delete().eq("post_id", post_id).execute())
        except Exception as db_err:
            logger.error(f"Gagal hapus menfess_map saat userdel: {db_err}")
        await query.edit_message_text(
            f"{query.message.text_html}\n\n✅ <i>Pesan ini telah kamu hapus secara mandiri.</i>",
            parse_mode="HTML",
            reply_markup=None 
        )
    except Exception as e:
        logger.error(f"Gagal hapus mandiri post_id {post_id}: {e}")
        err_str = str(e).lower()
        if "message to delete not found" in err_str:
            await query.edit_message_text(
                f"{query.message.text_html}\n\n⚠️ <i>Pesan ini sudah tidak ada di channel (mungkin sudah dihapus admin/sistem).</i>",
                parse_mode="HTML",
                reply_markup=None
            )
        else:
            await query.answer("Gagal menghapus pesan. Coba lagi nanti.", show_alert=True)


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

                # --- 1. CEK STATUS VIP USER VIA CACHE ---
                is_vip, _ = await get_vip_status(user_id)

                sent_msg_id = None

                # --- 2. JIKA VIP & HANYA TEKS -> KIRIM VIA USERBOT ---
                if is_vip and original_msg.text:
                    for attempt in range(2):
                        try:
                            teks_asli = original_msg.text
                            original_entities = original_msg.entities or []
                            new_teks, new_entities = process_user_emojis(teks_asli, original_entities)
                            sent_msg_id = await kirim_menfess_vip(new_teks, new_entities)
                            if sent_msg_id:
                                break
                        except Exception as vip_err:
                            logger.error(f"[Attempt {attempt+1}] Gagal kirim VIP manual: {vip_err}")
                            if attempt == 1:
                                try:
                                    await context.bot.send_message(ADMIN_GROUP_ID, f"⚠️ Userbot gagal kirim VIP manual user {user_id}: {vip_err}")
                                except Exception: pass

                # --- 3. JIKA NON-VIP / ADA MEDIA / USERBOT ERROR -> BOT BIASA ---
                if sent_msg_id is None:
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
                    sent_msg_id = sent_msg.message_id

                # --- 4. LANJUTKAN PROSES SEPERTI BIASA ---
                if not comsect_on:
                    CACHE_COMSECT_OFF.add(sent_msg_id)

                log_msg = f"📌 Log Menfess (Manual Approved):\n🆔 Pengirim ID: `{user_id}`\n⚙️ Comsect: {'ON' if comsect_on else 'OFF'}\n🤖 Dikirim via: {'Userbot (VIP)' if is_vip and original_msg.text else 'Regular Bot'}"
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    message_thread_id=TOPIC_ID_MENFESS_LOG,
                    text=log_msg,
                    parse_mode="Markdown"
                )

                new_balance = await add_kith_coins(user_id, earned_coins)

                try:
                    await db(lambda: supabase.table("menfess_map").insert({"post_id": sent_msg_id, "sender_user_id": user_id}).execute())
                except Exception as e:
                    logger.error(f"DB Error Map: {e}")

                await query.edit_message_text(f"{query.message.text}\n\n✅ *STATUS: {status_text}*", parse_mode="Markdown")
                await send_admin_log(
                    context, 
                    f"Approve Menfess ({'CS ON' if comsect_on else 'CS OFF'})", 
                    update.effective_user, 
                    f"Sender ID: `{user_id}`\nPost ID: {sent_msg_id}"
                )

                coin_msg = f"\n💰 <b>+{earned_coins} Kith-Coins!</b> (Saldo: {new_balance})" if new_balance is not None else ""
                keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{sent_msg_id}")]]
                await context.bot.send_message(chat_id=user_id, text=get_wording("menfess_disetujui", status_text=status_text, coin_msg=coin_msg), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
                
                ads_enabled = get_wording("ads_enabled").strip().upper()
                if ads_enabled == "ON":
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=get_wording("ads_text"),
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                    except Exception:
                        pass
            
            except Exception as e:
                logger.error(f"Gagal publish manual menfess: {e}")
                await query.edit_message_text(f"{query.message.text}\n\n❌ *GAGAL DIPUBLISH:* Pesan asli mungkin dihapus atau terjadi error internal.", parse_mode="Markdown")

        elif action == "R":
            await query.edit_message_text(f"{query.message.text}\n\n❌ *STATUS: DITOLAK*", parse_mode="Markdown")
            warning_text = get_wording("menfess_ditolak")
            await context.bot.send_message(chat_id=user_id, text=warning_text, parse_mode="HTML")
            await send_admin_log(
                context, 
                "Menolak Menfess (Reject)", 
                update.effective_user, 
                f"Sender ID: `{user_id}`"
            )


async def handle_admin_reply(update: Update, context: CallbackContext):
    if update.effective_chat.id not in [ADMIN_GROUP_ID, LOG_GROUP_ID] or not update.message.reply_to_message:
        return
    
    if context.user_data.get("editing_wording_key") and update.message.text:
        return await handle_wording_text_input(update, context)
    
    if not update.message.reply_to_message:
        return

    replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""

    match = re.search(r"(?:ID(?:\s*Pengguna)?|Dari\s*ID):?\s*[`]*(\d+)", replied_text, re.IGNORECASE)
    if not match:
        return

    user_id = int(match.group(1))
    reply_text = update.message.text or update.message.caption or ""

    if "#AnonFallback" in replied_text:
        if reply_text.strip() == "/stop_anon":
            try:
                await db(lambda: supabase.table("users").update({"chat_state": "menfess"}).eq("user_id", user_id).execute())
                await context.bot.send_message(chat_id=user_id, text=get_wording("anon_left_partner"), parse_mode="HTML")
                await update.message.reply_text(f"✅ Sesi obrolan anonim dengan ID `{user_id}` telah diakhiri oleh Admin.", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Gagal mengakhiri sesi anonim: {e}")
            return

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

    try:
        await context.bot.copy_message(
            chat_id=user_id, 
            from_chat_id=update.effective_chat.id, 
            message_id=update.message.message_id
        )

        await send_admin_log(
            context, 
            "Membalas Pesan User/Userbot", 
            update.effective_user, 
            f"Ke User ID: `{user_id}`\nIsi Balasan: {reply_text[:50]}..."
        )
        
        notif_text = "💬 Pesan anonim terkirim!" if "#AnonFallback" in replied_text else "✅ Balasan telah dikirim ke user."
        notif = await update.message.reply_text(notif_text)
        
        await asyncio.sleep(5)
        try:
            await notif.delete()
        except Exception:
            pass
    except Exception:
        await update.message.reply_text("❌ Gagal mengirim balasan.")


async def handle_channel_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if LOG_GROUP_ID == 0: return

    if update.channel_post:
        msg = update.channel_post
        action = "Memposting di Channel"
    elif update.edited_channel_post:
        msg = update.edited_channel_post
        action = "Mengedit Pesan di Channel"
    else:
        return

    if msg.chat.username and "@" + msg.chat.username.lower() != CHANNEL_ID.lower():
        return 

    admin_name = "Admin (Anonim Channel)"
    admin_username = "Tidak ada"
    admin_id = "N/A"

    if msg.from_user: 
        admin_name = msg.from_user.first_name
        admin_username = f"@{msg.from_user.username}" if msg.from_user.username else "Tidak ada"
        admin_id = msg.from_user.id
    elif msg.author_signature:
        admin_name = f"Signature: {msg.author_signature}"

    text_preview = msg.text or msg.caption or "[Media Tanpa Teks]"
    
    text = (
        f"🚨 *CHANNEL ACTIVITY LOG*\n"
        f"👤 *Oleh:* {admin_name} ({admin_username})\n"
        f"🆔 *ID:* `{admin_id}`\n"
        f"🛠 *Aksi:* {action}\n"
        f"🔗 *Message ID:* {msg.message_id}\n"
        f"📝 *Isi/Perubahan:* {text_preview[:100]}..."
    )
    try:
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=text, parse_mode="Markdown")
    except Exception:
        pass


async def update_cort_message_bg(bot, chat_id, msg_id, vote_data):
    await asyncio.sleep(3) 
    
    try:
        c_g = vote_data['counts']['guilty']
        c_i = vote_data['counts']['innocent']
        c_f = vote_data['counts']['fool']
        
        new_keyboard = [
            [
                InlineKeyboardButton(f"☠️ ({c_g})", callback_data="cort|guilty"),
                InlineKeyboardButton(f"😇 ({c_i})", callback_data="cort|innocent"),
                InlineKeyboardButton(f"🤡 ({c_f})", callback_data="cort|fool")
            ]
        ]
        
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=InlineKeyboardMarkup(new_keyboard)
        )
    except Exception as e:
        pass 
    finally:
        vote_data['task_running'] = False


async def handle_cort_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = update.effective_user.id
    msg_id = query.message.message_id
    chat_id = query.message.chat_id
    
    data = query.data.split("|")
    if len(data) < 2:
        return
        
    choice = data[1]
    
    await query.answer("Memproses vote... ⚖️")
    
    if msg_id not in CORT_VOTES:
        keyboard = query.message.reply_markup.inline_keyboard
        def get_count(text):
            match = re.search(r'\((\d+)\)', text)
            return int(match.group(1)) if match else 0
            
        CORT_VOTES[msg_id] = {
            'users': {},
            'counts': {
                'guilty': get_count(keyboard[0][0].text),
                'innocent': get_count(keyboard[1][0].text),
                'fool': get_count(keyboard[2][0].text)
            },
            'task_running': False
        }
        
    vote_data = CORT_VOTES[msg_id]
    
    previous_choice = vote_data['users'].get(user_id)
    
    if previous_choice == choice:
        del vote_data['users'][user_id]
        vote_data['counts'][choice] = max(0, vote_data['counts'][choice] - 1)
    else:
        if previous_choice:
            vote_data['counts'][previous_choice] = max(0, vote_data['counts'][previous_choice] - 1)
        vote_data['users'][user_id] = choice
        vote_data['counts'][choice] += 1
        
    if not vote_data['task_running']:
        vote_data['task_running'] = True
        asyncio.create_task(update_cort_message_bg(context.bot, chat_id, msg_id, vote_data))


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
                link = f"https://telegram.me/{CHANNEL_ID.lstrip('@')}/{post_id}?comment={msg.message_id}"

                notif_text = get_wording("komentar_notif", commenter=commenter, msg_id=msg.message_id)
                await context.bot.send_message(chat_id=sender_user_id, text=notif_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Lihat Balasan", url=link)]]), parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ Gagal proses balasan diskusi: {e}")


async def open_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = True
        try:
            await db(lambda: supabase.table("bot_settings").upsert({"key": "bot_active", "value": "true"}).execute())
        except Exception as e:
            logger.error(f"Gagal simpan bot_active ke DB: {e}")
        await update.message.reply_text("✅ Bot telah diaktifkan kembali.")


async def close_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = False
        try:
            await db(lambda: supabase.table("bot_settings").upsert({"key": "bot_active", "value": "false"}).execute())
        except Exception as e:
            logger.error(f"Gagal simpan bot_active ke DB: {e}")
        await update.message.reply_text("⏸️ Bot telah dipause.")


async def get_group_id(update: Update, context: CallbackContext):
    await update.message.reply_text(f"🆔 ID: `{update.effective_chat.id}`\n🏷️ Nama: {update.effective_chat.title or 'Private'}", parse_mode="Markdown")


async def get_all_user_ids():
    all_ids = []
    page_size = 1000
    offset = 0
    try:
        while True:
            response = await db(
                lambda o=offset: supabase
                    .table("users")
                    .select("user_id")
                    .range(o, o + page_size - 1)
                    .execute()
            )
            if not (hasattr(response, "data") and response.data):
                break
            all_ids.extend(row["user_id"] for row in response.data)
            if len(response.data) < page_size:
                break
            offset += page_size
    except Exception as e:
        logger.error(f"Gagal fetch semua user IDs: {e}")
    return all_ids


async def menu(update: Update, context: CallbackContext):
    context.user_data.clear()
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text("👋 *Navigasi Bot*\n\nSilakan pilih menu di bawah:", parse_mode="Markdown", reply_markup=get_main_keyboard())


async def broadcast_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _broadcast_running
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args:
        return await update.message.reply_text("Format: /broadcastfw <link>")

    if _broadcast_running:
        return await update.message.reply_text("⚠️ Broadcast sedang berjalan. Tunggu hingga selesai.")

    link = context.args[0]
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", link)
    if not match:
        return await update.message.reply_text("❌ Link tidak valid!")

    channel_username, message_id = match.groups()
    if channel_username == "c":
        return await update.message.reply_text("❌ Tidak bisa forward menggunakan link dari channel private!")

    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0:
        return await update.message.reply_text("⚠️ Tidak ada user di database.")

    _broadcast_running = True
    sc, fc = 0, 0
    failed_users = []
    batch_size = 10

    status_msg = await update.message.reply_text(f"⏳ *Memulai broadcast forward ke {total_users} user...*", parse_mode="Markdown")

    try:
        for i in range(0, total_users, batch_size):
            batch = user_list[i : i + batch_size]
            tasks = [safe_forward(context, uid, f"@{channel_username}", int(message_id)) for uid in batch]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for idx, res in enumerate(results):
                if isinstance(res, Exception):
                    fc += 1
                    failed_users.append(batch[idx])
                else:
                    sc += 1

            if (i // batch_size) % 4 == 0 or (i + batch_size) >= total_users:
                try:
                    await status_msg.edit_text(
                        f"⏳ *Sedang memproses broadcast forward... ({min(i + batch_size, total_users)}/{total_users})*\n"
                        f"✅ Berhasil: {sc}\n❌ Gagal: {fc}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            await asyncio.sleep(2.0)

    finally:
        _broadcast_running = False

    try:
        await status_msg.edit_text(f"✅ *Broadcast Forward Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(f"✅ *Broadcast Forward Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

    if failed_users:
        try:
            await process_broadcast_failures(context, update.effective_chat.id, failed_users, "broadcast_forward")
        except Exception as e:
            logger.error(f"Error memproses file failed broadcast_forward: {e}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _broadcast_running
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    if not update.message.reply_to_message:
        return await update.message.reply_text("⚠️ Format salah!\nKamu harus **me-reply (balas)** pesan yang ingin di-broadcast dengan mengetik `/broadcast`.\n\n*(Format bold/italic/media/emoji premium akan otomatis terbawa!)*", parse_mode="Markdown")

    if _broadcast_running:
        return await update.message.reply_text("⚠️ Broadcast sedang berjalan. Tunggu hingga selesai.")

    replied_msg = update.message.reply_to_message
    user_list = await get_all_user_ids()
    total_users = len(user_list)
    
    if total_users == 0:
        return await update.message.reply_text("⚠️ Tidak ada user di database.")

    _broadcast_running = True
    sc, fc = 0, 0
    failed_users = []
    batch_size = 10 

    status_msg = await update.message.reply_text(f"⏳ *Memulai broadcast clone ke {total_users} user...*", parse_mode="Markdown")
    main_keyboard = get_main_keyboard()

    try:
        for i in range(0, total_users, batch_size):
            batch = user_list[i : i + batch_size]
            
            tasks = [
                context.bot.copy_message(
                    chat_id=uid, 
                    from_chat_id=replied_msg.chat_id, 
                    message_id=replied_msg.message_id, 
                    reply_markup=main_keyboard
                ) for uid in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for idx, res in enumerate(results):
                if isinstance(res, Exception):
                    fc += 1
                    failed_users.append(batch[idx])
                else:
                    sc += 1

            if (i // batch_size) % 4 == 0 or (i + batch_size) >= total_users:
                try:
                    await status_msg.edit_text(
                        f"⏳ *Sedang memproses broadcast... ({min(i + batch_size, total_users)}/{total_users})*\n"
                        f"✅ Berhasil: {sc}\n❌ Gagal: {fc}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            await asyncio.sleep(2.0)

    finally:
        _broadcast_running = False

    try:
        await status_msg.edit_text(f"✅ *Broadcast Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(f"✅ *Broadcast Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

    if failed_users:
        try:
            await process_broadcast_failures(context, update.effective_chat.id, failed_users, "broadcast")
        except Exception as e:
            logger.error(f"Error memproses file failed broadcast: {e}")


async def safe_forward(context, chat_id, from_chat_id, message_id):
    try:
        return await context.bot.forward_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)
    except Exception:
        return await context.bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)


async def process_broadcast_failures(context: ContextTypes.DEFAULT_TYPE, chat_id: int, failed_ids: list, broadcast_name: str):
    if not failed_ids: return

    failed_data = {}
    for i in range(0, len(failed_ids), 200):
        batch_ids = failed_ids[i:i+200]
        try:
            res = await db(lambda b=batch_ids: supabase.table("users").select("user_id, total_kith_coins").in_("user_id", b).execute())
            if res and hasattr(res, 'data') and res.data:
                for row in res.data:
                    failed_data[row["user_id"]] = row.get("total_kith_coins", 0)
        except Exception as e:
            logger.error(f"Gagal fetch failed users coins: {e}")

    file_lines = []
    to_delete = []
    to_passive = []

    for uid in failed_ids:
        coins = failed_data.get(uid, 0) if failed_data.get(uid) is not None else 0
        file_lines.append(f"ID: {uid} | Total History Kithkoins: {coins}")
        CACHE_PASSIVE_USERS.add(uid)
        if coins == 0:
            to_delete.append(uid)
        else:
            to_passive.append(uid)

    file_content = "\n".join(file_lines).encode('utf-8')
    filename = f"failed_{broadcast_name}.txt"
    
    caption = (
        f"📄 Terdapat {len(failed_ids)} user yang memblokir bot ({broadcast_name}).\n\n"
        f"🛠️ *Rencana Tindakan:*\n"
        f"🗑️ Hapus Permanen (Koin 0): *{len(to_delete)} user*\n"
        f"💤 Set Status Pasif (Koin > 0): *{len(to_passive)} user*"
    )

    reply_markup = None
    if to_delete or to_passive:
        task_id = str(uuid.uuid4())[:8]
        BROADCAST_DELETE_CACHE[task_id] = {
            "delete": to_delete,
            "passive": to_passive
        }
        keyboard = [[InlineKeyboardButton(f"🛠️ Generate SQL Pembersihan", callback_data=f"delbc|{task_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        caption += "\n\n🚨 *Perhatian:* Klik tombol di bawah untuk membuat kode SQL pembersihan database secara otomatis."

    try:
        await context.bot.send_document(
            chat_id=chat_id, document=file_content, filename=filename,
            caption=caption, reply_markup=reply_markup,
            read_timeout=60, write_timeout=60, connect_timeout=60
        )
    except Exception as e:
        logger.error(f"Gagal upload file broadcast: {e}")
        await context.bot.send_message(chat_id, f"⚠️ *Laporan Gagal*\nAda {len(failed_ids)} gagal tapi file timeout.", parse_mode="Markdown")


async def handle_broadcast_delete_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query.data.startswith("delbc|"):
        return

    try:
        await query.answer()
    except Exception:
        pass

    task_id = query.data.split("|")[1]
    to_delete = BROADCAST_DELETE_CACHE.get(task_id)

    if not to_delete:
        try:
            return await query.edit_message_caption(
                caption=f"{query.message.caption}\n\n❌ *Data sudah kadaluarsa atau SQL sudah di-generate sebelumnya.*", 
                parse_mode="Markdown"
            )
        except Exception:
            return

    BROADCAST_DELETE_CACHE.pop(task_id, None)

    try:
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n✅ *Kode SQL berhasil di-generate!*", 
            parse_mode="Markdown"
        )
    except Exception:
        pass

    ids_str = ", ".join(str(uid) for uid in to_delete)
    sql_query = f"-- Copy dan jalankan query ini di SQL Editor Supabase kamu\nDELETE FROM users WHERE user_id IN ({ids_str});"

    if len(sql_query) > 3500:
        file_content = sql_query.encode('utf-8')
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file_content,
            filename="delete_failed_users.sql",
            caption=f"📄 *Kode SQL terlalu panjang!*\nSilakan download file `.sql` ini, lalu copy seluruh isinya dan jalankan di menu **SQL Editor** pada dashboard Supabase kamu.",
            parse_mode="Markdown"
        )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Berikut adalah kode SQL untuk menghapus {len(to_delete)} user tersebut.\nSilakan tekan teks di bawah untuk menyalin, lalu jalankan di **SQL Editor** Supabase:\n\n```sql\n{sql_query}\n```",
            parse_mode="Markdown"
        )


async def add_command(update: Update, context: CallbackContext) -> None:
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
    fallback = f"https://telegram.me/c/{str(GROUP_ID_DISKUSI).replace('-100', '')}/{comment_msg_id}"
    try:
        lookup_id = thread_id or comment_msg_id
        map_res = await db(lambda: supabase.table("menfess_map").select("post_id").eq("discussion_message_id", lookup_id).execute())
        if hasattr(map_res, 'data') and map_res.data:
            post_id = map_res.data[0]['post_id']
            channel_username = CHANNEL_ID.replace('@', '')
            return f"https://telegram.me/{channel_username}/{post_id}?comment={comment_msg_id}"
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


# === LOOP TIMER (DENGAN FIX SELECT "*" & NOTIF AFK GAME OVER) ===
async def run_game_timer(chat_id, game_id, thread_id, context, start_round=1):
    for i in range(start_round, 6):
        await asyncio.sleep(120)
        
        res = await db(lambda: supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute())
        if not hasattr(res, 'data') or not res.data:
            return
        game = res.data[0]
        if game['status'] != 'playing':
            return

        players = game['players']
        recap = f"⏱️ *Ronde {i} Selesai!*\n\n*Rekap Kata Pemain:*\n"
        dead_players = []
        
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

        for pid in dead_players:
            del players[pid]

        await db(lambda: supabase.table("uc_active_games").update({"players": players}).eq("game_id", game_id).execute())

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

            if is_game_over:
                result_msg = await context.bot.send_message(chat_id, hasil_text, reply_to_message_id=game_id, parse_mode="Markdown")
                result_link = await get_discussion_link(result_msg.message_id, thread_id)
                btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Lihat Hasil Diskusi", url=result_link)]])

                for pid in all_players_id:
                    koin = 100 if pid == under_id else 200 if is_undercover_caught else (200 if pid == under_id else 100)
                    await add_kith_coins(int(pid), koin)
                    
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

    res = await db(lambda: supabase.table("uc_active_games").select("*").eq("game_id", game_id).execute())
    if not res.data:
        return await update.message.reply_text(f"❌ Game dengan ID {game_id} tidak ditemukan atau sudah selesai.")

    game = res.data[0]
    players = game['players']
    undercover_id = str(game['undercover_id'])
    civilian_word = game['civilian_word']
    undercover_word = game['undercover_word']
    
    await db(lambda: supabase.table("uc_active_games").update({"status": "playing"}).eq("game_id", game_id).execute())

    thread_id = update.message.message_thread_id
    group_link = await get_discussion_link(game_id, thread_id)
    btn_grup = InlineKeyboardMarkup([[InlineKeyboardButton("Kembali ke Grup", url=group_link)]])
    
    for pid in players.keys():
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

    await update.message.reply_text(
        f"▶️ *MELANJUTKAN GAME*\n"
        f"ID Game: `{game_id}`\n"
        f"Melanjutkan dari: *Ronde {start_round}*\n\n"
        f"📢 Notifikasi kelanjutan game dan pengingat kata rahasia sudah dikirim ke DM seluruh pemain yang terdaftar!", 
        parse_mode="Markdown"
    )

    asyncio.create_task(run_game_timer(GROUP_ID_DISKUSI, game_id, thread_id, context, start_round=start_round))


async def set_profile(update: Update, context: CallbackContext):
    context.user_data["keyboard_state"] = "ANON_AGE"
    
    keyboard = [
        [KeyboardButton("Legal (≥ 18)"), KeyboardButton("Minor (< 18)")],
        [KeyboardButton("❌ Cancel")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        get_wording("anon_pilih_umur"),
        parse_mode="HTML", 
        reply_markup=reply_markup
    )


async def search_anon(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    TOPIC_ID_ANON_LOG = 5417 
    
    res = await db(lambda: supabase.table("users").select("chat_state, age_group, gender, orientation").eq("user_id", user_id).execute())
    user_data = res.data[0] if res.data else None
    
    if not user_data: return await update.message.reply_text(get_wording("anon_belum_setup"), parse_mode="HTML")
    if not user_data.get('age_group') or not user_data.get('gender') or not user_data.get('orientation'):
        return await update.message.reply_text(get_wording("anon_belum_isi_profil"), parse_mode="HTML")
    if user_data.get('chat_state') != 'menfess':
        return await update.message.reply_text(get_wording("anon_masih_dalam_sesi"), parse_mode="HTML")

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
        
        log_text = (f"🔍 *Anon Match Found*\n\n👤 User 1: `{user_id}` (G:{user_data.get('gender')}, O:{user_data.get('orientation')})\n👤 User 2: `{matched_partner}` (G:{matched_partner_data.get('gender')}, O:{matched_partner_data.get('orientation')})\n━━━━━━━━━━━━━━━━\nStatus: Berhasil terhubung")
        try:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=TOPIC_ID_ANON_LOG, text=log_text, parse_mode="Markdown")
        except:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=log_text, parse_mode="Markdown")
        
        success_text = get_wording("anon_match_sukses")
        await update.message.reply_text(success_text, parse_mode="HTML", reply_markup=get_stop_anon_keyboard())
        await context.bot.send_message(chat_id=matched_partner, text=success_text, parse_mode="HTML", reply_markup=get_stop_anon_keyboard())
    else:
        await db(lambda: supabase.table("users").update({"chat_state": "searching"}).eq("user_id", user_id).execute())
        await update.message.reply_text(get_wording("anon_mencari"), parse_mode="HTML")
        asyncio.create_task(wait_for_partner_timeout(user_id, context))


async def wait_for_partner_timeout(user_id: int, context: CallbackContext):
    await asyncio.sleep(600)
    
    res = await db(lambda: supabase.table("users").select("chat_state").eq("user_id", user_id).execute())
    if res.data and res.data[0].get("chat_state") == "searching":
        await db(lambda: supabase.table("users").update({"chat_state": "menfess"}).eq("user_id", user_id).execute())
        fail_text = get_wording("anon_timeout_gagal")
        await context.bot.send_message(chat_id=user_id, text=fail_text, parse_mode="HTML", reply_markup=get_main_keyboard())


async def stop_anon(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    res = await db(lambda: supabase.table("users").select("chat_state").eq("user_id", user_id).execute())
    
    if res.data:
        state = res.data[0].get("chat_state")
        
        if state in ["searching", "chatting", "chatting_admin"]:
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes", callback_data="stop_anon_yes"),
                    InlineKeyboardButton("❌ No", callback_data="stop_anon_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                get_wording("anon_stop_confirm"), 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(get_wording("anon_stop_none"), parse_mode="HTML")


async def handle_stop_anon_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id

    if data == "stop_anon_no":
        try:
            await query.delete_message()
        except:
            await query.edit_message_text(get_wording("anon_stop_lanjut"), parse_mode="HTML")
        return

    if data == "stop_anon_yes":
        res = await db(lambda: supabase.table("users").select("chat_state, partner_id").eq("user_id", user_id).execute())
        
        if res.data:
            state = res.data[0].get("chat_state")
            partner_id = res.data[0].get("partner_id")
            
            if state in ["searching", "chatting", "chatting_admin"]:
                await db(lambda: supabase.table("users").update({"chat_state": "menfess", "partner_id": None}).eq("user_id", user_id).execute())
                
                try:
                    await query.delete_message()
                except:
                    pass
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text=get_wording("anon_left_self"),
                    parse_mode="HTML",
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
                    await context.bot.send_message(
                        chat_id=partner_id, 
                        text=get_wording("anon_left_partner"),
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard()
                    )


async def randompair_massal(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    status_msg = await update.message.reply_text("⏳ Memulai mass random pair untuk user idle dengan profil lengkap...")

    try:
        res = await db(lambda: supabase.table("users").select("*").eq("chat_state", "menfess").execute())
        users = res.data if res and hasattr(res, 'data') else []

        eligible_users = [u for u in users if u.get('age_group') and u.get('gender') and u.get('orientation')]

        if not eligible_users:
            return await status_msg.edit_text("⚠️ Tidak ada user dengan profil lengkap yang sedang idle (menfess).")

        berhasil_blast = 0
        
        for u in eligible_users:
            user_id = u['user_id']
            await db(lambda: supabase.table("users").update({"chat_state": "searching"}).eq("user_id", user_id).execute())

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🔍 *Base Closed - Sesi Anon Massal Dimulai!*\n\nMencari partner yang cocok... (Maksimal tunggu 10 menit)",
                    parse_mode="Markdown"
                )
                asyncio.create_task(wait_for_partner_timeout(user_id, context))
                berhasil_blast += 1
            except Exception as e:
                logger.error(f"Gagal kirim notif mass search ke {user_id}: {e}")

            await asyncio.sleep(0.1)

        await status_msg.edit_text(f"✅ Berhasil mengubah {berhasil_blast} user menjadi searching!\n\n⏳ Memulai proses auto-matching massal di background...")

        match_count = 0
        for u in eligible_users:
            user_id = u['user_id']
            
            cek = await db(lambda: supabase.table("users").select("chat_state").eq("user_id", user_id).execute())
            if not cek.data or cek.data[0].get("chat_state") != "searching":
                continue 

            search_res = await db(lambda: supabase.table("users").select("*").eq("chat_state", "searching").neq("user_id", user_id).execute())
            potential_partners = search_res.data if hasattr(search_res, 'data') and search_res.data else []

            matched_partner = None
            matched_partner_data = None

            for p in potential_partners:
                if p.get('age_group') != u.get('age_group'): continue

                a_gender, a_oris = u.get('gender'), u.get('orientation', '').split(',')
                b_gender, b_oris = p.get('gender'), p.get('orientation', '').split(',')
                is_match = False

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
                
                match_count += 1
                success_text = get_wording("anon_match_massal_sukses")

                try:
                    await context.bot.send_message(chat_id=user_id, text=success_text, parse_mode="HTML", reply_markup=get_stop_anon_keyboard())
                    await context.bot.send_message(chat_id=matched_partner, text=success_text, parse_mode="HTML", reply_markup=get_stop_anon_keyboard())

                    log_text = (f"🔍 *Mass Anon Match Found*\n\n👤 User 1: `{user_id}` (G:{u.get('gender')}, O:{u.get('orientation')})\n👤 User 2: `{matched_partner}` (G:{matched_partner_data.get('gender')}, O:{matched_partner_data.get('orientation')})\n━━━━━━━━━━━━━━━━\nStatus: Berhasil terhubung via /randompair")
                    try:
                        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=5417, text=log_text, parse_mode="Markdown")
                    except:
                        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=log_text, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Gagal kirim notif success match: {e}")

        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID, 
            text=f"🏁 *Mass Random Pair Selesai!*\n\nBerhasil mencocokkan: *{match_count} pasangan*. Sisa user yang belum nemu pasangan akan masuk antrean timeout 10 menit.", 
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error di randompair_massal: {e}")
        await status_msg.edit_text(f"❌ Terjadi kesalahan saat eksekusi: `{e}`", parse_mode="Markdown")


# === SISTEM LIVE PHOTO ===
def _get_video_file_from_message(msg):
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
    if not message:
        return None
    try:
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        logger.warning(f"Gagal edit pesan status Live Photo, proses tetap dilanjutkan: {e}")
        return None


async def _safe_delete_message(message):
    if not message:
        return None
    try:
        return await message.delete()
    except Exception:
        return None


async def _send_live_photo_direct(bot_token, chat_id, video_path, photo_path, message_thread_id=None, reply_to_message_id=None):
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
            get_wording("live_kirim_video_dulu"),
            reply_markup=get_main_keyboard(),
        parse_mode="HTML")

    ffmpeg_exe = "./ffmpeg" 
    
    import os
    if not os.path.isfile(ffmpeg_exe):
        return await msg.reply_text(
            get_wording("live_ffmpeg_missing"),
            reply_markup=get_main_keyboard(),
        parse_mode="HTML")
    
    os.chmod(ffmpeg_exe, 0o777)

    file_size = getattr(video, "file_size", 0) or 0
    if file_size > (LIVE_MAX_INPUT_FILE_SIZE_MB * 1024 * 1024):
        return await msg.reply_text(
            get_wording("live_video_terlalu_besar", max_mb=LIVE_MAX_INPUT_FILE_SIZE_MB),
            reply_markup=get_main_keyboard(),
        parse_mode="HTML")

    current_balance = 0
    charged = False
    try:
        # Ambil VIP via cache
        is_vip, _ = await get_vip_status(user_id)
        res = await db(lambda: supabase.table("users").select("kith_coins").eq("user_id", user_id).execute())
        row = res.data[0] if res.data else {}
        current_balance = row.get("kith_coins") or 0

        actual_price = LIVE_PHOTO_PRICE // 2 if is_vip else LIVE_PHOTO_PRICE

        if actual_price > 0 and current_balance < actual_price:
            promo = get_wording("vip_promo_title_live") if not is_vip else ""
            return await msg.reply_text(
                get_wording("live_koin_kurang", harga=actual_price, saldo=current_balance, promo=promo),
                parse_mode="HTML",
                reply_markup=get_main_keyboard(),
            )

        if actual_price > 0:
            await db(lambda: supabase.table("users").update({"kith_coins": current_balance - actual_price}).eq("user_id", user_id).execute())
            charged = True
            
    except Exception as e:
        logger.error(f"Gagal mengecek/memotong saldo Live Photo: {e}")
        return await msg.reply_text(get_wording("live_cek_saldo_gagal"), reply_markup=get_main_keyboard(), parse_mode="HTML")

    charge_text = f" <b>(Saldo dipotong {actual_price} Coins)</b>" if actual_price > 0 else ""
    status_msg = await msg.reply_text(
        get_wording("live_processing", charge_text=charge_text),
        parse_mode="HTML",
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
            {"width": 1080, "vb": "5000k", "mr": "6000k", "bs": "12000k", "ab": "128k"},
            {"width": 720, "vb": "3500k", "mr": "4200k", "bs": "8400k", "ab": "128k"},
            {"width": 540, "vb": "2200k", "mr": "2600k", "bs": "5200k", "ab": "96k"},
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
                    "-preset", "fast",
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
        await msg.reply_text(get_wording("live_sukses"), reply_markup=get_main_keyboard(), parse_mode="HTML")
        
        try:
            log_text = (
                f"📸 *Log Live Photo*\n"
                f"👤 Pengguna: {update.effective_user.first_name}\n"
                f"🆔 ID: `{user_id}`\n"
                f"💰 Biaya: {actual_price} Coins"
            )
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=TOPIC_ID_ANON_LOG,
                text=log_text,
                parse_mode="Markdown"
            )
        except Exception as log_err:
            logger.error(f"Gagal kirim log live photo: {log_err}")

    except Exception as e:
        logger.exception("Gagal live photo")
        refund_note = ""
        ulang_note = ""
        if charged:
            try:
                await db(lambda: supabase.table("users").update({"kith_coins": current_balance}).eq("user_id", user_id).execute())
                ulang_note = f"- silahkan coba kembali."
                refund_note = f"\n\nKoin kamu telah di-Refund {LIVE_PHOTO_PRICE} Coins."
            except Exception as refund_err:
                logger.error(f"Gagal refund Live Photo untuk {user_id}: {refund_err}")

        await msg.reply_text(
            get_wording("live_gagal", error=str(e)[:1500], ulang_note=ulang_note, refund_note=refund_note),
            reply_markup=get_main_keyboard(),
        parse_mode="HTML")
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
    await query.answer()
    
    data = query.data.split("_")
    if len(data) < 3: return
    
    user_id = int(data[1])
    post_id = int(data[2])

    try:
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=post_id)
        
        try:
            await db(lambda: supabase.table("menfess_map").delete().eq("post_id", post_id).execute())
        except Exception as db_err:
            logger.error(f"Gagal hapus data menfess_map di DB: {db_err}")
        
        await context.bot.send_message(
            chat_id=user_id, 
            text="❌ *Pesan kamu dihapus admin karena tidak sesuai ketentuan base. Silakan baca rules kembali.*", 
            parse_mode="Markdown"
        )

        await send_admin_log(
            context, 
            "Menghapus Menfess & Menegur User", 
            update.effective_user, 
            f"Message ID Channel: {post_id}\nUser Tujuan: `{user_id}`"
        )
        
        await query.edit_message_text(f"{query.message.text_markdown}\n\n✅ *Status: Dihapus dari Channel & Database, User ditegur.*", parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Gagal hapus menfess ID {post_id}: {e}")
        await query.answer("Gagal! Pastikan bot admin di channel & pesan masih ada.", show_alert=True)


# ==========================================
# FITUR GIVEAWAY OTOMATIS
# ==========================================

async def create_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return ConversationHandler.END
        
    keyboard = [
        [InlineKeyboardButton("🪙 Kith-Coins", callback_data="ga_coins"),
         InlineKeyboardButton("💎 VIP Status", callback_data="ga_vip")]
    ]
    await update.message.reply_text(
        "🎉 *SETUP GIVEAWAY (HYBRID)*\n\nPilih jenis hadiah yang ingin dibagikan:", 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return GA_TYPE


async def force_end_ga_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    status_msg = await update.message.reply_text("🔍 Mengecek giveaway yang sedang aktif...")

    try:
        res = await db(lambda: supabase.table("active_giveaways").select("*").eq("status", "active").execute())
        active_gas = res.data if res and hasattr(res, 'data') else []

        if not active_gas:
            return await status_msg.edit_text("❌ Saat ini tidak ada giveaway yang sedang berjalan.")

        if len(active_gas) == 1:
            ga_id = active_gas[0]['ga_id']
            await status_msg.edit_text(f"⏳ Mengakhiri otomatis giveaway `{ga_id}` sekarang...", parse_mode="Markdown")
            context.job_queue.run_once(end_giveaway, when=0, data=ga_id, name=f"manual_end_ga_{ga_id}")
        else:
            keyboard = []
            for ga in active_gas:
                hadiah_teks = f"{ga['value']} Coins" if ga['type'] == 'coins' else f"{ga['value']} Hari VIP"
                btn_text = f"ID: {ga['ga_id']} | 🎁 {hadiah_teks}"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"force_endga|{ga['ga_id']}")])

            keyboard.append([InlineKeyboardButton("❌ Batal", callback_data="force_endga|cancel")])

            await status_msg.edit_text(
                "⚠️ *Ditemukan lebih dari 1 Giveaway aktif!*\n\nSilakan pilih mana yang ingin diakhiri sekarang:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Gagal memuat list GA aktif: {e}")
        await status_msg.edit_text("❌ Terjadi kesalahan saat menghubungi database.")


async def force_end_ga_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return await query.answer("⚠️ Hanya admin yang bisa menggunakan tombol ini.", show_alert=True)

    await query.answer()
    data = query.data.split("|")
    action = data[1]

    if action == "cancel":
        return await query.edit_message_text("✅ Aksi dibatalkan. Giveaway tetap berjalan.")

    ga_id = action
    await query.edit_message_text(f"⏳ Mengakhiri giveaway `{ga_id}` sekarang...", parse_mode="Markdown")
    context.job_queue.run_once(end_giveaway, when=0, data=ga_id, name=f"manual_end_ga_{ga_id}")


async def ga_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['ga_type'] = "coins" if query.data == "ga_coins" else "vip"
    
    if context.user_data['ga_type'] == "coins":
        teks = "🪙 Berapa **nominal Kith-Coins** untuk masing-masing pemenang?\n*(Kirim angkanya saja)*"
    else:
        teks = "💎 Berapa **hari durasi VIP** untuk masing-masing pemenang?\n*(Kirim angkanya saja)*"
        
    await query.edit_message_text(teks, parse_mode="Markdown")
    return GA_VALUE


async def ga_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val <= 0: raise ValueError
        context.user_data['ga_value'] = val
        await update.message.reply_text("👥 Berapa **jumlah pemenang** yang akan dipilih?\n*(Kirim angkanya saja)*", parse_mode="Markdown")
        return GA_WINNERS
    except ValueError:
        await update.message.reply_text("❌ Harus angka bulat lebih dari 0! Silakan kirim ulang.")
        return GA_VALUE


async def ga_winners_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val <= 0: raise ValueError
        context.user_data['ga_winners'] = val
        await update.message.reply_text("⏳ Berapa **menit** durasi giveaway ini berlangsung?\n*(Kirim angkanya saja)*", parse_mode="Markdown")
        return GA_TIME
    except ValueError:
        await update.message.reply_text("❌ Harus angka bulat lebih dari 0! Silakan kirim ulang.")
        return GA_WINNERS


async def ga_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val <= 0: raise ValueError
        
        ga_id = str(uuid.uuid4())[:8]
        end_time = datetime.now(timezone.utc) + timedelta(minutes=val)
        
        hadiah_teks = f"🪙 {context.user_data['ga_value']} Kith-Coins" if context.user_data['ga_type'] == "coins" else f"💎 {context.user_data['ga_value']} Hari VIP Status"
        end_time_wib = end_time + timedelta(hours=7)
        
        text = (
            f"🎉 *GIVEAWAY ALERT!* 🎉\n\n"
            f"🎁 *Hadiah:* {hadiah_teks} (untuk {context.user_data['ga_winners']} pemenang)\n"
            f"⏳ *Berakhir:* {end_time_wib.strftime('%H:%M WIB')}\n"
            f"👥 *Partisipan:* 0\n\n"
            f"👇 *Tekan tombol di bawah untuk ikutan!*"
        )
        keyboard = [[InlineKeyboardButton("🎉 Join Giveaway", callback_data=f"joinga|{ga_id}")]]
        
        msg = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        await db(lambda: supabase.table("active_giveaways").insert({
            "ga_id": ga_id,
            "type": context.user_data['ga_type'],
            "value": context.user_data['ga_value'],
            "winners_count": context.user_data['ga_winners'],
            "end_time": end_time.isoformat(),
            "participants": {},
            "message_id": msg.message_id,
            "status": "active"
        }).execute())
        
        ACTIVE_GA_CACHE[ga_id] = {
            "type": context.user_data['ga_type'],
            "value": context.user_data['ga_value'],
            "winners_count": context.user_data['ga_winners'],
            "participants": {},
            "message_id": msg.message_id,
            "is_dirty": False
        }
        
        context.job_queue.run_once(end_giveaway, when=val * 60, data=ga_id, name=f"ga_{ga_id}")
        context.job_queue.run_repeating(sync_ga_to_db_job, interval=10, first=10, data=ga_id, name=f"sync_ga_{ga_id}")
        
        await update.message.reply_text(f"✅ Airdrop Hybrid berhasil dimulai!\n\nID Airdrop: `{ga_id}`", parse_mode="Markdown")
        context.user_data.clear()
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Harus angka bulat lebih dari 0 (menit)! Silakan kirim ulang.")
        return GA_TIME


async def cancel_ga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Pembuatan giveaway dibatalkan.")
    return ConversationHandler.END


# === JOIN CALLBACK & HYBRID RAM CACHE UPDATE ===
async def join_giveaway_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    ga_id = query.data.split("|")[1]
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    
    ga = ACTIVE_GA_CACHE.get(ga_id)
    
    if not ga:
        res = await db(lambda: supabase.table("active_giveaways").select("*").eq("ga_id", ga_id).eq("status", "active").execute())
        if not res.data:
            return await query.answer(get_wording("ga_tidak_ditemukan"), show_alert=True)
        
        row = res.data[0]
        ACTIVE_GA_CACHE[ga_id] = {
            "type": row["type"],
            "value": row["value"],
            "winners_count": row["winners_count"],
            "participants": row.get("participants", {}),
            "message_id": row["message_id"],
            "is_dirty": False
        }
        ga = ACTIVE_GA_CACHE[ga_id]

    participants = ga['participants']
    
    if user_id in participants:
        return await query.answer(get_wording("ga_join_sudah"), show_alert=True)
        
    participants[user_id] = user_name
    ga['is_dirty'] = True
    
    await query.answer(get_wording("ga_join_sukses"), show_alert=True)
    
    job_name = f"update_ga_{ga_id}"
    if not context.job_queue.get_jobs_by_name(job_name):
        context.job_queue.run_once(update_ga_board, when=3, data=ga_id, name=job_name)


async def update_ga_board(context: ContextTypes.DEFAULT_TYPE):
    ga_id = context.job.data
    ga = ACTIVE_GA_CACHE.get(ga_id)
    if not ga or not ga['message_id']: return
    
    hadiah_teks = f"🪙 {ga['value']} Kith-Coins" if ga['type'] == "coins" else f"💎 {ga['value']} Hari VIP Status"
    text = (
        f"🎉 *KITHEONS AIRDROP!* 🎉\n\n"
        f"🎁 *Hadiah:* {hadiah_teks} (untuk {ga['winners_count']} pemenang)\n"
        f"👥 *Partisipan:* {len(ga['participants'])}\n\n"
        f"👇 *Tekan tombol di bawah untuk looting!*"
    )
    keyboard = [[InlineKeyboardButton("Looting", callback_data=f"joinga|{ga_id}")]]
    
    try:
        await context.bot.edit_message_text(
            chat_id=CHANNEL_ID,
            message_id=ga['message_id'],
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Gagal update angka partisipan giveaway {ga_id}: {e}")


# === BACKGROUND SYNC JOB (MENYIMPAN KE DB SECARA BERKALA) ===
async def sync_ga_to_db_job(context: ContextTypes.DEFAULT_TYPE):
    ga_id = context.job.data
    ga = ACTIVE_GA_CACHE.get(ga_id)
    
    if ga and ga.get("is_dirty"):
        try:
            await db(lambda: supabase.table("active_giveaways").update({
                "participants": ga['participants']
            }).eq("ga_id", ga_id).execute())
            ga['is_dirty'] = False
        except Exception as e:
            logger.error(f"Gagal sync data giveaway {ga_id} ke DB: {e}")


# === DISTRIBUSI HADIAH OTOMATIS & CLEANUP ===
async def end_giveaway(context: ContextTypes.DEFAULT_TYPE):
    ga_id = context.job.data
    
    for job in context.job_queue.get_jobs_by_name(f"sync_ga_{ga_id}"):
        job.schedule_removal()

    ga = ACTIVE_GA_CACHE.get(ga_id)
    if not ga:
        res = await db(lambda: supabase.table("active_giveaways").select("*").eq("ga_id", ga_id).eq("status", "active").execute())
        if not res.data: return
        row = res.data[0]
        participants_list = list(row.get("participants", {}).items())
        val = row["value"]
        g_type = row["type"]
        winners_count = row["winners_count"]
        message_id = row["message_id"]
    else:
        participants_list = list(ga['participants'].items())
        val = ga['value']
        g_type = ga['type']
        winners_count = ga['winners_count']
        message_id = ga['message_id']
        ACTIVE_GA_CACHE.pop(ga_id, None)

    # Ubah status di database menjadi finished
    await db(lambda: supabase.table("active_giveaways").update({"status": "finished"}).eq("ga_id", ga_id).execute())

    total_winners = min(winners_count, len(participants_list))

    if total_winners == 0:
        hasil_text = "😔 *AIRDROP BERAKHIR*\n\nSayang sekali, tidak ada yang membuks airdrop ini."
    else:
        winners = random.sample(participants_list, total_winners)
        hadiah_teks = f"🪙 {val} Kith-Coins" if g_type == "coins" else f"💎 {val} Hari VIP Status"
        
        winner_mentions = []
        for uid_str, name in winners:
            uid = int(uid_str)
            winner_mentions.append(f"• [{name}](tg://user?id={uid})")
            
            try:
                if g_type == "coins":
                    await add_kith_coins(uid, val)
                    await context.bot.send_message(uid, get_wording("ga_menang_coins", val=val), parse_mode="HTML")
                
                elif g_type == "vip":
                    # Invalidate cache setelah update VIP
                    invalidate_vip_cache(uid)
                    user_res = await db(lambda u=uid: supabase.table("users").select("is_vip, vip_until").eq("user_id", u).execute())
                    row_user = user_res.data[0] if user_res.data else {}
                    is_vip = row_user.get("is_vip", False)
                    vip_until_str = row_user.get("vip_until")
                    now_utc = datetime.now(timezone.utc)
                    
                    if is_vip and vip_until_str:
                        current_expiry = datetime.fromisoformat(vip_until_str.replace("Z", "+00:00"))
                        if current_expiry < now_utc: current_expiry = now_utc
                    else:
                        current_expiry = now_utc
                        
                    new_expiry = current_expiry + timedelta(days=val)
                    await db(lambda u=uid, e=new_expiry: supabase.table("users").update({"is_vip": True, "vip_until": e.isoformat()}).eq("user_id", u).execute())
                    
                    await context.bot.send_message(uid, get_wording("ga_menang_vip", val=val), parse_mode="HTML")
            except Exception as e:
                logger.error(f"Gagal transfer hadiah GA hybrid ke {uid}: {e}")

        hasil_text = (
            f"🎊 *GIVEAWAY BERAKHIR!* 🎊\n\n"
            f"🎁 *Hadiah:* {hadiah_teks}\n"
            f"🏆 *Para Pemenang:*\n" + "\n".join(winner_mentions) + "\n\n"
            f"Selamat kepada pemenang! Hadiah sudah **otomatis ditransfer** ke akun kalian masing-masing! 🥳"
        )
        
    try:
        await context.bot.edit_message_text(
            chat_id=CHANNEL_ID,
            message_id=message_id,
            text=hasil_text,
            parse_mode="Markdown"
        )
    except Exception:
        pass
        
    await db(lambda: supabase.table("active_giveaways").delete().eq("ga_id", ga_id).execute())


async def handle_vip_fallback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == "vipfb_cancel":
        context.user_data.clear()
        await query.edit_message_text(get_wording("vip_fallback_batal"), parse_mode="HTML")
        return

    if data == "vipfb_normal":
        final_text = context.user_data.get('fallback_text')
        final_entities = context.user_data.get('fallback_entities')
        target_url = context.user_data.get('fallback_target_url')
        target_username = context.user_data.get('fallback_target_username')
        target_user_id = context.user_data.get('fallback_target_user_id')
        teks_asli = context.user_data.get('teks_menfess', "")
        display_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
        
        if not final_text:
            await query.edit_message_text("❌ Sesi telah kedaluwarsa. Silakan kirim ulang pesan dari awal.")
            return
            
        try:
            link_opts = LinkPreviewOptions(is_disabled=False, url=target_url)
            message_sent = await context.bot.send_message(
                chat_id=CHANNEL_ID, 
                text=final_text, 
                entities=final_entities, 
                link_preview_options=link_opts
            )
            sent_message_id = message_sent.message_id
            
            CACHE_COMSECT_OFF.add(sent_message_id)
            
            new_balance = await add_kith_coins(user_id, 50)
            coin_msg = f"\n💰 <b>+50 Kith-Coins!</b> (Saldo: {new_balance})" if new_balance is not None else ""
            
            try:
                await db(lambda: supabase.table("menfess_map").insert({"post_id": sent_message_id, "sender_user_id": user_id, "target_username": target_username, "target_user_id": target_user_id}).execute())
            except Exception as e:
                logger.error(f"DB Error Fallback VIP: {e}")
                
            keyboard_user = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{sent_message_id}")]]
            await query.edit_message_text(
                get_wording("vip_fallback_sukses", coin_msg=coin_msg), 
                reply_markup=InlineKeyboardMarkup(keyboard_user), 
                parse_mode="HTML"
            )
            
            log_msg = f"📌 Log Menfess (AUTO - Fallback VIP):\n🕰️ Waktu: {datetime.now(timezone.utc)}\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`\n🔗 Username Target: @{target_username}\n💬 Pesan: {teks_asli}"
            keyboard_log = [
                [InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://telegram.me/{CHANNEL_ID[1:]}/{sent_message_id}")],
                [InlineKeyboardButton("❌ Hapus & Tegur", callback_data=f"del_{user_id}_{sent_message_id}")]
            ]
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=TOPIC_ID_MENFESS_LOG, text=log_msg, reply_markup=InlineKeyboardMarkup(keyboard_log), parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Error fallback send: {e}")
            await query.edit_message_text("❌ Terjadi kesalahan saat mengirim menfess.")
            
        context.user_data.clear()


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


# === FITUR MUTE ===
async def mute_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ Format: `/mute <user_id> <hari>`\nContoh: `/mute 123456789 1`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        muted_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        await db(lambda: supabase.table("users").update({"muted_until": muted_until}).eq("user_id", target_id).execute())
        await update.message.reply_text(f"🔇 User `{target_id}` berhasil di-mute selama {days} hari.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Gagal! Pastikan ID dan hari berupa angka.")
    except Exception as e:
        logger.error(f"Error mute: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat mengeksekusi mute.")


async def handle_reaction_count_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction_count
    if not reaction:
        return

    chat = reaction.chat
    if chat.username and ("@" + chat.username.lower()) != CHANNEL_ID.lower():
        return

    post_id = reaction.message_id

    total_nangis = 0
    for r in reaction.reactions:
        emoji = getattr(r.type, "emoji", None)
        if emoji in REACTION_BAD_EMOJIS:
            total_nangis += r.total_count

    if total_nangis < REACTION_THRESHOLD or post_id in CACHE_REACTION_WARNED:
        return
    CACHE_REACTION_WARNED.add(post_id)

    try:
        res = await db(lambda: supabase.table("menfess_map").select("sender_user_id").eq("post_id", post_id).execute())
        if not (hasattr(res, "data") and res.data):
            return
        sender_user_id = res.data[0]["sender_user_id"]

        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=post_id)

        try:
            await db(lambda: supabase.table("menfess_map").delete().eq("post_id", post_id).execute())
        except Exception as db_err:
            logger.error(f"Gagal hapus menfess_map (auto-reaction): {db_err}")

        try:
            await context.bot.send_message(
                chat_id=sender_user_id,
                text=(
                    f"⚠️ <b>Menfess Kamu Dihapus Otomatis!</b>\n\n"
                    f"Pesan kamu mendapat {total_nangis} reaksi dari member dan dianggap melanggar rules base. "
                    f"Mohon perhatikan kembali isi menfess kamu sebelum mengirim ya!"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=TOPIC_ID_MENFESS_LOG,
                text=(
                    f"🗑️ <b>Auto-Delete via Reaction</b>\n"
                    f"🆔 Sender: <code>{sender_user_id}</code>\n"
                    f"📌 Post ID: {post_id}\n"
                    f"😢 Total Reaksi: {total_nangis}"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Gagal kirim log auto-delete reaction: {e}")

    except Exception as e:
        logger.error(f"Gagal proses auto-delete reaction post {post_id}: {e}")
        CACHE_REACTION_WARNED.discard(post_id)


async def unmute_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args:
        return await update.message.reply_text("⚠️ Format: `/unmute <user_id>`")

    try:
        target_id = int(context.args[0])
        await db(lambda: supabase.table("users").update({"muted_until": None}).eq("user_id", target_id).execute())
        await update.message.reply_text(f"🔊 User `{target_id}` berhasil di-unmute.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error unmute: {e}")
        await update.message.reply_text("❌ Gagal unmute user.")


async def refresh_total_coin(update: Update, context: CallbackContext):
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
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    status_msg = await update.message.reply_text("⏳ Sedang memutus sesi anonim dan me-reset data profil semua user...")

    try:
        res_active = await db(lambda: supabase.table("users").select("user_id").neq("chat_state", "menfess").execute())
        affected_users = [row["user_id"] for row in res_active.data] if res_active and hasattr(res_active, 'data') and res_active.data else []
        
        if affected_users:
            await db(lambda: supabase.table("users").update({
                "chat_state": "menfess", 
                "partner_id": None
            }).neq("chat_state", "menfess").execute())
        
        await db(lambda: supabase.table("users").update({
            "age_group": None,
            "gender": None,
            "orientation": None
        }).neq("user_id", 0).execute())
        
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
            
            await asyncio.sleep(0.1)
        
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


async def handle_vip_admin(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    action = data[0]
    target_id = int(data[1])

    if action == "vipacc":
        days = int(data[2])
        vip_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat() if days < 9000 else "2099-12-31T23:59:59+00:00"
        
        try:
            await db(lambda: supabase.table("users").update({"is_vip": True, "vip_until": vip_until}).eq("user_id", target_id).execute())
            await add_kith_coins(target_id, 5000)
            # Invalidate cache setelah update VIP
            invalidate_vip_cache(target_id)
            
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ *STATUS: DISETUJUI*", parse_mode="Markdown")
            await context.bot.send_message(
                chat_id=target_id,
                text=get_wording("vip_acc_notif"),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Gagal acc VIP: {e}")
            await query.message.reply_text(get_wording("vip_acc_gagal"), parse_mode="HTML")
            
    elif action == "viprej":
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ *STATUS: DITOLAK*", parse_mode="Markdown")
        await context.bot.send_message(
            chat_id=target_id,
            text=get_wording("vip_reject_notif"),
            parse_mode="HTML"
        )


# === FITUR GIFT VIP (BULK) ===
async def gift_vip(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    parts = update.message.text.split(maxsplit=2)
    if len(parts) < 3:
        return await update.message.reply_text(
            "⚠️ *Format salah!*\n\n"
            "Gunakan: `/giftvip <durasi_hari> <notes/pesan> @username1 @username2`\n"
            "Contoh: `/giftvip 30 Selamat ulang tahun! @jake @anna`",
            parse_mode="Markdown"
        )

    try:
        durasi_hari = int(parts[1])
        if durasi_hari <= 0:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("❌ Durasi hari harus berupa angka lebih dari 0!")

    rest_text = parts[2]

    usernames_found = re.findall(r'@([a-zA-Z0-9_]+)', rest_text)
    target_usernames = list(set([u.lower() for u in usernames_found]))

    if not target_usernames:
        return await update.message.reply_text("❌ Tidak ada username yang ditemukan. Pastikan menggunakan awalan @ (contoh: @username).")

    notes = rest_text
    for u in usernames_found:
        notes = re.sub(rf'@{u}\b', '', notes, flags=re.IGNORECASE)
    notes = notes.strip()
    if not notes:
        notes = "Semoga harimu menyenangkan!"

    status_msg = await update.message.reply_text(f"⏳ Sedang memproses gift VIP untuk {len(target_usernames)} user...")

    berhasil = []
    gagal = []

    try:
        res = await db(lambda: supabase.table("users").select("user_id, username, is_vip, vip_until").in_("username", target_usernames).execute())
        users_data = res.data if hasattr(res, 'data') and res.data else []
    except Exception as e:
        logger.error(f"Error fetching users for giftvip: {e}")
        return await status_msg.edit_text("❌ Terjadi kesalahan saat menghubungi database.")

    found_users_map = {row["username"].lower(): row for row in users_data}

    for un in target_usernames:
        if un not in found_users_map:
            gagal.append(f"@{un} (Tidak terdaftar di Bot)")
            continue
        
        user_row = found_users_map[un]
        user_id = user_row["user_id"]
        
        is_vip = user_row.get("is_vip", False)
        vip_until_str = user_row.get("vip_until")
        now_utc = datetime.now(timezone.utc)
        
        if is_vip and vip_until_str:
            current_expiry = datetime.fromisoformat(vip_until_str.replace("Z", "+00:00"))
            if current_expiry < now_utc:
                current_expiry = now_utc
        else:
            current_expiry = now_utc
            
        new_expiry = current_expiry + timedelta(days=durasi_hari)
        
        try:
            await db(lambda u=user_id, e=new_expiry: supabase.table("users").update({
                "is_vip": True, 
                "vip_until": e.isoformat()
            }).eq("user_id", u).execute())
            # Invalidate cache
            invalidate_vip_cache(user_id)
            
            berhasil.append(f"@{un}")
            
            notif_msg = (
                f"🎉 *SELAMAT! Kamu mendapatkan hadiah VIP!* 🎉\n\n"
                f"💎 *Durasi Ditambahkan:* {durasi_hari} Hari\n"
                f"📝 *Pesan dari Admin:* _{notes}_\n\n"
                f"Status Premium kamu sudah diaktifkan! Nikmati fitur VIP seperti diskon dan cooldown yang jauh lebih singkat. 🚀"
            )
            await context.bot.send_message(chat_id=user_id, text=notif_msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Gagal update/notif gift VIP untuk {un}: {e}")
            gagal.append(f"@{un} (Gagal diproses)")

    report = (
        f"🎁 *GIFT VIP SELESAI*\n\n"
        f"💎 *Durasi:* {durasi_hari} Hari\n"
        f"📝 *Notes:* {notes}\n\n"
        f"✅ *Berhasil ({len(berhasil)}):*\n" + (", ".join(berhasil) if berhasil else "-") + "\n\n"
        f"❌ *Gagal/Tidak Ditemukan ({len(gagal)}):*\n" + (", ".join(gagal) if gagal else "-")
    )
    
    await status_msg.edit_text(report, parse_mode="Markdown")


async def send_admin_log(context: CallbackContext, action: str, admin_user, details: str):
    if LOG_GROUP_ID == 0: return
    
    admin_name = admin_user.first_name
    admin_username = f"@{admin_user.username}" if admin_user.username else "Tidak ada"
    admin_id = admin_user.id
    
    text = (
        f"🚨 *ADMIN ACTIVITY LOG*\n"
        f"👤 *Oleh:* {admin_name} ({admin_username})\n"
        f"🆔 *ID Admin:* `{admin_id}`\n"
        f"🛠 *Aksi:* {action}\n"
        f"📝 *Detail:* {details}"
    )
    try:
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Gagal kirim admin log: {e}")


async def boardrep_cmd(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    if not context.args:
        return await update.message.reply_text("⚠️ Format salah!\nGunakan: `/boardrep <isi pesan tersembunyi>`", parse_mode="Markdown")

    hidden_text = " ".join(context.args)
    unique_id = str(uuid.uuid4())[:8] 
    BOARDREP_CACHE[unique_id] = hidden_text

    keyboard = [[InlineKeyboardButton("📩 Buka Pesan", callback_data=f"brep|{unique_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    target_channel = CHANNEL_ID 
    
    try:
        await context.bot.send_message(
            chat_id=target_channel,
            text="🔒 *Ada pesan rahasia yang disembunyikan!*\n\nSiapa cepat dia dapat. Klik tombol di bawah untuk membuka!",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        await update.message.reply_text("✅ Pesan boardrep berhasil dikirim ke channel!")
    except Exception as e:
        logger.error(f"Gagal mengirim boardrep: {e}")
        await update.message.reply_text("❌ Gagal mengirim pesan ke channel.")


async def handle_boardrep_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data.split("|")

    if len(data) < 2: 
        return

    unique_id = data[1]
    hidden_text = BOARDREP_CACHE.pop(unique_id, None)

    if not hidden_text:
        return await query.answer("❌ Terlambat! Pesan ini sudah dibuka oleh orang lain.", show_alert=True)

    clicker_name = update.effective_user.first_name
    new_text = f"🔓 *PESAN TERBUKA*\n\nDibuka pertama kali oleh: *{clicker_name}*\n\n📝 *Isi Pesan:*\n{hidden_text}"

    try:
        await query.edit_message_text(
            text=new_text,
            parse_mode="Markdown",
            reply_markup=None 
        )
        await query.answer("✅ Kamu adalah orang pertama yang membuka pesan ini!", show_alert=True)
    except Exception as e:
        logger.error(f"Gagal edit pesan boardrep: {e}")
        BOARDREP_CACHE[unique_id] = hidden_text
        await query.answer("❌ Terjadi kesalahan jaringan, silakan coba tekan lagi.", show_alert=True)


# ==========================================
# FITUR BOARD POLLING
# ==========================================

async def create_polling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return ConversationHandler.END
        
    if not context.args:
        await update.message.reply_text("⚠️ Gunakan format: `/polling <Judul Polling>`", parse_mode="Markdown")
        return ConversationHandler.END
        
    judul = " ".join(context.args)
    poll_id = str(uuid.uuid4())[:8]
    
    context.user_data['temp_poll_id'] = poll_id
    context.user_data['temp_poll_judul'] = judul
    
    keyboard = [
        [InlineKeyboardButton("👻 Anonim", callback_data="pollanon_yes"),
         InlineKeyboardButton("👤 Tidak Anonim", callback_data="pollanon_no")]
    ]
    await update.message.reply_text(
        "Apakah nama pengisi polling ini akan ditampilkan (Tidak Anonim) atau disembunyikan (Anonim)?", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return POLL_ANON


async def poll_anon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    is_anon = (query.data == "pollanon_yes")
    context.user_data['temp_poll_anon'] = is_anon
    
    teks_status = "Aktif" if is_anon else "Mati"
    await query.edit_message_text(f"Mode Anonim: *{teks_status}*\n\nSekarang kirimkan teks untuk tombol inline yang akan ditekan user (Contoh: `Silakan Vote!`)", parse_mode="Markdown")
    return POLL_BTN_TEXT


async def poll_btn_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btn_text = update.message.text
    poll_id = context.user_data['temp_poll_id']
    judul = context.user_data['temp_poll_judul']
    is_anon = context.user_data['temp_poll_anon']
    
    POLL_DB[poll_id] = {
        "judul": judul,
        "anon": is_anon,
        "btn_text": btn_text,
        "votes": [], 
        "voter_ids": set() 
    }
    
    bot_me = await context.bot.get_me()
    deep_link = f"https://telegram.me/{bot_me.username}?start=poll_{poll_id}"
    keyboard = [[InlineKeyboardButton(btn_text, url=deep_link)]]
    
    text_awal = f"📊 *{judul}*\n\n_Belum ada suara._"
    
    msg = await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=text_awal,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    POLL_DB[poll_id]['channel_msg_id'] = msg.message_id
    
    await save_poll_to_db(poll_id, POLL_DB[poll_id])
    
    await update.message.reply_text(f"✅ Board polling berhasil diposting ke channel!\n\nID Polling: `{poll_id}`", parse_mode="Markdown")
    
    context.user_data.pop('temp_poll_id', None)
    context.user_data.pop('temp_poll_judul', None)
    context.user_data.pop('temp_poll_anon', None)
    return ConversationHandler.END


async def cancel_polling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('temp_poll_id', None)
    await update.message.reply_text("❌ Pembuatan polling dibatalkan.")
    return ConversationHandler.END


async def update_poll_board(context: ContextTypes.DEFAULT_TYPE):
    poll_id = context.job.data
    poll = POLL_DB.get(poll_id)
    
    if not poll or 'channel_msg_id' not in poll:
        return
        
    text = f"📊 *{poll['judul']}*\n\n"
    if not poll['votes']:
        text += "_Belum ada suara._"
    else:
        for v in poll['votes']:
            text += f"*{v['name']}:* {v['text']}\n"
        
    bot_me = await context.bot.get_me()
    deep_link = f"https://telegram.me/{bot_me.username}?start=poll_{poll_id}"
    keyboard = [[InlineKeyboardButton(poll['btn_text'], url=deep_link)]]
    
    try:
        await context.bot.edit_message_text(
            chat_id=CHANNEL_ID,
            message_id=poll['channel_msg_id'],
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Gagal update board polling {poll_id}: {e}")


async def handle_reload_preview(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer("⏳ Sedang memancing preview dari server via userbot...", show_alert=False)
    
    data = query.data.split("|")
    if len(data) < 2: 
        return
        
    post_id = int(data[1])
    
    try:
        success = await reload_preview_userbot(post_id)
        if success:
            await query.answer("✅ Preview berhasil direfresh!", show_alert=True)
        else:
            await query.answer("❌ Gagal. Pesan mungkin sudah dihapus admin atau error internal.", show_alert=True)
    except Exception as e:
        logger.error(f"Gagal execute reload_preview: {e}")
        await query.answer("❌ Terjadi kesalahan saat merefresh.", show_alert=True)


async def handle_delete_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, poll_id, user_id_str = query.data.split("|")
        user_id = int(user_id_str)
    except Exception:
        return await query.answer("Format data tidak valid.", show_alert=True)
    
    poll = POLL_DB.get(poll_id)
    if not poll:
        return await query.answer("Polling ini sudah dihapus dari memory bot.", show_alert=True)
        
    original_length = len(poll['votes'])
    poll['votes'] = [v for v in poll['votes'] if v['user_id'] != user_id]
    
    if len(poll['votes']) < original_length:
        poll['voter_ids'].discard(user_id) 
        await save_poll_to_db(poll_id, poll)
        
        await query.answer("Vote terhapus! Board di channel sedang disinkronisasi.", show_alert=True)
        await query.edit_message_text(f"{query.message.text}\n\n❌ _VOTE TELAH DIHAPUS OLEH ADMIN_")
        
        job_name = f"update_poll_{poll_id}"
        if not context.job_queue.get_jobs_by_name(job_name):
            context.job_queue.run_once(update_poll_board, when=5, data=poll_id, name=job_name)
    else:
        await query.answer("Vote tidak ditemukan atau sudah dihapus sebelumnya.", show_alert=True)


async def refresh_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return

    await update.message.reply_text("⏳ Memulai kalkulasi Kith-Coins retroaktif... Mohon tunggu, proses ini butuh waktu.")
    try:
        response = await db(lambda: supabase.table("menfess_map").select("sender_user_id").execute())
        if not hasattr(response, 'data') or not response.data:
            return await update.message.reply_text("❌ Data menfess map masih kosong.")

        user_counts = {}
        for row in response.data:
            uid = row.get("sender_user_id")
            if uid:
                user_counts[uid] = user_counts.get(uid, 0) + 1

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

        upsert_data = [{"user_id": u["user_id"], "kith_coins": u["kith_coins"], "total_kith_coins": u["total_kith_coins"]} for u in batch_updates]
        await db(lambda: supabase.table("users").upsert(upsert_data).execute())

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


# ================== PERIODIC CACHE REFRESH (Step 8) ==================
async def periodic_cache_refresh(context: ContextTypes.DEFAULT_TYPE):
    """Refresh semua cache global setiap 5 menit."""
    await asyncio.gather(
        update_hashtags_cache(),
        update_badwords_cache(),
        update_banned_users_cache(),
        update_wordings_cache(),
        update_settings_cache()
    )
    logger.info("🔄 Periodic cache refresh selesai.")


def main():
    # FIX: concurrent_updates=True memungkinkan setiap update diproses sebagai asyncio.Task
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .concurrent_updates(True)
        .build()
    )
    
    # Job scheduler: proses scheduled menfess tiap menit
    application.job_queue.run_repeating(process_scheduled_menfess, interval=60)

    # Periodic refresh cache global tiap 5 menit
    application.job_queue.run_repeating(periodic_cache_refresh, interval=300, first=300)

    application.add_handler(TypeHandler(Update, global_user_tracker), group=-1)

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
    application.add_handler(CommandHandler('endga', force_end_ga_command))

    # Commands admin
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('unblock', unblock_user))
    application.add_handler(CommandHandler('mute', mute_user))
    application.add_handler(CommandHandler('unmute', unmute_user))
    application.add_handler(CommandHandler('auto', set_mode_auto))
    application.add_handler(CommandHandler('manual', set_mode_manual))
    application.add_handler(CommandHandler('cortmode', set_mode_cort))
    application.add_handler(CommandHandler('break_anon', break_all_anon))
    application.add_handler(CommandHandler('randompair', randompair_massal))
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('cekemoji', cek_emoji_pack))
    application.add_handler(CommandHandler('menu', menu))
    application.add_handler(CommandHandler('open', open_bot))
    application.add_handler(CommandHandler('close', close_bot))
    application.add_handler(CommandHandler('grupid', get_group_id))
    application.add_handler(CommandHandler('setrequired', set_required_channels))
    application.add_handler(CommandHandler('refresh_totalkoin', refresh_total_coin))
    application.add_handler(CommandHandler('refreshcoin', refresh_coin))
    application.add_handler(CommandHandler('settings2', cmd_settings_wording))

    # Fitur Profil & Leaderboard
    application.add_handler(CommandHandler('profile', cek_profile))
    application.add_handler(CommandHandler(['leaderboard', 'leadboard'], leaderboard))
    application.add_handler(CommandHandler('boardrep', boardrep_cmd))
    application.add_handler(CommandHandler('giftvip', gift_vip))

    # Fitur Game
    application.add_handler(CommandHandler('adducword', add_uc_word))
    application.add_handler(CommandHandler('undercover', start_undercover))
    application.add_handler(CommandHandler('vote', submit_word))
    application.add_handler(CommandHandler('sus', sus_vote))
    application.add_handler(CommandHandler("continue", continue_game))

    application.add_handler(CallbackQueryHandler(handle_uc_callback, pattern="^uc_"))
    application.add_handler(CallbackQueryHandler(force_end_ga_callback, pattern=r"^force_endga\|"))

    # Fitur Roleplay
    application.add_handler(CommandHandler('buytitle', buy_title))
    application.add_handler(CommandHandler('live', live_photo_handler))

    # Conversation Handler untuk Menfess
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_pesan)],
        states={
            WAITING_USERNAME: [MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_username)]
        },
        fallbacks=[CommandHandler('cancel', cancel_menfess)]
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('cancel', cancel_menfess, filters.ChatType.PRIVATE))

    # Handler Polling
    conv_polling = ConversationHandler(
        entry_points=[CommandHandler('polling', create_polling)],
        states={
            POLL_ANON: [CallbackQueryHandler(poll_anon_callback, pattern="^pollanon_")],
            POLL_BTN_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, poll_btn_text_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel_polling)]
    )
    application.add_handler(conv_polling)
    
    # Handler Giveaway
    conv_giveaway = ConversationHandler(
        entry_points=[CommandHandler('creategiveaway', create_giveaway)],
        states={
            GA_TYPE: [CallbackQueryHandler(ga_type_callback, pattern="^ga_")],
            GA_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ga_value_received)],
            GA_WINNERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ga_winners_received)],
            GA_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ga_time_received)]
        },
        fallbacks=[CommandHandler('cancelga', cancel_ga)]
    )
    application.add_handler(conv_giveaway)

    # Handler Grup (Admin & Diskusi)
    application.add_handler(CallbackQueryHandler(handle_callback_review, pattern=r"^mf\|"))
    application.add_handler(CallbackQueryHandler(handle_del_menfess, pattern=r"^del_"))
    application.add_handler(CallbackQueryHandler(handle_reload_preview, pattern=r"^reload\|"))
    application.add_handler(CallbackQueryHandler(handle_userdel_menfess, pattern=r"^userdel\|"))
    application.add_handler(CallbackQueryHandler(handle_cort_callback, pattern=r"^cort\|"))
    application.add_handler(CallbackQueryHandler(handle_stop_anon_callback, pattern=r"^stop_anon_"))
    application.add_handler(CallbackQueryHandler(handle_boardrep_callback, pattern=r"^brep\|"))
    application.add_handler(CallbackQueryHandler(handle_delete_vote, pattern=r"^delvote\|"))
    application.add_handler(CallbackQueryHandler(handle_broadcast_delete_callback, pattern=r"^delbc\|"))
    application.add_handler(CallbackQueryHandler(handle_vip_admin, pattern=r"^vip(acc|rej)\|"))
    application.add_handler(CallbackQueryHandler(handle_vip_menu, pattern=r"^(buy_vip_menu|vip_dur_|vip_cancel)"))
    application.add_handler(CallbackQueryHandler(handle_checkjoin_title, pattern=r"^checkjoin_title$"))
    application.add_handler(CallbackQueryHandler(handle_vip_fallback, pattern=r"^vipfb_"))
    application.add_handler(CallbackQueryHandler(join_giveaway_callback, pattern=r"^joinga\|"))
    application.add_handler(CallbackQueryHandler(handle_wording_settings_callback, pattern=r"^wset\|"))
    
    application.add_handler(MessageHandler(filters.ALL & filters.Chat([ADMIN_GROUP_ID, LOG_GROUP_ID]), handle_admin_reply))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_update))
    application.add_handler(MessageReactionHandler(handle_reaction_count_update))
    application.add_handler(MessageHandler(filters.Chat(GROUP_ID_DISKUSI), handle_discussion))

    # --- Handler Fitur Anon Chat ---
    application.add_handler(CommandHandler('setprofile', set_profile, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('search', search_anon, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('stop', stop_anon, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('randompair', randompair_massal))

    # Message handler untuk file, media dll
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.TEXT, handle_pesan))

    # Fallback custom command
    application.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.PRIVATE, handle_custom_command))

    logger.info("✅ Membangun bot selesai. Menjalankan polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)


if __name__ == '__main__':
    main()
