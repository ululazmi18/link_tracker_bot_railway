
import os
import sys
import csv
import io
import re
import logging
import sqlite3
from datetime import datetime

# Impor pihak ketiga
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# Muat variabel lingkungan
load_dotenv()

# Konfigurasi
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")
DB_PATH = os.getenv("DB_PATH", "link_tracker.db")
DATA_DB_PATH = os.getenv("DATA_DB_PATH", "data.db")

# Validasi Konfigurasi
if not all([API_ID, API_HASH, BOT_TOKEN]):
    print("Missing API_ID, API_HASH, or BOT_TOKEN in environment variables.")
    print("Please create a .env file with these values.")
    sys.exit(1)

# Initialize SQLite Database
def init_database():
    """Inisialisasi database SQLite dengan tabel-tabel yang diperlukan."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tabel hanya akan dibuat jika belum ada (Persistensi Data Aktif)
    
    # Create links table
    # links = link_id, owner_id, username_target, owner_code, clicks
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS links (
            link_id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            username_target TEXT NOT NULL,
            owner_code TEXT NOT NULL,
            clicks INTEGER DEFAULT 0,
            group_username TEXT,
            group_id INTEGER
        )
    ''')

    # Migrasi: Pastikan kolom baru ada jika tabel sudah dibuat sebelumnya
    try:
        cursor.execute("SELECT group_username FROM links LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating links table: adding group_username and group_id")
        cursor.execute("ALTER TABLE links ADD COLUMN group_username TEXT")
        cursor.execute("ALTER TABLE links ADD COLUMN group_id INTEGER")
    
    # Buat tabel click_stats
    # click_stats = link_id, sumber, user_id, first_name, last_name, username, language_code
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS click_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id TEXT NOT NULL,
            sumber TEXT,
            user_id INTEGER,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            language_code TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (link_id) REFERENCES links(link_id)
        )
    ''')
    
    # Buat tabel user_activity (disimpan karena membantu pelacakan aktivitas, FK diperbarui)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            chat_id INTEGER NOT NULL,
            chat_title TEXT,
            chat_username TEXT,
            owner_code TEXT NOT NULL,
            link_id TEXT NOT NULL,
            message_text TEXT,
            message_id INTEGER,
            post_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (link_id) REFERENCES links(link_id)
        )
    ''')
    
    # Migrasi sederhana: tambahkan kolom post_id jika belum ada
    try:
        cursor.execute("ALTER TABLE user_activity ADD COLUMN post_id INTEGER")
    except sqlite3.OperationalError:
        pass # Column already exists
    
    # Buat indeks untuk performa yang lebih baik
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_owner_id ON links(owner_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_link_id ON click_stats(link_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON click_stats(user_id)')
    
    # Tabel link_groups untuk multi-link support
    # Menyimpan grup link dengan nama yang diberikan user
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS link_groups (
            group_id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            group_name TEXT NOT NULL,
            owner_code TEXT NOT NULL,
            clicks INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabel link_items untuk menyimpan link-link dalam grup
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS link_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            target_url TEXT NOT NULL,
            target_type TEXT DEFAULT 'telegram',
            position INTEGER DEFAULT 0,
            FOREIGN KEY (group_id) REFERENCES link_groups(group_id)
        )
    ''')
    
    # Index untuk link_groups dan link_items
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_group_owner ON link_groups(owner_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_item_group ON link_items(group_id)')
    
    # Tabel link_group_targets untuk tracking target channel dari link group
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS link_group_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            chat_id INTEGER,
            chat_username TEXT,
            username_target TEXT,
            FOREIGN KEY (group_id) REFERENCES link_groups(group_id)
        )
    ''')

    # Index untuk link_group_targets
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_target_group ON link_group_targets(group_id)')

    conn.commit()
    conn.close()
    print(f"SQLite database initialized at {DB_PATH}")

def init_user_database():
    """Inisialisasi data.db untuk pelacakan pengguna, grup, dan anggota."""
    conn = sqlite3.connect(DATA_DB_PATH)
    cursor = conn.cursor()
    
    # Buat tabel users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            language_code TEXT,
            is_bot INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            interaction_count INTEGER DEFAULT 0
        )
    ''')
    
    # Buat tabel groups
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            chat_type TEXT,
            title TEXT,
            username TEXT,
            description TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Buat tabel members (pelacakan pasif)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            UNIQUE(chat_id, user_id)
        )
    ''')
    
    # Buat indeks untuk pencarian yang lebih cepat
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_username ON users(username)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_members_chat_id ON members(chat_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_members_user_id ON members(user_id)')
    
    conn.commit()
    conn.close()
    print(f"Data database initialized at {DATA_DB_PATH}")

# Initialize database on startup
try:
    init_database()
    init_user_database()
except Exception as e:
    print(f"Failed to initialize database: {e}")
    sys.exit(1)

# Initialize Pyrogram Client
app = Client(
    "link_tracker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# --- Helper Functions ---

def generate_owner_code() -> str:
    """Hasilkan kode 3 karakter acak (huruf kecil + angka)."""
    import random
    import string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))

def sanitize_slug(text: str) -> str:
    """Bersihkan teks untuk digunakan sebagai slug."""
    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
    slug = re.sub(r'[\s-]+', '-', slug).strip('-').lower()
    return slug[:50]

def track_user(user):
    """Lacak interaksi pengguna di data.db."""
    if not user:
        return
    
    try:
        conn = sqlite3.connect(DATA_DB_PATH)
        cursor = conn.cursor()
        
        # Periksa apakah pengguna ada
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user.id,))
        exists = cursor.fetchone()
        
        if exists:
            # Perbarui pengguna yang sudah ada
            cursor.execute('''
                UPDATE users 
                SET username = ?, 
                    first_name = ?, 
                    last_name = ?, 
                    language_code = ?,
                    last_seen = CURRENT_TIMESTAMP,
                    interaction_count = interaction_count + 1
                WHERE user_id = ?
            ''', (user.username, user.first_name, user.last_name, user.language_code, user.id))
        else:
            # Masukkan pengguna baru
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, language_code, is_bot, interaction_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            ''', (user.id, user.username, user.first_name, user.last_name, user.language_code, 1 if user.is_bot else 0))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error tracking user: {e}")

def save_group_to_db(chat):
    """Simpan/perbarui informasi grup ke data.db."""
    if not chat:
        return
    
    try:
        conn = sqlite3.connect(DATA_DB_PATH)
        cursor = conn.cursor()
        
        # Ambil tipe chat sebagai string
        chat_type = str(chat.type).replace("ChatType.", "").lower() if chat.type else "unknown"
        
        # Simpan atau perbarui grup
        cursor.execute('''
            INSERT OR REPLACE INTO groups (chat_id, chat_type, title, username, description, last_seen)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (chat.id, chat_type, chat.title, chat.username, chat.description))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving group: {e}")

def save_member_to_db(chat_id: int, user):
    """Simpan/perbarui informasi anggota ke data.db (pelacakan pasif)."""
    if not user:
        return
    
    try:
        conn = sqlite3.connect(DATA_DB_PATH)
        cursor = conn.cursor()
        
        # Periksa apakah anggota ada
        cursor.execute('SELECT id FROM members WHERE chat_id = ? AND user_id = ?', (chat_id, user.id))
        exists = cursor.fetchone()
        
        if exists:
            # Perbarui anggota yang sudah ada
            cursor.execute('''
                UPDATE members 
                SET username = ?, 
                    first_name = ?, 
                    last_name = ?,
                    last_seen = CURRENT_TIMESTAMP,
                    message_count = message_count + 1
                WHERE chat_id = ? AND user_id = ?
            ''', (user.username, user.first_name, user.last_name, chat_id, user.id))
        else:
            # Masukkan anggota baru
            cursor.execute('''
                INSERT INTO members (chat_id, user_id, username, first_name, last_name, message_count)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (chat_id, user.id, user.username, user.first_name, user.last_name))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving member: {e}")

def get_link_from_db(link_id: str):
    """Ambil link dari database SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM links WHERE link_id = ?', (link_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None

async def get_username_supergroup(client: Client, username_target: str):
    result = await client.get_chat(username_target)
    
    username = None
    chat_id = None

    if str(result.type).split('.')[1] == 'CHANNEL':
        if result.linked_chat:
            username = result.linked_chat.username
            chat_id = result.linked_chat.id
    else:
        username = result.username
        chat_id = result.id
    
    return username, chat_id

def save_link_to_db(user_id: int, username_target: str, owner_code: str, group_username: str, group_id: int):
    """Simpan link baru ke database SQLite."""
    link_id = f"{username_target}-{owner_code}"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO links (link_id, owner_id, username_target, owner_code, clicks, group_username, group_id)
        VALUES (?, ?, ?, ?, 0, ?, ?)
    ''', (link_id, user_id, username_target, owner_code, group_username, group_id))
    
    conn.commit()
    conn.close()
    return link_id

def log_click(link_id: str, user, source: str = None):
    """Log kejadian klik ke database SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tingkatkan penghitung
    cursor.execute('UPDATE links SET clicks = clicks + 1 WHERE link_id = ?', (link_id,))
    
    # Log detail
    cursor.execute('''
        INSERT INTO click_stats (link_id, sumber, user_id, first_name, last_name, username, language_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (link_id, source, user.id, user.first_name, user.last_name, user.username, user.language_code))
    
    conn.commit()
    conn.close()

async def get_user_tracked_links(user_id: int, chat_username: str, chat_id: int):
    """Get tracked links that a user clicked for a specific chat (by username or ID)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    results = []

    # 1. Single Links (Links biasa / Legacy)
    query_single = '''
        SELECT DISTINCT l.link_id, l.owner_code, l.username_target
        FROM links l
        INNER JOIN click_stats cs ON l.link_id = cs.link_id
        WHERE cs.user_id = ?
    '''
    params_single = [user_id]
    
    if chat_username and chat_id:
        query_single += " AND (l.group_id = ? OR LOWER(l.group_username) = LOWER(?))"
        params_single.extend([chat_id, chat_username.replace("@", "")])
    elif chat_username:
        query_single += " AND LOWER(l.group_username) = LOWER(?)"
        params_single.append(chat_username.replace("@", ""))
    elif chat_id:
        query_single += " AND l.group_id = ?"
        params_single.append(chat_id)
    else:
        conn.close()
        return []

    cursor.execute(query_single, params_single)
    results.extend([dict(row) for row in cursor.fetchall()])

    # 2. Multi-Link Groups (Link Groups via link_group_targets)
    # Cek link_group_targets
    query_group = '''
        SELECT DISTINCT lg.group_id as link_id, lg.owner_code, lgt.username_target
        FROM link_groups lg
        INNER JOIN click_stats cs ON lg.group_id = cs.link_id
        INNER JOIN link_group_targets lgt ON lg.group_id = lgt.group_id
        WHERE cs.user_id = ?
    '''
    params_group = [user_id]

    if chat_username and chat_id:
        query_group += " AND (lgt.chat_id = ? OR LOWER(lgt.chat_username) = LOWER(?))"
        params_group.extend([chat_id, chat_username.replace("@", "")])
    elif chat_username:
        query_group += " AND LOWER(lgt.chat_username) = LOWER(?)"
        params_group.append(chat_username.replace("@", ""))
    elif chat_id:
        query_group += " AND lgt.chat_id = ?"
        params_group.append(chat_id)
        
    cursor.execute(query_group, params_group)
    results.extend([dict(row) for row in cursor.fetchall()])
    
    conn.close()
    
    return results

def log_user_activity(user_id: int, username: str, chat_id: int, chat_title: str, 
                      chat_username: str, owner_code: str, link_id: str, 
                      message_text: str, message_id: int, post_id: int = None):
    """Log user activity in a group/channel."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Truncate message text to avoid excessive storage (max 500 chars)
    truncated_message = message_text[:500] if message_text else None
    
    cursor.execute('''
        INSERT INTO user_activity 
        (user_id, username, chat_id, chat_title, chat_username, owner_code, link_id, message_text, message_id, post_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, chat_id, chat_title, chat_username, owner_code, link_id, truncated_message, message_id, post_id))
    
    conn.commit()
    conn.close()

# --- Helper Functions untuk Link Groups ---

def check_group_name_exists(owner_id: int, group_name: str) -> bool:
    """Cek apakah user sudah punya group dengan nama yang sama."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*) FROM link_groups 
        WHERE owner_id = ? AND LOWER(group_name) = LOWER(?)
    ''', (owner_id, group_name))
    
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

def create_link_group(owner_id: int, group_name: str, owner_code: str) -> str:
    """Buat link group baru dan kembalikan group_id."""
    # Buat slug dari nama grup
    slug = sanitize_slug(group_name) or "group"
    group_id = f"{slug}-{owner_code}"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO link_groups (group_id, owner_id, group_name, owner_code)
        VALUES (?, ?, ?, ?)
    ''', (group_id, owner_id, group_name, owner_code))
    
    conn.commit()
    conn.close()
    return group_id

def get_link_group(group_id: str) -> dict:
    """Ambil data link group berdasarkan group_id."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM link_groups WHERE group_id = ?', (group_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None

def get_link_items(group_id: str) -> list:
    """Ambil semua link items dalam sebuah grup."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM link_items 
        WHERE group_id = ? 
        ORDER BY position ASC, id ASC
    ''', (group_id,))
    
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return items

def add_link_item(group_id: str, display_name: str, target_url: str, target_type: str = 'telegram') -> int:
    """Tambahkan link item ke grup dan kembalikan item id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Cari posisi terakhir
    cursor.execute('SELECT MAX(position) FROM link_items WHERE group_id = ?', (group_id,))
    max_pos = cursor.fetchone()[0]
    position = (max_pos or 0) + 1
    
    cursor.execute('''
        INSERT INTO link_items (group_id, display_name, target_url, target_type, position)
        VALUES (?, ?, ?, ?, ?)
    ''', (group_id, display_name, target_url, target_type, position))
    
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id

def delete_link_item(item_id: int) -> bool:
    """Hapus link item berdasarkan id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM link_items WHERE id = ?', (item_id,))
    deleted = cursor.rowcount > 0
    
    conn.commit()
    conn.close()
    return deleted

def get_link_item(item_id: int) -> dict:
    """Ambil data satu link item."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM link_items WHERE id = ?', (item_id,))
    row = cursor.fetchone()
    conn.close()
    
    return dict(row) if row else None

def update_link_item(item_id: int, display_name: str = None, target_url: str = None) -> bool:
    """Update data link item (nama atau url)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if display_name and target_url:
        cursor.execute('''
            UPDATE link_items 
            SET display_name = ?, target_url = ?
            WHERE id = ?
        ''', (display_name, target_url, item_id))
    elif display_name:
        cursor.execute('UPDATE link_items SET display_name = ? WHERE id = ?', (display_name, item_id))
    elif target_url:
        cursor.execute('UPDATE link_items SET target_url = ? WHERE id = ?', (target_url, item_id))
    
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def delete_link_group(group_id: str) -> bool:
    """Hapus link group beserta semua items-nya."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Hapus items dulu
    cursor.execute('DELETE FROM link_items WHERE group_id = ?', (group_id,))
    # Hapus grup
    cursor.execute('DELETE FROM link_groups WHERE group_id = ?', (group_id,))
    deleted = cursor.rowcount > 0
    
    conn.commit()
    conn.close()
    return deleted

def log_group_click(group_id: str, user, source: str = None):
    """Log klik pada link group."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Increment click counter
    cursor.execute('UPDATE link_groups SET clicks = clicks + 1 WHERE group_id = ?', (group_id,))
    
    # Log detail ke click_stats (gunakan group_id sebagai link_id untuk kompatibilitas)
    cursor.execute('''
        INSERT INTO click_stats (link_id, sumber, user_id, first_name, last_name, username, language_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (group_id, source, user.id, user.first_name, user.last_name, user.username, user.language_code))
    
    conn.commit()
    conn.close()

def save_target_channel(group_id: str, username_target: str, chat_id: int, chat_username: str):
    """Simpan target channel/group untuk tracking."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Cek apakah sudah ada
    cursor.execute('''
        SELECT id FROM link_group_targets 
        WHERE group_id = ? AND username_target = ?
    ''', (group_id, username_target))
    
    exists = cursor.fetchone()
    
    if exists:
        # Update jika ada perubahan chat_id atau chat_username
        cursor.execute('''
            UPDATE link_group_targets
            SET chat_id = ?, chat_username = ?
            WHERE id = ?
        ''', (chat_id, chat_username, exists[0]))
    else:
        # Insert baru
        cursor.execute('''
            INSERT INTO link_group_targets (group_id, chat_id, chat_username, username_target)
            VALUES (?, ?, ?, ?)
        ''', (group_id, chat_id, chat_username, username_target))
    
    conn.commit()
    conn.close()

def get_user_link_groups(owner_id: int) -> list:
    """Ambil semua link groups milik user."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT lg.*, COUNT(li.id) as item_count
        FROM link_groups lg
        LEFT JOIN link_items li ON lg.group_id = li.group_id
        WHERE lg.owner_id = ?
        GROUP BY lg.group_id
        ORDER BY lg.created_at DESC
    ''', (owner_id,))
    
    groups = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return groups

# --- Conversation State ---
user_states = {}

# --- Bot Handlers ---

@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    """Handle /start command. Can be a normal start or a deep link redirect."""
    track_user(message.from_user)
    args = message.command
    
    if len(args) > 1:
        # Deep link usage: /start group-code OR /start group-code-source
        payload = args[1]
        
        # Parse payload
        parts = payload.split('-')
        
        if len(parts) < 2:
            await message.reply_text("❌ Invalid link format.")
            return

        # Format: {slug}-{code}[-source]
        # Kode selalu 3 karakter di akhir sebelum source
        # Ambil 2 bagian pertama sebagai potential link_id
        code = parts[-1] if len(parts[-1]) == 3 else (parts[1] if len(parts) >= 2 else None)
        
        # Coba parsing yang lebih fleksibel
        # Jika ada source, code adalah part terakhir kedua dengan panjang 3
        if len(parts) >= 3 and len(parts[-2]) == 3:
            code = parts[-2]
            source = parts[-1]
            link_id = "-".join(parts[:-1])
        elif len(parts) >= 2 and len(parts[-1]) == 3:
            code = parts[-1]
            source = None
            link_id = "-".join(parts)
        else:
            # Fallback ke format lama
            target = parts[0]
            code = parts[1]
            source = "-".join(parts[2:]) if len(parts) > 2 else None
            link_id = f"{target}-{code}"
        
        # Cek dulu di link_groups (multi-link)
        group_data = get_link_group(link_id)
        
        if group_data:
            # Multi-link mode: tampilkan semua link sebagai tombol
            items = get_link_items(link_id)
            
            if not items:
                await message.reply_text("❌ This link group has no items yet.")
                return
            
            # Log klik
            try:
                log_group_click(link_id, message.from_user, source)
            except Exception as e:
                print(f"Error logging group click: {e}")
            
            # Buat tombol untuk setiap link
            buttons = []
            for item in items:
                if item['target_type'] == 'telegram':
                    url = f"https://t.me/{item['target_url'].replace('@', '')}"
                else:
                    url = item['target_url']
                buttons.append([InlineKeyboardButton(item['display_name'], url=url)])
            
            await message.reply_text(
                f"📂 **{group_data['group_name']}**\n\n"
                f"Select a link below:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            # Tidak ditemukan di grup
            await message.reply_text("❌ Link not found or expired.")
            
    else:
        # Normal start
        await message.reply_text(
            "👋 **Welcome to Link Tracker Bot!**\n\n"
            "Use /help to see all available commands.\n"
            "Use /newlinks to create a tracked multi-link collection.\n"
        )

@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    """Show help message with all available commands."""
    track_user(message.from_user)
    
    await message.reply_text(
        "📚 **Link Tracker Bot - Help**\n\n"
        "**Main Commands:**\n"
        "➕ /newlinks - Create a new link collection\n"
        "📂 /mylinks - View all your links\n"
        "📊 /export - Export click statistics\n"
        "📝 /activity - View user activity logs\n"
        "🗑 /deletegroup - Delete a link group\n\n"
        "**How to use:**\n"
        "1. Create a collection with /newlinks\n"
        "2. Add multiple links to your collection\n"
        "3. Share the referral link\n"
        "4. When clicked, users see buttons for each link\n\n"
        "💡 **Tip:** Add source tracking by appending `-source` to your link\n"
        "Example: `link-fb` for Facebook traffic"
    )

@app.on_message(filters.command(["newlinks"]))
async def add_link_handler(client: Client, message: Message):
    """Create a new multi-link collection."""
    track_user(message.from_user)
    user_id = message.from_user.id
    
    # Initialize state untuk flow baru
    user_states[user_id] = {'step': 'waiting_group_name'}
    
    await message.reply_text(
        "📂 **Create a Link Collection**\n\n"
        "Send a name for this collection:\n"
        "• Only letters, numbers, and underscores\n"
        "• No spaces allowed\n"
        "Example: `my_promo_links`\n\n"
        "Send /cancel to cancel."
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "mylinks", "export", "newlinks", "activity", "deletegroup"]))
async def text_handler(client: Client, message: Message):
    """Handle text messages for conversation."""
    track_user(message.from_user)
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    state = user_states[user_id]
    
    # Cancel handler
    if message.text == "/cancel":
        user_states.pop(user_id, None)
        await message.reply_text("❌ Cancelled.")
        return
    
    # Step 1: Waiting for Group Name (new flow)
    if state['step'] == 'waiting_group_name':
        group_name = message.text.strip()
        
        # Validasi minimal 2 karakter
        if not group_name or len(group_name) < 2:
            await message.reply_text("❌ Name must be at least 2 characters.")
            return
        
        # Fungsi untuk normalize nama menjadi format username
        def normalize_name(name: str) -> str:
            # Ganti spasi dengan underscore
            normalized = name.replace(' ', '_')
            # Hapus karakter yang tidak valid (hanya huruf, angka, underscore)
            normalized = re.sub(r'[^a-zA-Z0-9_]', '', normalized)
            # Lowercase
            normalized = normalized.lower()
            return normalized
        
        # Cek apakah nama sudah valid
        if not re.match(r'^[a-zA-Z0-9_]+$', group_name) or ' ' in group_name:
            # Normalize dan tawarkan saran
            suggested_name = normalize_name(group_name)
            
            if len(suggested_name) < 2:
                await message.reply_text("❌ Cannot create a valid name from your input. Please try again.")
                return
            
            # Simpan saran ke state
            user_states[user_id] = {
                'step': 'confirm_group_name',
                'suggested_name': suggested_name,
                'original_name': group_name
            }
            
            await message.reply_text(
                f"📝 **Suggested name:**\n`{suggested_name}`\n\n"
                f"Do you want to use this name?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, use it", callback_data=f"confirmname_{suggested_name}")],
                    [InlineKeyboardButton("❌ No, I'll type another", callback_data="cancelname")]
                ])
            )
            return
        
        # Nama sudah valid, cek apakah sudah ada
        final_name = group_name.lower()
        
        if check_group_name_exists(user_id, final_name):
            await message.reply_text(
                f"❌ You already have a collection named `{final_name}`.\n"
                "Please use a different name."
            )
            return
        
        # Lanjut buat group
        owner_code = generate_owner_code()
        
        try:
            group_id = create_link_group(
                owner_id=user_id,
                group_name=final_name,
                owner_code=owner_code
            )
        except Exception as e:
            print(f"DB Error: {e}")
            await message.reply_text("An error occurred while creating the link group.")
            user_states.pop(user_id, None)
            return
        
        # Simpan group_id ke state untuk langkah selanjutnya
        user_states[user_id] = {
            'step': 'managing_group',
            'group_id': group_id,
            'group_name': group_name.lower()
        }
        
        # Tampilkan menu manajemen grup
        await send_group_management_menu(client, message.chat.id, group_id, group_name.lower())
    
    # Step 2: Adding a link item (waiting for display name)
    elif state['step'] == 'waiting_item_name':
        display_name = message.text.strip()
        
        if not display_name:
            await message.reply_text("❌ Please enter a valid display name.")
            return
        
        user_states[user_id]['item_name'] = display_name
        user_states[user_id]['step'] = 'waiting_item_url'
        
        await message.reply_text(
            "🔗 **Send the target URL**\n\n"
            "Examples:\n"
            "• `@username` (Telegram)\n"
            "• `t.me/username`\n"
            "• `https://example.com`\n\n"
            "Send /cancel to cancel."
        )
    
    # Step 3: Adding a link item (waiting for URL)
    elif state['step'] == 'waiting_item_url':
        input_url = message.text.strip()
        group_id = state.get('group_id')
        display_name = state.get('item_name')
        
        if not input_url:
            await message.reply_text("❌ Please enter a valid URL.")
            return
        
        # Tentukan tipe link dan proses URL
        target_type = 'telegram'
        target_url = input_url
        
        lower_url = input_url.lower()

        if any(domain in lower_url for domain in ['twitter.com', 'x.com']):
            target_type = 'x'
            target_url = input_url if input_url.startswith('http') else f'https://{input_url}'
        elif any(domain in lower_url for domain in ['discord.com', 'discord.gg']):
            target_type = 'discord'
            target_url = input_url if input_url.startswith('http') else f'https://{input_url}'
        elif 'reddit.com' in lower_url:
            target_type = 'reddit'
            target_url = input_url if input_url.startswith('http') else f'https://{input_url}'
        elif 'tiktok.com' in lower_url:
            target_type = 'tiktok'
            target_url = input_url if input_url.startswith('http') else f'https://{input_url}'
        elif any(domain in lower_url for domain in ['youtube.com', 'youtu.be']):
            target_type = 'youtube'
            target_url = input_url if input_url.startswith('http') else f'https://{input_url}'
        elif input_url.startswith('http') and 't.me/' not in input_url:
            # External URL
            target_type = 'external'
            target_url = input_url
        elif 't.me/' in input_url:
            # Telegram link
            target_url = input_url.split('t.me/')[-1].split('/')[0].split('?')[0]
        else:
            # Username
            target_url = input_url.replace('@', '')
        
        try:
            add_link_item(group_id, display_name, target_url, target_type)
            
            # Jika target adalah Telegram, simpan info target channelnya
            if target_type == 'telegram':
                # Resolve username untuk dapat chat_id real
                # target_url disini sudah bersih (username tanpa @)
                try:
                    real_username, real_chat_id = await get_username_supergroup(client, target_url)
                    if real_chat_id:
                        save_target_channel(group_id, target_url, real_chat_id, real_username)
                except Exception as e:
                    print(f"Failed to resolve target channel {target_url}: {e}")
                    # Tetap lanjut, mungkin user bot belum join atau private
            
        except Exception as e:
            print(f"DB Error: {e}")
            await message.reply_text("An error occurred while adding the link.")
            return
        
        # Kembali ke state managing_group
        user_states[user_id] = {
            'step': 'managing_group',
            'group_id': group_id,
            'group_name': state.get('group_name')
        }
        
        await message.reply_text(f"✅ Link **{display_name}** added!")
        await send_group_management_menu(client, message.chat.id, group_id, state.get('group_name'))
    
    # Handle Edit Name
    elif state['step'] == 'waiting_edit_name':
        new_name = message.text.strip()
        item_id = state['item_id']
        group_id = state['group_id']
        
        if not new_name:
            await message.reply_text("❌ Please enter a valid name.")
            return
            
        update_link_item(item_id, display_name=new_name)
        
        # Kembali ke menu edit item tersebut
        # Kita butuh group_name untuk menu, karena struct state sekarang beda, kita ambil lagi
        group_data = get_link_group(group_id)
        
        await message.reply_text(f"✅ Name updated to: **{new_name}**")
        
        # Reset state
        user_states[user_id] = {
            'step': 'managing_group',
            'group_id': group_id,
            'group_name': group_data['group_name']
        }
        
        # Tampilkan menu pilihan edit lagi
        item = get_link_item(item_id)
        await client.send_message(
            message.chat.id,
            f"✏️ **Editing: {item['display_name']}**\n"
            f"URL: `{item['target_url']}`\n\n"
            "What do you want to edit?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Change Name", callback_data=f"editname_{item_id}_{group_id}")],
                [InlineKeyboardButton("Change URL", callback_data=f"editurl_{item_id}_{group_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"edititem_{group_id}")]
            ])
        )

    # Handle Edit URL
    elif state['step'] == 'waiting_edit_url':
        input_url = message.text.strip()
        item_id = state['item_id']
        group_id = state['group_id']
        
        if not input_url:
            await message.reply_text("❌ Please enter a valid URL.")
            return

        # Proses URL sama seperti saat adding
        target_type = 'telegram'
        target_url = input_url
        
        if "t.me/" in input_url or "telegram.me/" in input_url:
            target_url = input_url.replace("https://", "").replace("http://", "")
            if "t.me/" in target_url:
                target_url = target_url.split("t.me/")[1]
            elif "telegram.me/" in target_url:
                target_url = target_url.split("telegram.me/")[1]
        elif input_url.startswith("@"):
            target_url = input_url
        elif not input_url.startswith("http"):
            # Asumsi external link tanpa http
            target_type = 'external'
            target_url = f"https://{input_url}"
        else:
            target_type = 'external'
        
        update_link_item(item_id, target_url=target_url)

        # Kembali ke menu edit item tersebut
        group_data = get_link_group(group_id)
        
        await message.reply_text(f"✅ URL updated to: `{target_url}`")
        
        # Reset state
        user_states[user_id] = {
            'step': 'managing_group',
            'group_id': group_id,
            'group_name': group_data['group_name']
        }
        
        # Tampilkan menu pilihan edit lagi
        item = get_link_item(item_id)
        await client.send_message(
            message.chat.id,
            f"✏️ **Editing: {item['display_name']}**\n"
            f"URL: `{item['target_url']}`\n\n"
            "What do you want to edit?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Change Name", callback_data=f"editname_{item_id}_{group_id}")],
                [InlineKeyboardButton("Change URL", callback_data=f"editurl_{item_id}_{group_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"edititem_{group_id}")]
            ])
        )
    
    # Legacy: waiting_target (single link - deprecated but kept for compatibility)
    elif state['step'] == 'waiting_target':
        input_text = message.text.strip()
        
        # Extract username
        username_target = ""
        if "t.me/" in input_text:
            username_target = input_text.split("t.me/")[-1].split("/")[0].split("?")[0]
        else:
            username_target = input_text.replace("@", "").replace("https://", "")
        
        # Basic validation
        username_target = re.sub(r'[^a-zA-Z0-9_]', '', username_target)
        
        if not username_target:
             await message.reply_text("❌ Invalid username. Please send a valid Telegram username or link.")
             return

        owner_code = generate_owner_code()
        
        username, chat_id = await get_username_supergroup(client, username_target)
        # Save to DB
        try:
            link_id = save_link_to_db(
                user_id=user_id,
                username_target=username_target,
                owner_code=owner_code,
                group_username=username,
                group_id=chat_id
            )
        except Exception as e:
            print(f"DB Error: {e}")
            await message.reply_text("An error occurred while saving the link.")
            user_states.pop(user_id, None)
            return

        final_link = f"https://t.me/{BOT_USERNAME}?start={link_id}"
        example_source_link = f"{final_link}-fb"

        await message.reply_text(
            f"✅ **Link Created!**\n\n"
            f"🎯 **Target:** @{username_target}\n"
            f"🔗 **Referral Link:** \n   `{final_link}`\n   `[@NAME]({final_link})`\n"
            f"🔗 **With Source Example (e.g. fb):** \n   `{example_source_link}`\n",
            disable_web_page_preview=True
        )
        
        # Clear state
        user_states.pop(user_id, None)

async def send_group_management_menu(client: Client, chat_id: int, group_id: str, group_name: str, message_to_edit: Message = None):
    """Helper untuk menampilkan menu manajemen grup."""
    items = get_link_items(group_id)
    
    # Buat list link yang sudah ditambahkan
    items_text = ""
    if items:
        for i, item in enumerate(items, 1):
            items_text += f"{i}. {item['display_name']} → {item['target_url']}\n"
    else:
        items_text = "_No links added yet_\n"
    
    final_link = f"https://t.me/{BOT_USERNAME}?start={group_id}"
    
    text = (
        f"📂 **{group_name}**\n\n"
        f"**Links:**\n{items_text}\n"
        f"🔗 **Referral Link:**\n`{final_link}`\n   `[@NAME]({final_link})`\n\n"
        f"Use the buttons below to manage links:"
    )
    
    buttons = [
        [InlineKeyboardButton("➕ Add Link", callback_data=f"additem_{group_id}")],
        [InlineKeyboardButton("✏️ Edit Link", callback_data=f"edititem_{group_id}")],
        [InlineKeyboardButton("🗑 Delete Link", callback_data=f"delitem_{group_id}")],
        [InlineKeyboardButton("✅ Done", callback_data=f"donegroup_{group_id}")]
    ]
    
    markup = InlineKeyboardMarkup(buttons)
    
    if message_to_edit:
        await message_to_edit.edit_text(text, reply_markup=markup)
    else:
        await client.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)

@app.on_callback_query(filters.regex(r"^confirmname_"))
async def confirm_name_callback(client: Client, callback_query):
    """Handle confirmation of suggested name."""
    suggested_name = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    # Cek apakah nama sudah ada
    if check_group_name_exists(user_id, suggested_name):
        user_states[user_id] = {'step': 'waiting_group_name'}
        await callback_query.message.edit_text(
            f"❌ You already have a collection named `{suggested_name}`.\n"
            "Please send a different name.\n\n"
            "Send /cancel to cancel."
        )
        return
    
    owner_code = generate_owner_code()
    
    try:
        group_id = create_link_group(
            owner_id=user_id,
            group_name=suggested_name,
            owner_code=owner_code
        )
    except Exception as e:
        print(f"DB Error: {e}")
        await callback_query.message.edit_text("An error occurred while creating the link group.")
        user_states.pop(user_id, None)
        return
    
    # Set state untuk manajemen grup
    user_states[user_id] = {
        'step': 'managing_group',
        'group_id': group_id,
        'group_name': suggested_name
    }
    
    # Tampilkan menu manajemen grup
    await send_group_management_menu(
        client, 
        callback_query.message.chat.id, 
        group_id, 
        suggested_name,
        message_to_edit=callback_query.message
    )

@app.on_callback_query(filters.regex(r"^cancelname$"))
async def cancel_name_callback(client: Client, callback_query):
    """Handle cancellation of suggested name."""
    user_id = callback_query.from_user.id
    
    # Reset state ke waiting_group_name
    user_states[user_id] = {'step': 'waiting_group_name'}
    
    await callback_query.message.edit_text(
        "📂 **Create a Link Collection**\n\n"
        "Send a name for this collection:\n"
        "• Only letters, numbers, and underscores\n"
        "• No spaces allowed\n"
        "Example: `my_promo_links`\n\n"
        "Send /cancel to cancel."
    )

@app.on_callback_query(filters.regex(r"^edititem_"))
async def edit_item_menu_callback(client: Client, callback_query):
    """Show menu to select item to edit."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    # Verify ownership
    group_data = get_link_group(group_id)
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    items = get_link_items(group_id)
    
    if not items:
        await callback_query.answer("No links to edit.", show_alert=True)
        return
    
    # Buat tombol untuk setiap item
    buttons = []
    for item in items:
        buttons.append([InlineKeyboardButton(
            f"✏️ {item['display_name']}", 
            callback_data=f"editsel_{item['id']}_{group_id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"backgroup_{group_id}")])
    
    await callback_query.message.edit_text(
        "✏️ **Select a link to edit:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex(r"^editsel_"))
async def edit_select_callback(client: Client, callback_query):
    """Show edit options for selected item."""
    parts = callback_query.data.split("_")
    item_id = int(parts[1])
    group_id = "_".join(parts[2:])
    user_id = callback_query.from_user.id
    
    item = get_link_item(item_id)
    if not item:
        await callback_query.answer("Item not found.", show_alert=True)
        return
        
    await callback_query.message.edit_text(
        f"✏️ **Editing: {item['display_name']}**\n"
        f"URL: `{item['target_url']}`\n\n"
        "What do you want to edit?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Change Name", callback_data=f"editname_{item_id}_{group_id}")],
            [InlineKeyboardButton("Change URL", callback_data=f"editurl_{item_id}_{group_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"edititem_{group_id}")]
        ])
    )

@app.on_callback_query(filters.regex(r"^editname_"))
async def edit_name_callback(client: Client, callback_query):
    """Prepare to edit name."""
    parts = callback_query.data.split("_")
    item_id = int(parts[1])
    group_id = "_".join(parts[2:])
    user_id = callback_query.from_user.id
    
    # Set state
    user_states[user_id] = {
        'step': 'waiting_edit_name',
        'item_id': item_id,
        'group_id': group_id
    }
    
    await callback_query.message.edit_text(
        "📝 **Enter new name:**\n"
        "Send the new display name for this link.\n\n"
        "Send /cancel to cancel."
    )

@app.on_callback_query(filters.regex(r"^editurl_"))
async def edit_url_callback(client: Client, callback_query):
    """Prepare to edit URL."""
    parts = callback_query.data.split("_")
    item_id = int(parts[1])
    group_id = "_".join(parts[2:])
    user_id = callback_query.from_user.id
    
    # Set state
    user_states[user_id] = {
        'step': 'waiting_edit_url',
        'item_id': item_id,
        'group_id': group_id
    }
    
    await callback_query.message.edit_text(
        "🔗 **Enter new URL:**\n"
        "Send the new target URL or username (@username).\n\n"
        "Send /cancel to cancel."
    )

@app.on_callback_query(filters.regex(r"^additem_"))
async def add_item_callback(client: Client, callback_query):
    """Handle add item button."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    # Verify ownership
    group_data = get_link_group(group_id)
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    # Set state untuk menunggu nama link
    user_states[user_id] = {
        'step': 'waiting_item_name',
        'group_id': group_id,
        'group_name': group_data['group_name']
    }
    
    await callback_query.message.edit_text(
        "➕ **Add a new link**\n\n"
        "Send the display name for this link:\n"
        "Example: `Join Channel`\n\n"
        "Send /cancel to cancel."
    )

@app.on_callback_query(filters.regex(r"^delitem_"))
async def delete_item_menu_callback(client: Client, callback_query):
    """Show menu to select item to delete."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    # Verify ownership
    group_data = get_link_group(group_id)
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    items = get_link_items(group_id)
    
    if not items:
        # Tidak ada link, tawarkan untuk hapus grup
        await callback_query.message.edit_text(
            f"📂 **{group_data['group_name']}**\n\n"
            "This collection has no links.\n"
            "Do you want to delete the entire collection?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Yes, delete collection", callback_data=f"delgrpconf_{group_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"backgroup_{group_id}")]
            ])
        )
        return
    
    # Buat tombol untuk setiap item
    buttons = []
    for item in items:
        buttons.append([InlineKeyboardButton(
            f"🗑 {item['display_name']}", 
            callback_data=f"rmitem_{item['id']}_{group_id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"backgroup_{group_id}")])
    
    await callback_query.message.edit_text(
        "🗑 **Select a link to delete:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex(r"^rmitem_"))
async def remove_item_callback(client: Client, callback_query):
    """Handle item deletion."""
    parts = callback_query.data.split("_")
    item_id = int(parts[1])
    group_id = "_".join(parts[2:])
    user_id = callback_query.from_user.id
    
    # Verify ownership
    group_data = get_link_group(group_id)
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    delete_link_item(item_id)
    await callback_query.answer("✅ Link deleted!")
    
    # Kembali ke menu manajemen
    await send_group_management_menu(
        client, 
        callback_query.message.chat.id, 
        group_id, 
        group_data['group_name'],
        message_to_edit=callback_query.message
    )

@app.on_callback_query(filters.regex(r"^backgroup_"))
async def back_to_group_callback(client: Client, callback_query):
    """Go back to group management menu."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    group_data = get_link_group(group_id)
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    await send_group_management_menu(
        client,
        callback_query.message.chat.id,
        group_id,
        group_data['group_name'],
        message_to_edit=callback_query.message
    )

@app.on_callback_query(filters.regex(r"^donegroup_"))
async def done_group_callback(client: Client, callback_query):
    """Finish group creation/editing."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    # Clear state
    user_states.pop(user_id, None)
    
    group_data = get_link_group(group_id)
    if not group_data:
        await callback_query.answer("Group not found.", show_alert=True)
        return
    
    items = get_link_items(group_id)
    final_link = f"https://t.me/{BOT_USERNAME}?start={group_id}"
    
    items_text = ""
    if items:
        for i, item in enumerate(items, 1):
            items_text += f"{i}. {item['display_name']}\n"
    else:
        items_text = "_No links_\n"
    
    
    # Redirect ke tampilan menu group (seperti showgroup_)
    await callback_query.message.edit_text(
        f"📂 **{group_data['group_name']}**\n\n"
        f"**Links:**\n{items_text}\n"
        f"🔗 **Referral Link:**\n`{final_link}`\n   `[@NAME]({final_link})`\n"
        f"� **Total Clicks:** {group_data['clicks']}\n\n"
        f"💡 Add source: `{final_link}-fb`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Links", callback_data=f"editgroup_{group_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_mylinks")]
        ]),
        disable_web_page_preview=True
    )

async def send_mylinks_menu(client: Client, chat_id: int, user_id: int, message_to_edit: Message = None):
    """Helper to show My Links menu (Groups Only)."""
    # Get link groups
    groups = get_user_link_groups(user_id)
    
    if not groups:
        text = "You haven't created any link collections yet.\nUse /newlinks to create a new one."
        if message_to_edit:
            await message_to_edit.edit_text(text)
        else:
            await client.send_message(chat_id, text)
        return

    buttons = []
    
    # Show link groups
    for g in groups:
        display_text = f"📂 {g['group_name']} ({g['clicks']} clicks, {g['item_count']} links)"
        buttons.append([InlineKeyboardButton(display_text, callback_data=f"showgroup_{g['group_id']}")])
    
    text = "📂 **Select a link collection to view info:**"
    markup = InlineKeyboardMarkup(buttons)
    
    if message_to_edit:
        await message_to_edit.edit_text(text, reply_markup=markup)
    else:
        await client.send_message(chat_id, text, reply_markup=markup)

@app.on_callback_query(filters.regex(r"^showgroup_"))
async def show_group_callback(client: Client, callback_query):
    """Show link group details."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    group_data = get_link_group(group_id)
    
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Group not found or access denied.", show_alert=True)
        return
    
    items = get_link_items(group_id)
    final_link = f"https://t.me/{BOT_USERNAME}?start={group_id}"
    
    items_text = ""
    if items:
        for i, item in enumerate(items, 1):
            items_text += f"{i}. {item['display_name']} → {item['target_url']}\n"
    else:
        items_text = "_No links_\n"
    
    await callback_query.message.edit_text(
        f"📂 **{group_data['group_name']}**\n\n"
        f"**Links:**\n{items_text}\n"
        f"🔗 **Referral Link:**\n`{final_link}`\n   `[@NAME]({final_link})`\n"
        f"📊 **Total Clicks:** {group_data['clicks']}\n\n"
        f"💡 Add source: `{final_link}-fb`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Links", callback_data=f"editgroup_{group_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_mylinks")]
        ]),
        disable_web_page_preview=True
    )

@app.on_callback_query(filters.regex(r"^editgroup_"))
async def edit_group_callback(client: Client, callback_query):
    """Edit link group - show management menu."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    group_data = get_link_group(group_id)
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    # Set state
    user_states[user_id] = {
        'step': 'managing_group',
        'group_id': group_id,
        'group_name': group_data['group_name']
    }
    
    await send_group_management_menu(
        client,
        callback_query.message.chat.id,
        group_id,
        group_data['group_name'],
        message_to_edit=callback_query.message
    )

@app.on_message(filters.command("mylinks"))
async def mylinks_handler(client: Client, message: Message):
    """List all target usernames to select from."""
    track_user(message.from_user)
    user_id = message.from_user.id
    await send_mylinks_menu(client, message.chat.id, user_id)

@app.on_callback_query(filters.regex(r"^showlink_"))
async def show_link_callback(client: Client, callback_query):
    target = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM links 
        WHERE owner_id = ? AND username_target = ?
    ''', (user_id, target))
    
    link = cursor.fetchone()
    conn.close()
    
    if not link:
        await callback_query.answer("Link not found.", show_alert=True)
        return
        
    link_data = dict(link)
    link_id = link_data['link_id']
    username_target = link_data['username_target']
    
    final_link = f"https://t.me/{BOT_USERNAME}?start={link_id}"
    
    await callback_query.message.edit_text(
        f"🎯 **Target:** @{username_target}\n\n"
        f"🔗 **Link:** `{final_link}`\n"
        f"📊 **Total Clicks:** {link_data.get('clicks', 0)}\n\n"
        f"To track source, append `-somename` to the link.\n"
        f"Ex: `{final_link}-twitter`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_mylinks")]
        ])
    )

@app.on_callback_query(filters.regex(r"^back_mylinks"))
async def back_mylinks_callback(client: Client, callback_query):
    # Re-trigger mylinks logic using the helper
    # CORRECT FIX: Use callback_query.from_user.id for user_id
    await send_mylinks_menu(
        client, 
        callback_query.message.chat.id, 
        callback_query.from_user.id, 
        message_to_edit=callback_query.message
    )

@app.on_message(filters.command("export"))
async def export_handler(client: Client, message: Message):
    """Export click stats to CSV."""
    track_user(message.from_user)
    user_id = message.from_user.id
    
    # Get Link Groups
    groups = get_user_link_groups(user_id)
    
    if not groups:
        await message.reply_text("No link collections found to export.")
        return

    # Create buttons
    buttons = []
    for g in groups:
        btn_text = f"📂 {g['group_name']} ({g['clicks']} clicks)"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"export_{g['group_id']}")])

    await message.reply_text(
        "📊 **Select a link collection to export data:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex(r"^export_"))
async def export_callback(client: Client, callback_query):
    """Callback export click stats (Groups Only)."""
    try:
        doc_id = callback_query.data.split("_", 1)[1]
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Ensure target is Link Group
        cursor.execute('SELECT * FROM link_groups WHERE group_id = ?', (doc_id,))
        group_row = cursor.fetchone()
        
        if not group_row:
             conn.close()
             await callback_query.answer("Link collection not found.", show_alert=True)
             return
             
        if group_row['owner_id'] != callback_query.from_user.id:
             conn.close()
             await callback_query.answer("Access denied.", show_alert=True)
             return
             
        link_data = dict(group_row)
        export_name = link_data.get('group_name', doc_id)

        await callback_query.message.edit_text("⏳ Generating CSV & Summary...")

        # Ambil statistik klik
        cursor.execute('''
            SELECT sumber, user_id, first_name, username, language_code, timestamp
            FROM click_stats 
            WHERE link_id = ? 
            ORDER BY timestamp DESC
        ''', (doc_id,))
        
        stats = cursor.fetchall()
        conn.close()
        
        if len(stats) == 0:
            await callback_query.message.edit_text("No clicks recorded for this group yet.")
            return
        
        # 1. BUAT CSV (Pengguna Unik dengan Data Aktivitas)
        output_csv = io.StringIO()
        writer = csv.writer(output_csv)
        # Menghapus 'Join Status' karena tidak relevan untuk grup
        writer.writerow(['User ID', 'First Name', 'Username', 'Language', 'First Click', 'Activity Count'])

        # Ambil pengguna unik
        conn = sqlite3.connect(DB_PATH) # Buka kembali
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, first_name, username, language_code, MIN(timestamp) as first_click
            FROM click_stats 
            WHERE link_id = ? 
            GROUP BY user_id
            ORDER BY first_click DESC
        ''', (doc_id,))
        
        unique_users = cursor.fetchall()
        
        # Hitung sumber lalu lintas dan user unik per sumber
        cursor.execute('''
            SELECT sumber, COUNT(*) as total, COUNT(DISTINCT user_id) as unique_users 
            FROM click_stats 
            WHERE link_id = ? 
            GROUP BY sumber
        ''', (doc_id,))
        source_data = cursor.fetchall()

        conn.close() 
        
        # Proses Data Pengayaan
        for user in unique_users:
            uid = user['user_id']
            
            # Hitung aktivitas pengguna
            # Gunakan link_id (group_id) atau owner_code untuk mencocokkan aktivitas
            conn_act = sqlite3.connect(DB_PATH)
            cursor_act = conn_act.cursor()
            cursor_act.execute('SELECT COUNT(*) FROM user_activity WHERE (link_id = ? OR owner_code = ?) AND user_id = ?', (doc_id, link_data.get('owner_code'), uid))
            act_count = cursor_act.fetchone()[0]
            conn_act.close()

            writer.writerow([
                uid, 
                user['first_name'], 
                user['username'] or "", 
                user['language_code'], 
                user['first_click'], 
                act_count
            ])
            
        output_csv.seek(0)
        
        # 2. BUAT RINGKASAN (File Teks)
        output_txt = io.StringIO()
        output_txt.write(f"Export Report for: {export_name}\n")
        output_txt.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        output_txt.write(f"Total Clicks (All Time): {len(stats)}\n")
        output_txt.write(f"Unique Users: {len(unique_users)}\n\n")
        
        output_txt.write("Traffic Sources (Total Clicks - Unique Users):\n")
        for row in source_data:
            src = row['sumber'] or "None"
            output_txt.write(f"- {src}: {row['total']} ({row['unique_users']})\n")
            
        output_txt.seek(0)

        # Kirim File
        date_str = datetime.now().strftime("%Y%m%d")
        safe_name = "".join(x for x in export_name if x.isalnum() or x in ('_','-'))
        filename = f"export_{safe_name}_{date_str}.csv"
        summary_filename = f"summary_{safe_name}_{date_str}.txt"
        
        # CSV
        bio_csv = io.BytesIO(output_csv.getvalue().encode('utf-8'))
        bio_csv.name = filename
        
        # Ringkasan
        bio_txt = io.BytesIO(output_txt.getvalue().encode('utf-8'))
        bio_txt.name = summary_filename
        
        await client.send_document(
            chat_id=callback_query.message.chat.id,
            document=bio_csv,
            file_name=filename,
            caption=f"📊 **Export Data for:** `{export_name}`\n\nIncluded: CSV (Detailed) and Summary Report.",
            reply_to_message_id=callback_query.message.reply_to_message.id if callback_query.message.reply_to_message else None
        )
        
        await client.send_document(
            chat_id=callback_query.message.chat.id,
            document=bio_txt,
            file_name=summary_filename,
            caption="📄 **Summary Report**",
            reply_to_message_id=callback_query.message.reply_to_message.id if callback_query.message.reply_to_message else None
        )
        
        await callback_query.message.delete()

    except Exception as e:
        print(f"Error in export_callback: {e}")
        await callback_query.message.edit_text(f"❌ An error occurred during export: {e}")



@app.on_message(filters.command("activity"))
async def activity_handler(client: Client, message: Message):
    """Export user activity data for tracked links."""
    try:
        track_user(message.from_user)
        user_id = message.from_user.id
        
        # 1. Get Multi-Link Groups
        groups = get_user_link_groups(user_id)
        
        if not groups:
            await message.reply_text("No links found for activity tracking.")
            return

        # Membuat tombol
        buttons = []
        
        # Tambahkan Grup
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for g in groups:
            # Ambil jumlah aktivitas untuk grup ini
            cursor.execute('SELECT COUNT(*) FROM user_activity WHERE link_id = ? OR owner_code = ?', (g['group_id'], g['owner_code']))
            act_count = cursor.fetchone()[0]
            
            btn_text = f"📂 {g['group_name']} ({act_count})"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"activity_{g['group_id']}")])
        conn.close()

        await message.reply_text(
            "📊 **Select a link collection to export activity:**\n",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        print(f"Error in activity handler: {e}")
        await message.reply_text("An error occurred. Please try again later.")

@app.on_callback_query(filters.regex(r"^activity_"))
async def activity_callback(client: Client, callback_query):
    """Callback untuk export data aktivitas (Advanced Tracking)."""
    try:
        doc_id = callback_query.data.split("_", 1)[1]
        user_id = callback_query.from_user.id
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Ensure target is Link Group
        cursor.execute('SELECT * FROM link_groups WHERE group_id = ?', (doc_id,))
        group_row = cursor.fetchone()
        
        if not group_row:
             conn.close()
             await callback_query.answer("Link collection not found.", show_alert=True)
             return
             
        if group_row['owner_id'] != user_id:
             conn.close()
             await callback_query.answer("Access denied.", show_alert=True)
             return
             
        link_data = dict(group_row)
        export_name = link_data.get('group_name', doc_id)
        owner_code = link_data.get('owner_code')
                
        conn.close() # Tutup untuk operasi async

        await callback_query.message.edit_text("⏳ Analyzing channels & activity logs... (This may take a moment)")
        
        # 2. Resolusi Chat ID (Saluran Target & Grup Diskusi Tertaut)
        target_chat_ids = set()
        
        # Helper untuk ekstrak username
        def get_username_from_url(url):
            if not url: return None
            url = url.strip()
            if "t.me/" in url:
                return url.split("t.me/")[1].split("/")[0]
            elif url.startswith("@"):
                return url[1:]
            elif not url.startswith("http"):
                return url
            return None

        # Kumpulkan username dari item grup
        usernames_to_resolve = []
        items = get_link_items(doc_id)
        for item in items:
            u = get_username_from_url(item['target_url'])
            if u: usernames_to_resolve.append(u)
            
        # Resolusi melalui Telegram API
        for uname in usernames_to_resolve:
            try:
                chat = await client.get_chat(uname)
                target_chat_ids.add(chat.id)
                if chat.linked_chat:
                    target_chat_ids.add(chat.linked_chat.id)
            except Exception:
                # Abaikan username tidak valid atau chat tidak dapat diakses
                pass
                
        # 3. Kueri Data Aktivitas
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if not target_chat_ids:
            # Fallback jika tidak ada id yang berhasil di-resolve
            cursor.execute('''
                SELECT user_id, username, chat_id, chat_title, chat_username, 
                       owner_code, message_text, message_id, timestamp, link_id, post_id
                FROM user_activity 
                WHERE link_id = ?
                ORDER BY timestamp DESC
            ''', (doc_id,))
        else:
            # Query Dinamis menggunakan IN clause
            ids_list = list(target_chat_ids)
            placeholders = ','.join('?' for _ in ids_list)
            
            # Logika Kueri:
            # - Cocokkan link_id
            # - ATAU Cocokkan owner_code DAN chat_id ada di target (aktivitas terdeteksi di grup relevan)
            sql = f'''
                SELECT user_id, username, chat_id, chat_title, chat_username, 
                       owner_code, message_text, message_id, timestamp, link_id, post_id
                FROM user_activity 
                WHERE link_id = ? 
                   OR (owner_code = ? AND chat_id IN ({placeholders}))
                ORDER BY timestamp DESC
            '''
            params = [doc_id, owner_code] + ids_list
            cursor.execute(sql, params)

        
        activities = cursor.fetchall()
        conn.close()
        
        if len(activities) == 0:
            await callback_query.message.edit_text("No activity recorded yet.")
            return
        
        # 4. Buat CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['User ID', 'Username', 'Chat ID', 'Chat Title', 'ID Post', 'Timestamp', 'Message'])
        
        for activity in activities:
            # Potong pesan untuk pratinjau
            msg_text = activity['message_text'][:200] if activity['message_text'] else ""
            
            writer.writerow([
                activity['user_id'],
                activity['username'],
                activity['chat_id'],
                activity['chat_title'],
                activity['post_id'] or "",
                activity['timestamp'],
                msg_text
            ])
            
        output.seek(0)
        
        # Kirim File
        safe_name = "".join(x for x in export_name if x.isalnum() or x in ('_','-'))
        filename = f"activity_{safe_name}.csv"
        
        bio = io.BytesIO(output.getvalue().encode('utf-8'))
        bio.name = filename
        
        await client.send_document(
            chat_id=callback_query.message.chat.id,
            document=bio,
            file_name=filename,
            caption=f"📊 **Activity Log for:** `{export_name}`\n"
                    f"Found {len(activities)} activities across",
            reply_to_message_id=callback_query.message.reply_to_message.id if callback_query.message.reply_to_message else None
        )
        
        await callback_query.message.delete()

    except Exception as e:
        print(f"Error in activity_callback: {e}")
        try:
            await callback_query.message.edit_text(f"❌ Error: {str(e)[:100]}")
        except:
            pass
        await callback_query.message.edit_text("An error occurred generating the file.")

@app.on_message(filters.command("deletegroup"))
async def deletegroup_handler(client: Client, message: Message):
    """Delete a link group or legacy link."""
    track_user(message.from_user)
    user_id = message.from_user.id
    
    # Ambil link groups
    groups = get_user_link_groups(user_id)
    
    # Ambil legacy links
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM links WHERE owner_id = ?', (user_id,))
    legacy_links = {row['link_id']: dict(row) for row in cursor.fetchall()}
    conn.close()
    
    if not groups and not legacy_links:
        await message.reply_text("You don't have any links to delete.")
        return

    buttons = []
    
    # Tombol untuk link groups
    for g in groups:
        btn_text = f"📂 {g['group_name']} ({g['item_count']} links)"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"delgrpsel_{g['group_id']}")])
    
    # Tombol untuk legacy links
    for doc_id, data in legacy_links.items():
        btn_text = f"🔗 @{data.get('username_target')} ({data.get('clicks')} clicks)"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"delsel_{doc_id}")])

    await message.reply_text(
        "🗑 **Select a link to delete:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex(r"^delgrpsel_"))
async def delete_group_select_callback(client: Client, callback_query):
    """Handle link group selection for deletion - show confirmation."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    group_data = get_link_group(group_id)
    
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Group not found or access denied.", show_alert=True)
        return
    
    items = get_link_items(group_id)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"delgrpconf_{group_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"delgrpcanc_{group_id}")]
    ])
    
    await callback_query.message.edit_text(
        f"⚠️ **Confirm Deletion**\n\n"
        f"Are you sure you want to delete this link group?\n\n"
        f"📂 **Name:** {group_data['group_name']}\n"
        f"🔗 **Links:** {len(items)}\n"
        f"📊 **Clicks:** {group_data['clicks']}\n\n"
        f"This will delete the group and all its links.",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex(r"^delgrpconf_"))
async def delete_group_confirm_callback(client: Client, callback_query):
    """Handle group deletion confirmation."""
    group_id = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    group_data = get_link_group(group_id)
    
    if not group_data or group_data['owner_id'] != user_id:
        await callback_query.answer("Access denied.", show_alert=True)
        return
    
    group_name = group_data['group_name']
    
    # Hapus grup dan semua items
    delete_link_group(group_id)
    
    # Hapus juga click_stats yang terkait
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM click_stats WHERE link_id = ?', (group_id,))
    conn.commit()
    conn.close()
    
    await callback_query.message.edit_text(
        f"✅ **Link Group Deleted**\n\n"
        f"📂 **{group_name}** has been removed.\n"
        f"All links and click stats have been deleted."
    )

@app.on_callback_query(filters.regex(r"^delgrpcanc_"))
async def delete_group_cancel_callback(client: Client, callback_query):
    """Handle group deletion cancellation."""
    await callback_query.message.edit_text("❌ Deletion cancelled.")

@app.on_callback_query(filters.regex(r"^delsel_"))
async def delete_select_callback(client: Client, callback_query):
    """Handle link selection for deletion - show confirmation."""
    doc_id = callback_query.data.split("_", 1)[1]
    
    # Verify ownership
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM links WHERE link_id = ?', (doc_id,))
    link_row = cursor.fetchone()
    conn.close()
    
    if not link_row or link_row['owner_id'] != callback_query.from_user.id:
        await callback_query.answer("Link not found or access denied.", show_alert=True)
        return
    
    link_data = dict(link_row)
    
    # Show confirmation
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"delconf_{doc_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"delcanc_{doc_id}")]
    ])
    
    await callback_query.message.edit_text(
        f"⚠️ **Confirm Deletion**\\n\\n"
        f"Are you sure you want to delete this link?\\n\\n"
        f"🎯 **Target:** @{link_data.get('username_target')}\\n"
        f"📊 **Clicks:** {link_data.get('clicks')}\\n\\n"
        f"This will also delete all click stats and activity logs for this link.",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex(r"^delconf_"))
async def delete_confirm_callback(client: Client, callback_query):
    """Handle deletion confirmation."""
    doc_id = callback_query.data.split("_", 1)[1]
    
    # Verify ownership one more time
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM links WHERE link_id = ?', (doc_id,))
    link_row = cursor.fetchone()
    
    if not link_row or link_row['owner_id'] != callback_query.from_user.id:
        conn.close()
        await callback_query.answer("Link not found or access denied.", show_alert=True)
        return
    
    link_data = dict(link_row)
    
    # Delete cascading: user_activity -> click_stats -> links
    cursor.execute('DELETE FROM user_activity WHERE link_id = ?', (doc_id,))
    cursor.execute('DELETE FROM click_stats WHERE link_id = ?', (doc_id,))
    cursor.execute('DELETE FROM links WHERE link_id = ?', (doc_id,))
    
    conn.commit()
    conn.close()
    
    await callback_query.message.edit_text(
        f"✅ **Link Deleted Successfully**\\n\\n"
        f"🎯 @{link_data.get('username_target')} has been removed.\\n"
        f"All associated data (clicks and activity logs) have also been deleted."
    )

@app.on_callback_query(filters.regex(r"^delcanc_"))
async def delete_cancel_callback(client: Client, callback_query):
    """Handle deletion cancellation."""
    await callback_query.message.edit_text("❌ Deletion cancelled.")

# --- Activity Monitoring Handlers ---
@app.on_message(filters.group & ~filters.bot & ~filters.service)
async def monitor_group_activity(client: Client, message: Message):
    """Monitor user activity in groups."""
    try:
        track_user(message.from_user)
        # Skip if no user
        if not message.from_user:
            return
        
        # Save group info (passive tracking)
        save_group_to_db(message.chat)
        
        # Save member info (passive tracking)
        save_member_to_db(message.chat.id, message.from_user)
        
        # Skip if no text (for activity logging)
        if not (message.text or message.caption):
            return
        
        user_id = message.from_user.id
        chat_id = message.chat.id
        chat_username = message.chat.username

        if not (chat_username or chat_id):
            # Cannot track without username since we rely on username_target
            return

        # Get tracked links for this user in this chat
        tracked_links = await get_user_tracked_links(user_id, chat_username, chat_id)
        if not tracked_links:
            return

        # Log activity for the first tracked link (or all if needed)
        for link in tracked_links:
            # Cek apakah ini komentar dia sendiri (reply to message)
            # Biasanya di discussion group, message adalah reply ke channel post.
            post_id = None
            if message.reply_to_message and message.reply_to_message.forward_from_message_id:
                post_id = message.reply_to_message.forward_from_message_id
            
            log_user_activity(
                user_id=user_id,
                username=message.from_user.username or "",
                chat_id=chat_id,
                chat_title=message.chat.title or "",
                chat_username=chat_username,
                owner_code=link['owner_code'],
                link_id=link['link_id'],
                message_text=message.text or message.caption,
                message_id=message.id,
                post_id=post_id
            )

    except Exception as e:
        print(f"Error monitoring group activity: {e}")


if __name__ == "__main__":
    print("Starting Link Tracker Bot...")
    app.run()

