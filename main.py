#!/usr/bin/env python3
"""
Telegram Game Bot with Coin System
A comprehensive bot for playing word chain games with virtual coins, challenges, and leaderboards
"""

import logging
import os
import psycopg2
import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from apscheduler.jobstores.base import JobLookupError
from threading import Lock

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_COINS = 100
DEFAULT_GAME_COST = 10
BOT_TOKEN = os.getenv('BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# Game States
class GameState(Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"

class ChallengeState(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"

@dataclass
class GamePlayer:
    user_id: int
    username: str
    coins: int
    is_alive: bool = True

@dataclass
class WordChainGame:
    chat_id: int
    game_id: str
    state: GameState
    players: List[GamePlayer]
    current_player_index: int
    words_used: List[str]
    current_word: str
    last_letter: str
    stake: int
    creator_id: int
    time_limit: int = 60
    last_word_time: datetime = None
    lobby_message_id: Optional[int] = None

@dataclass
class Challenge:
    challenge_id: str
    challenger_id: int
    challenged_id: int
    chat_id: int
    game_type: str
    stake: int
    state: ChallengeState
    created_at: datetime
    expires_at: datetime

class DatabaseManager:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.placeholder = '%s'
        self.init_database()
    
    def get_connection(self):
        return psycopg2.connect(self.db_url)
    
    def init_database(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        coins INTEGER DEFAULT 100,
                        games_played INTEGER DEFAULT 0,
                        games_won INTEGER DEFAULT 0,
                        total_coins_won INTEGER DEFAULT 0,
                        total_coins_lost INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS games (
                        game_id TEXT PRIMARY KEY,
                        chat_id BIGINT,
                        game_type TEXT,
                        state TEXT,
                        stake INTEGER,
                        creator_id BIGINT,
                        winner_id BIGINT,
                        players TEXT,
                        game_data TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        finished_at TIMESTAMP
                    )
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS challenges (
                        challenge_id TEXT PRIMARY KEY,
                        challenger_id BIGINT,
                        challenged_id BIGINT,
                        chat_id BIGINT,
                        game_type TEXT,
                        stake INTEGER,
                        state TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP
                    )
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        transaction_id SERIAL PRIMARY KEY,
                        from_user_id BIGINT,
                        to_user_id BIGINT,
                        amount INTEGER,
                        transaction_type TEXT,
                        reference_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                conn.commit()
                logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            raise
    
    def get_user(self, user_id: int) -> Optional[dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT user_id, username, first_name, coins, games_played, 
                           games_won, total_coins_won, total_coins_lost
                    FROM users WHERE user_id = %s
                """, (user_id,))
                row = cursor.fetchone()
                if row:
                    return {
                        'user_id': row[0], 'username': row[1], 'first_name': row[2],
                        'coins': row[3], 'games_played': row[4], 'games_won': row[5],
                        'total_coins_won': row[6], 'total_coins_lost': row[7]
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None
    
    def get_user_by_username(self, username: str) -> Optional[dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT user_id, username, first_name, coins, games_played, 
                           games_won, total_coins_won, total_coins_lost
                    FROM users WHERE username = %s
                """, (username,))
                row = cursor.fetchone()
                if row:
                    return {
                        'user_id': row[0], 'username': row[1], 'first_name': row[2],
                        'coins': row[3], 'games_played': row[4], 'games_won': row[5],
                        'total_coins_won': row[6], 'total_coins_lost': row[7]
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting user by username {username}: {e}")
            return None
    
    def create_or_update_user(self, user: User) -> bool:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, coins, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user.id, user.username, user.first_name, DEFAULT_COINS))
                cursor.execute("""
                    UPDATE users SET username = %s, first_name = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                """, (user.username, user.first_name, user.id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error creating/updating user {user.id}: {e}")
            return False
    
    def update_user_coins(self, user_id: int, amount: int) -> bool:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE users SET coins = coins + %s, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                """, (amount, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating coins for user {user_id}: {e}")
            return False
    
    def transfer_coins(self, from_user_id: int, to_user_id: int, amount: int) -> bool:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT coins FROM users WHERE user_id = %s", (from_user_id,))
                sender = cursor.fetchone()
                cursor.execute("SELECT coins FROM users WHERE user_id = %s", (to_user_id,))
                recipient = cursor.fetchone()
                
                if not sender or not recipient or sender[0] < amount:
                    return False
                
                conn.autocommit = False
                try:
                    cursor.execute("UPDATE users SET coins = coins - %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s", (amount, from_user_id))
                    cursor.execute("UPDATE users SET coins = coins + %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s", (amount, to_user_id))
                    cursor.execute("""
                        INSERT INTO transactions (from_user_id, to_user_id, amount, transaction_type)
                        VALUES (%s, %s, %s, 'transfer')
                    """, (from_user_id, to_user_id, amount))
                    conn.commit()
                    return True
                except Exception as e:
                    conn.rollback()
                    raise e
        except Exception as e:
            logger.error(f"Error transferring coins: {e}")
            return False
    
    def get_leaderboard(self, limit: int = 10) -> List[dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT user_id, username, first_name, coins, games_won, games_played
                    FROM users
                    ORDER BY coins DESC, games_won DESC
                    LIMIT %s
                """, (limit,))
                return [
                    {
                        'user_id': row[0], 'username': row[1], 'first_name': row[2],
                        'coins': row[3], 'games_won': row[4], 'games_played': row[5]
                    }
                    for row in cursor.fetchall()
                ]
        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return []

class GameBot:
    def __init__(self):
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise ValueError("DATABASE_URL environment variable not set")
        self.db = DatabaseManager(db_url)
        self.active_games: Dict[int, WordChainGame] = {}
        self.pending_challenges: Dict[str, Challenge] = {}
        self.pending_stake_settings: Dict[int, int] = {}
        self.word_list = self.load_word_list()
        self.game_jobs: Dict[int, List] = {}
        self.application = Application.builder().token(BOT_TOKEN)..Dotenv
        self.game_lock = Lock()
        self.challenge_lock = Lock()
        self.setup_handlers()
    
    async def start_bot(self):
        await self.application.initialize()
    
    def load_word_list(self, file_path: str = 'words.txt') -> set:
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                words = {line.strip().lower() for line in file if line.strip()}
            if not words:
                logger.warning("Word list is empty! Using default minimal set.")
                return {'apple', 'elephant', 'tiger'}
            return words
        except FileNotFoundError:
            logger.error(f"Word list file not found: {file_path}. Using default minimal set.")
            return {'apple', 'elephant', 'tiger'}
        
    def is_valid_word(self, word: str) -> bool:
        return word.lower() in self.word_list
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("balance", self.balance_command))
        self.application.add_handler(CommandHandler("pay", self.pay_command))
        self.application.add_handler(CommandHandler("wordchain", self.wordchain_command))
        self.application.add_handler(CommandHandler("join", self.join_command))
        self.application.add_handler(CommandHandler("challenge", self.challenge_command))
        self.application.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_word_chain_message))
        self.application.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.create_or_update_user(user)
        welcome_text = f"""
🎮 Welcome to the Game Bot, {user.first_name}! 🎮

🪙 You start with {DEFAULT_COINS} coins!

📜 **Available Commands:**
• /balance - Check your coin balance
• /pay @username amount - Transfer coins
• /challenge @username - Challenge a player
• /wordchain - Start a word chain game
• /join - Join an active game
• /leaderboard - View top players
• /help - Show this help

💰 **How it works:**
- Default games cost {DEFAULT_GAME_COST} coins
- Winners receive coins from losers
- Challenges allow custom stakes
- Enjoy and play responsibly!
        """
        await update.message.reply_text(welcome_text)
    
    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_data = self.db.get_user(user.id)
        if not user_data:
            self.db.create_or_update_user(user)
            user_data = {'coins': DEFAULT_COINS, 'games_played': 0, 'games_won': 0}
        
        win_rate = (user_data['games_won'] / user_data['games_played'] * 100) if user_data['games_played'] > 0 else 0
        balance_text = f"""
💰 **{user.first_name}'s Balance**

🪙 Coins: {user_data['coins']}
🎮 Games Played: {user_data['games_played']}
🏆 Games Won: {user_data['games_won']}
📊 Win Rate: {win_rate:.1f}%
🔢 User ID: {user.id}
        """
        await update.message.reply_text(balance_text)
    
    async def pay_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /pay @username amount")
            return
        
        try:
            recipient_username = context.args[0].replace('@', '')
            amount = int(context.args[1])
            if amount <= 0:
                await update.message.reply_text("Amount must be positive!")
                return
            
            sender = update.effective_user
            sender_data = self.db.get_user(sender.id)
            recipient_data = self.db.get_user_by_username(recipient_username)
            
            if not sender_data:
                await update.message.reply_text("❌ Register with /start first!")
                return
            if not recipient_data:
                await update.message.reply_text(f"❌ User @{recipient_username} not found!")
                return
            if sender_data['coins'] < amount:
                await update.message.reply_text("❌ Insufficient coins!")
                return
            
            if self.db.transfer_coins(sender.id, recipient_data['user_id'], amount):
                await update.message.reply_text(f"✅ Transferred {amount} coins to @{recipient_username}!")
            else:
                await update.message.reply_text("❌ Transfer failed!")
        except ValueError:
            await update.message.reply_text("Invalid amount! Use a number.")
        except Exception as e:
            logger.error(f"Error in pay command: {e}")
            await update.message.reply_text("❌ Error processing payment.")
    
    async def wordchain_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        
        with self.game_lock:
            if chat_id in self.active_games:
                await update.message.reply_text("🎮 A game is already active!")
                return
        
        self.db.create_or_update_user(user)
        user_data = self.db.get_user(user.id)
        if user_data['coins'] < DEFAULT_GAME_COST:
            await update.message.reply_text(f"❌ Need {DEFAULT_GAME_COST} coins to start!")
            return
        
        keyboard = [
            [InlineKeyboardButton("🎯 Default Mode (10 coins)", callback_data="wordchain_default")],
            [InlineKeyboardButton("⚙️ Custom Mode", callback_data="wordchain_custom")],
            [InlineKeyboardButton("📋 Game Rules", callback_data="wordchain_rules")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        game_info = f"""
🎮 **Word Chain Game**

👤 **Creator:** {user.first_name}
💰 **Default Mode:** {DEFAULT_GAME_COST} coins
🏆 **Winner takes all**

📝 **How to play:**
• Words start with last letter of previous word
• No repeats
• 60s per turn
• Last standing wins!

Choose mode:
        """
        await update.message.reply_text(game_info, reply_markup=reply_markup)
    
    async def join_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        
        with self.game_lock:
            if chat_id not in self.active_games:
                await update.message.reply_text("❌ No active game!")
                return
        
            game = self.active_games[chat_id]
            if game.state != GameState.WAITING:
                await update.message.reply_text("❌ Game already started!")
                return
            if any(p.user_id == user.id for p in game.players):
                await update.message.reply_text("You're already in!")
                return
        
        self.db.create_or_update_user(user)
        user_data = self.db.get_user(user.id)
        if user_data['coins'] < game.stake:
            await update.message.reply_text(f"❌ Need {game.stake} coins!")
            return
        
        game.players.append(GamePlayer(user.id, user.username or user.first_name, game.stake))
        mention = f"[{user.first_name}](tg://user?id={user.id})"
        await update.message.reply_text(f"{mention} joined. Now {len(game.players)} players.", parse_mode='Markdown')
        
        if game.lobby_message_id:
            try:
                players_text = "\n".join([f"• {p.username}" for p in game.players])
                creator_name = next(p.username for p in game.players if p.user_id == game.creator_id)
                game_text = f"""
🎮 **Word Chain Game Lobby**

👤 **Creator:** {creator_name}
💰 **Entry Fee:** {game.stake} coins
👥 **Players:** {len(game.players)}

**Current Players:**
{players_text}

⏰ **Waiting...**

Use /join!
                """
                keyboard = [[InlineKeyboardButton("🎮 Join Game", callback_data="join_game")]]
                if len(game.players) >= 2:
                    keyboard.append([InlineKeyboardButton("▶️ Start Game", callback_data="start_wordchain")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=game.lobby_message_id,
                    text=game_text, reply_markup=reply_markup, parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error updating lobby: {e}")
    
    async def challenge_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 1:
            await update.message.reply_text("Usage: /challenge @username [amount]")
            return
        
        challenger = update.effective_user
        chat_id = update.effective_chat.id
        challenged_username = context.args[0].replace('@', '')
        challenged_data = self.db.get_user_by_username(challenged_username)
        
        if not challenged_data:
            await update.message.reply_text(f"❌ User @{challenged_username} not found!")
            return
        
        stake = DEFAULT_GAME_COST
        if len(context.args) > 1:
            try:
                stake = int(context.args[1])
                if stake <= 0:
                    await update.message.reply_text("Stake must be positive!")
                    return
            except ValueError:
                await update.message.reply_text("Invalid amount! Use a number.")
                return
        
        challenger_data = self.db.get_user(challenger.id)
        if challenger_data['coins'] < stake:
            await update.message.reply_text(f"❌ Need {stake} coins!")
            return
        
        challenge_id = f"challenge_{chat_id}_{challenger.id}_{challenged_data['user_id']}_{int(datetime.now(timezone.utc).timestamp())}"
        challenge = Challenge(
            challenge_id=challenge_id, challenger_id=challenger.id, challenged_id=challenged_data['user_id'],
            chat_id=chat_id, game_type="wordchain", stake=stake, state=ChallengeState.PENDING,
            created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        with self.challenge_lock:
            self.pending_challenges[challenge_id] = challenge
        
        challenge_text = f"""
⚔️ **Challenge Issued!**

👤 **Challenger:** {challenger.first_name}
🎯 **Challenged:** @{challenged_username}
💰 **Stake:** {stake} coins
🎮 **Game:** Word Chain

@{challenged_username}, accept?
        """
        keyboard = [
            [InlineKeyboardButton("✅ Accept", callback_data=f"accept_challenge_{challenge_id}")],
            [InlineKeyboardButton("❌ Decline", callback_data=f"decline_challenge_{challenge_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(challenge_text, reply_markup=reply_markup)
    
    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        leaderboard = self.db.get_leaderboard(10)
        if not leaderboard:
            await update.message.reply_text("🏆 No players yet!")
            return
        
        leaderboard_text = "🏆 **TOP PLAYERS** 🏆\n\n"
        for i, player in enumerate(leaderboard, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            username = player['username'] or player['first_name']
            leaderboard_text += f"{medal} {username}\n   💰 {player['coins']} coins | 🏆 {player['games_won']} wins\n\n"
        await update.message.reply_text(leaderboard_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
🎮 **Game Bot Help**

📜 **Commands:**
• /start - Begin using bot
• /balance - Check coins
• /pay @username amount - Send coins
• /challenge @username [amount] - Challenge player
• /wordchain - Start game
• /join - Join game
• /leaderboard - Top players
• /help - This help

🎯 **Word Chain Rules:**
• Start with last letter
• No repeats
• 60s/turn
• Winner takes all

💰 **Coins:**
• Start with 100
• Default game: 10 coins
• Winners get losers' coins

⚔️ **Challenges:**
• Custom stakes
• Winner takes all

🏆 **Ranking:**
• Earn coins for leaderboard
• Track wins

Questions? Ask in chat!
        """
        await update.message.reply_text(help_text)
    
    def schedule_joining_jobs(self, game: WordChainGame, context: ContextTypes.DEFAULT_TYPE):
        chat_id = game.chat_id
        jobs = []
        jobs.append(context.job_queue.run_once(
            self.send_join_reminder, 30, data={'chat_id': chat_id, 'time_left': 30},
            name=f"join_reminder_30_{chat_id}"
        ))
        jobs.append(context.job_queue.run_once(
            self.send_join_reminder, 45, data={'chat_id': chat_id, 'time_left': 15},
            name=f"join_reminder_15_{chat_id}"
        ))
        jobs.append(context.job_queue.run_once(
            self.auto_start_game, 60, data={'game': game, 'chat_id': chat_id},
            name=f"auto_start_{chat_id}"
        ))
        self.game_jobs[chat_id] = jobs
    
    async def send_join_reminder(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        chat_id = job.data['chat_id']
        time_left = job.data['time_left']
        with self.game_lock:
            if chat_id in self.active_games and self.active_games[chat_id].state == GameState.WAITING:
                await context.bot.send_message(chat_id, f"{time_left}s left to /join.")
    
    async def auto_start_game(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        chat_id = job.data['chat_id']
        game = job.data['game']
        
        with self.game_lock:
            if chat_id in self.active_games and game == self.active_games[chat_id] and game.state == GameState.WAITING:
                if len(game.players) >= 2:
                    await context.bot.send_message(chat_id, "Game starting...")
                    turn_order = "\n".join([p.username for p in game.players])
                    await context.bot.send_message(chat_id, f"Turn order:\n{turn_order}")
                    for player in game.players:
                        self.db.update_user_coins(player.user_id, -game.stake)
                    game.state = GameState.ACTIVE
                    game.current_player_index = 0
                    game.last_word_time = datetime.now(timezone.utc)
                    random.shuffle(game.players)
                    await self.next_turn(None, game, context)
                else:
                    await context.bot.send_message(chat_id, "❌ Not enough players. Cancelled.")
                    del self.active_games[chat_id]
                self.cancel_game_jobs(chat_id, context)
    
    def cancel_game_jobs(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        if chat_id in self.game_jobs:
            for job in self.game_jobs[chat_id]:
                try:
                    job.schedule_removal()
                except JobLookupError:
                    logger.debug(f"Job {job.name} already removed")
            del self.game_jobs[chat_id]
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user = query.from_user
        chat_id = query.message.chat_id
        
        if data == "wordchain_default":
            await self.start_wordchain_game(query, chat_id, user, DEFAULT_GAME_COST)
            with self.game_lock:
                if chat_id in self.active_games:
                    self.schedule_joining_jobs(self.active_games[chat_id], context)
        elif data == "wordchain_custom":
            await self.show_custom_game_options(query)
        elif data == "wordchain_rules":
            await self.show_game_rules(query)
        elif data.startswith("accept_challenge_"):
            await self.accept_challenge(query, user, data.split("_", 2)[2], context)
        elif data.startswith("decline_challenge_"):
            await self.decline_challenge(query, user, data.split("_", 2)[2])
        elif data == "join_game":
            await self.join_game(query, user, chat_id)
        elif data == "start_wordchain":
            await self.handle_start_wordchain(query, user, chat_id, context)
        elif data == "cancel_game":
            await self.cancel_game(query, user, chat_id, context)
        elif data == "cancel_stake_setting":
            with self.game_lock:
                if chat_id in self.pending_stake_settings:
                    del self.pending_stake_settings[chat_id]
            await self.show_main_wordchain_menu(query, user)
        elif data == "back_to_main":
            await self.show_main_wordchain_menu(query, user)
        else:
            await query.answer("Unknown command!")
    
    async def show_main_wordchain_menu(self, query, user: User):
        keyboard = [
            [InlineKeyboardButton("🎯 Default Mode (10 coins)", callback_data="wordchain_default")],
            [InlineKeyboardButton("⚙️ Custom Mode", callback_data="wordchain_custom")],
            [InlineKeyboardButton("📋 Game Rules", callback_data="wordchain_rules")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        game_info = f"""
🎮 **Word Chain Game**

👤 **Creator:** {user.first_name}
💰 **Default Mode:** {DEFAULT_GAME_COST} coins
🏆 **Winner takes all**

📝 **How to play:**
• Words start with last letter
• No repeats
• 60s per turn
• Last standing wins!

Choose mode:
        """
        await query.edit_message_text(game_info, reply_markup=reply_markup)
    
    async def cancel_game(self, query, user: User, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        with self.game_lock:
            if chat_id not in self.active_games:
                await query.answer("No active game!")
                return
            game = self.active_games[chat_id]
            if user.id != game.creator_id:
                await query.answer("Only creator can cancel!")
                return
            del self.active_games[chat_id]
            self.cancel_game_jobs(chat_id, context)
        await query.edit_message_text("❌ Game cancelled by creator!")
    
    async def handle_start_wordchain(self, query, user: User, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        with self.game_lock:
            if chat_id not in self.active_games:
                await query.answer("No active game!")
                return
            game = self.active_games[chat_id]
            if user.id != game.creator_id:
                await query.answer("Only creator can start!")
                return
            if len(game.players) < 2:
                await query.answer("Need 2+ players!")
                return
            
            for player in game.players:
                player_data = self.db.get_user(player.user_id)
                if player_data['coins'] < game.stake:
                    await query.answer(f"{player.username} lacks coins!")
                    return
                self.db.update_user_coins(player.user_id, -game.stake)
            
            game.state = GameState.ACTIVE
            game.current_player_index = 0
            game.last_word_time = datetime.now(timezone.utc)
            random.shuffle(game.players)
        
        await query.message.chat.send_message("Game starting...")
        turn_order = "\n".join([p.username for p in game.players])
        await query.message.chat.send_message(f"Turn order:\n{turn_order}")
        self.cancel_game_jobs(chat_id, context)
        await self.next_turn(None, game, context)
    
    async def start_wordchain_game(self, query, chat_id: int, creator: User, stake: int):
        self.db.create_or_update_user(creator)
        user_data = self.db.get_user(creator.id)
        if user_data['coins'] < stake:
            await query.edit_message_text(f"❌ Need {stake} coins!")
            return
        with self.game_lock:
            if chat_id in self.active_games:
                await query.edit_message_text("🎮 Game already active!")
                return
            
            game_id = f"wc_{chat_id}_{int(datetime.now(timezone.utc).timestamp())}"
            game = WordChainGame(
                chat_id=chat_id, game_id=game_id, state=GameState.WAITING,
                players=[GamePlayer(creator.id, creator.username or creator.first_name, stake)],
                current_player_index=0, words_used=[], current_word="", last_letter="",
                stake=stake, creator_id=creator.id
            )
            self.active_games[chat_id] = game
        
        keyboard = [
            [InlineKeyboardButton("🎮 Join Game", callback_data="join_game")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        game_text = f"""
🎮 **Word Chain Game Lobby**

👤 **Creator:** {creator.first_name}
💰 **Entry Fee:** {stake} coins
👥 **Players:** {len(game.players)}

**Current Players:**
• {creator.first_name} (Creator)

⏰ **Waiting...**
**Need 2+ players!**

Use /join!
        """
        await query.edit_message_text(game_text, reply_markup=reply_markup)
        game.lobby_message_id = query.message.message_id
    
    async def show_custom_game_options(self, query):
        chat_id = query.message.chat_id
        user = query.from_user
        with self.game_lock:
            self.pending_stake_settings[chat_id] = user.id
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_stake_setting")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            "⚙️ **Custom Game Mode**\n\nEnter stake amount (positive integer, e.g., 14, 55):",
            reply_markup=reply_markup
        )
        await query.answer()
    
    async def start_wordchain_game_from_message(self, update, chat_id, creator, stake):
        game_id = f"wc_{chat_id}_{int(datetime.now(timezone.utc).timestamp())}"
        game = WordChainGame(
            chat_id=chat_id, game_id=game_id, state=GameState.WAITING,
            players=[GamePlayer(creator.id, creator.username or creator.first_name, stake)],
            current_player_index=0, words_used=[], current_word="", last_letter="",
            stake=stake, creator_id=creator.id
        )
        with self.game_lock:
            self.active_games[chat_id] = game
        players_text = "\n".join([f"• {p.username}" for p in game.players])
        game_text = f"""
🎮 **Word Chain Game Lobby**

👤 **Creator:** {creator.first_name}
💰 **Entry Fee:** {game.stake} coins
👥 **Players:** {len(game.players)}

**Current Players:**
{players_text}

⏰ **Waiting...**
**Need 2+ players!**

Use /join!
        """
        keyboard = [
            [InlineKeyboardButton("🎮 Join Game", callback_data="join_game")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        lobby_message = await update.message.reply_text(game_text, reply_markup=reply_markup)
        game.lobby_message_id = lobby_message.message_id
    
    async def show_game_rules(self, query):
        rules_text = """
📋 **Word Chain Game Rules**

🎯 **Objective:**
Last player standing wins!

🎮 **How to Play:**
1. Take turns saying words
2. Start with last letter of previous word
3. No repeats
4. 60s/turn
5. Invalid words = out
6. Timeout = out

💰 **Coins:**
• Pay entry fee
• Winner takes all
• Split if multiple remain

✅ **Valid Words:**
• Real English words
• 3+ letters
• No proper nouns/abbreviations

❌ **Invalid:**
• Repeats
• Invalid words
• Over 60s
• Wrong letter

🏆 **Winning:**
• Last standing
• Collect coins
• Gain points

Good luck! 🍀
        """
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(rules_text, reply_markup=reply_markup)
    
    async def join_game(self, query, user: User, chat_id: int):
        with self.game_lock:
            if chat_id not in self.active_games:
                await query.answer("❌ No active game!")
                return
            game = self.active_games[chat_id]
            if game.state != GameState.WAITING:
                await query.answer("❌ Game started!")
                return
            if any(p.user_id == user.id for p in game.players):
                await query.answer("Already joined!")
                return
        
        self.db.create_or_update_user(user)
        user_data = self.db.get_user(user.id)
        if user_data['coins'] < game.stake:
            await query.answer(f"❌ Need {game.stake} coins!")
            return
        
        game.players.append(GamePlayer(user.id, user.username or user.first_name, game.stake))
        mention = f"[{user.first_name}](tg://user?id={user.id})"
        await query.message.chat.send_message(f"{mention} joined. Now {len(game.players)} players.", parse_mode='Markdown')
        
        players_text = "\n".join([f"• {p.username}" for p in game.players])
        creator_name = next(p.username for p in game.players if p.user_id == game.creator_id)
        game_text = f"""
🎮 **Word Chain Game Lobby**

👤 **Creator:** {creator_name}
💰 **Entry Fee:** {game.stake} coins
👥 **Players:** {len(game.players)}

**Current Players:**
{players_text}

⏰ **Waiting...**

Use /join!
        """
        keyboard = [[InlineKeyboardButton("🎮 Join Game", callback_data="join_game")]]
        if len(game.players) >= 2:
            keyboard.append([InlineKeyboardButton("▶️ Start Game", callback_data="start_wordchain")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(game_text, reply_markup=reply_markup)
        await query.answer(f"✅ {user.first_name} joined!")
    
    async def handle_word_chain_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        message = update.message.text.strip()
        
        with self.game_lock:
            if chat_id in self.pending_stake_settings and self.pending_stake_settings[chat_id] == user.id:
                try:
                    stake = int(message)
                    if stake <= 0 or stake > 1000:
                        await update.message.reply_text("❌ Stake must be 1-1000 coins!")
                        return
                    user_data = self.db.get_user(user.id)
                    if user_data['coins'] < stake:
                        await update.message.reply_text(f"❌ Need {stake} coins!")
                        del self.pending_stake_settings[chat_id]
                        return
                    await self.start_wordchain_game_from_message(update, chat_id, user, stake)
                    if chat_id in self.active_games:
                        self.schedule_joining_jobs(self.active_games[chat_id], context)
                    del self.pending_stake_settings[chat_id]
                except ValueError:
                    await update.message.reply_text("❌ Enter a number (e.g., 14, 55)!")
                return
        
            if chat_id not in self.active_games or self.active_games[chat_id].state != GameState.ACTIVE:
                return
        
            game = self.active_games[chat_id]
            current_player = game.players[game.current_player_index]
            if current_player.user_id != user.id or not current_player.is_alive:
                return
        
        for job_name in [f"turn_reminder_{game.chat_id}_{current_player.user_id}", f"turn_timeout_{game.chat_id}_{current_player.user_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                try:
                    job.schedule_removal()
                except JobLookupError:
                    logger.debug(f"Job {job.name} already removed")
        
        if len(message) < 3:
            await self.eliminate_player(context, game, current_player, "Word < 3 letters!", update)
            return
        if ' ' in message:
            await self.eliminate_player(context, game, current_player, "Single words only!", update)
            return
        if not self.is_valid_word(message):
            await self.eliminate_player(context, game, current_player, "Invalid word!", update)
            return
        if message in game.words_used:
            await self.eliminate_player(context, game, current_player, "Word used!", update)
            return
        if game.last_letter and not message.startswith(game.last_letter):
            await self.eliminate_player(context, game, current_player, f"Must start with '{game.last_letter.upper()}'!", update)
            return
        
        game.words_used.append(message)
        game.current_word = message
        game.last_letter = message[-1].lower()
        game.last_word_time = datetime.now(timezone.utc)
        game.current_player_index = (game.current_player_index + 1) % len(game.players)
        
        await update.message.reply_text(f"✅ {message} accepted! Next player's turn.")
        await self.next_turn(update, game, context)
    
    async def next_turn(self, update: Update, game: WordChainGame, context: ContextTypes.DEFAULT_TYPE):
        with self.game_lock:
            if game.state != GameState.ACTIVE:
                return
            
            alive_players = [p for p in game.players if p.is_alive]
            if len(alive_players) <= 1:
                await self.end_game(game, context)
                return
            
            while not game.players[game.current_player_index].is_alive:
                game.current_player_index = (game.current_player_index + 1) % len(game.players)
            
            current_player = game.players[game.current_player_index]
            mention = f"[{current_player.username}](tg://user?id={current_player.user_id})"
            prompt = f"{mention}, your turn! Say a word starting with '{game.last_letter.upper()}' (60s)."
            await context.bot.send_message(game.chat_id, prompt, parse_mode='Markdown')
            
            context.job_queue.run_once(
                self.send_turn_reminder, 30,
                data={'chat_id': game.chat_id, 'user_id': current_player.user_id},
                name=f"turn_reminder_{game.chat_id}_{current_player.user_id}"
            )
            context.job_queue.run_once(
                self.timeout_player, 60,
                data={'game': game, 'user_id': current_player.user_id},
                name=f"turn_timeout_{game.chat_id}_{current_player.user_id}"
            )
    
    async def send_turn_reminder(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        chat_id = job.data['chat_id']
        user_id = job.data['user_id']
        with self.game_lock:
            if chat_id in self.active_games:
                game = self.active_games[chat_id]
                if game.state == GameState.ACTIVE and any(p.user_id == user_id and p.is_alive for p in game.players):
                    mention = f"[{next(p.username for p in game.players if p.user_id == user_id)}](tg://user?id={user_id})"
                    await context.bot.send_message(chat_id, f"{mention}, 30s left!", parse_mode='Markdown')
    
    async def timeout_player(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        game = job.data['game']
        user_id = job.data['user_id']
        with self.game_lock:
            if game.chat_id in self.active_games and game == self.active_games[game.chat_id]:
                current_player = next((p for p in game.players if p.user_id == user_id and p.is_alive), None)
                if current_player:
                    await self.eliminate_player(context, game, current_player, "Time's up!", None)
    
    async def eliminate_player(self, context: ContextTypes.DEFAULT_TYPE, game: WordChainGame, player: GamePlayer, reason: str, update: Update):
        player.is_alive = False
        mention = f"[{player.username}](tg://user?id={player.user_id})"
        await context.bot.send_message(game.chat_id, f"{mention} is out: {reason}", parse_mode='Markdown')
        
        alive_players = [p for p in game.players if p.is_alive]
        if len(alive_players) <= 1:
            await self.end_game(game, context)
        else:
            game.current_player_index = (game.current_player_index + 1) % len(game.players)
            await self.next_turn(update, game, context)
    
    async def end_game(self, game: WordChainGame, context: ContextTypes.DEFAULT_TYPE):
        with self.game_lock:
            game.state = GameState.FINISHED
            alive_players = [p for p in game.players if p.is_alive]
            
            if not alive_players:
                await context.bot.send_message(game.chat_id, "Game over! No winners.")
            else:
                total_coins = sum(p.coins for p in game.players)
                winners = len(alive_players)
                coins_per_winner = total_coins // winners if winners > 0 else 0
                
                for player in alive_players:
                    self.db.update_user_coins(player.user_id, coins_per_winner)
                    with self.db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE users SET games_won = games_won + 1, games_played = games_played + 1,
                            total_coins_won = total_coins_won + %s
                            WHERE user_id = %s
                        """, (coins_per_winner, player.user_id))
                        conn.commit()
                
                for player in game.players:
                    if not player.is_alive:
                        with self.db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE users SET games_played = games_played + 1,
                                total_coins_lost = total_coins_lost + %s
                                WHERE user_id = %s
                            """, (player.coins, player.user_id))
                            conn.commit()
                
                winner_names = ", ".join([p.username for p in alive_players])
                await context.bot.send_message(
                    game.chat_id,
                    f"🏆 Game over! Winner(s): {winner_names}\nEach gets {coins_per_winner} coins!"
                )
            
            del self.active_games[game.chat_id]
            self.cancel_game_jobs(game.chat_id, context)
    
    async def accept_challenge(self, query, user: User, challenge_id: str, context: ContextTypes.DEFAULT_TYPE):
        with self.challenge_lock:
            if challenge_id not in self.pending_challenges:
                await query.answer("Challenge not found or expired!")
                return
            challenge = self.pending_challenges[challenge_id]
            if user.id != challenge.challenged_id:
                await query.answer("This challenge is not for you!")
                return
            if challenge.state != ChallengeState.PENDING:
                await query.answer("Challenge already handled!")
                return
            
            challenge.state = ChallengeState.ACCEPTED
        
        user_data = self.db.get_user(user.id)
        if user_data['coins'] < challenge.stake:
            await query.answer(f"❌ Need {challenge.stake} coins!")
            with self.challenge_lock:
                challenge.state = ChallengeState.DECLINED
                del self.pending_challenges[challenge_id]
            await query.message.chat.send_message(f"Challenge declined: @{user.username} lacks coins.")
            return
        
        game_id = f"wc_{challenge.chat_id}_{int(datetime.now(timezone.utc).timestamp())}"
        game = WordChainGame(
            chat_id=challenge.chat_id, game_id=game_id, state=GameState.ACTIVE,
            players=[
                GamePlayer(challenge.challenger_id, self.db.get_user(challenge.challenger_id)['username'], challenge.stake),
                GamePlayer(challenge.challenged_id, user.username or user.first_name, challenge.stake)
            ],
            current_player_index=0, words_used=[], current_word="", last_letter="",
            stake=challenge.stake, creator_id=challenge.challenger_id
        )
        
        with self.game_lock:
            self.active_games[challenge.chat_id] = game
        
        self.db.update_user_coins(challenge.challenger_id, -challenge.stake)
        self.db.update_user_coins(challenge.challenged_id, -challenge.stake)
        
        await query.message.chat.send_message(
            f"Challenge accepted! Starting Word Chain game between "
            f"[{self.db.get_user(challenge.challenger_id)['username']}](tg://user?id={challenge.challenger_id}) "
            f"and [{user.username}](tg://user?id={user.id}) for {challenge.stake} coins each.",
            parse_mode='Markdown'
        )
        
        with self.challenge_lock:
            del self.pending_challenges[challenge_id]
        
        random.shuffle(game.players)
        await self.next_turn(None, game, context)
    
    async def decline_challenge(self, query, user: User, challenge_id: str):
        with self.challenge_lock:
            if challenge_id not in self.pending_challenges:
                await query.answer("Challenge not found or expired!")
                return
            challenge = self.pending_challenges[challenge_id]
            if user.id != challenge.challenged_id:
                await query.answer("This challenge is not for you!")
                return
            if challenge.state != ChallengeState.PENDING:
                await query.answer("Challenge already handled!")
                return
            
            challenge.state = ChallengeState.DECLINED
            await query.message.chat.send_message(
                f"Challenge declined by [{user.username}](tg://user?id={user.id}).",
                parse_mode='Markdown'
            )
            del self.pending_challenges[challenge_id]
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Update {update} caused error: {context.error}")
        if update and update.effective_chat:
            await update.effective_chat.send_message("An error occurred. Please try again later.")
    
    async def start_wordchain_game_from_message(self, update, chat_id, creator, stake):
        game_id = f"wc_{chat_id}_{int(datetime.now(timezone.utc).timestamp())}"
        game = WordChainGame(
            chat_id=chat_id, game_id=game_id, state=GameState.WAITING,
            players=[GamePlayer(creator.id, creator.username or creator.first_name, stake)],
            current_player_index=0, words_used=[], current_word="", last_letter="",
            stake=stake, creator_id=creator.id
        )
        with self.game_lock:
            self.active_games[chat_id] = game
        players_text = "\n".join([f"• {p.username}" for p in game.players])
        game_text = f"""
🎮 **Word Chain Game Lobby**

👤 **Creator:** {creator.first_name}
💰 **Entry Fee:** {game.stake} coins
👥 **Players:** {len(game.players)}

**Current Players:**
{players_text}

⏰ **Waiting...**
**Need 2+ players!**

Use /join!
        """
        keyboard = [
            [InlineKeyboardButton("🎮 Join Game", callback_data="join_game")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        lobby_message = await update.message.reply_text(game_text, reply_markup=reply_markup)
        game.lobby_message_id = lobby_message.message_id
    
    async def show_game_rules(self, query):
        rules_text = """
📋 **Word Chain Game Rules**

🎯 **Objective:**
Last player standing wins!

🎮 **How to Play:**
1. Take turns saying words
2. Start with last letter of previous word
3. No repeats
4. 60s/turn
5. Invalid words = out
6. Timeout = out

💰 **Coins:**
• Pay entry fee
• Winner takes all
• Split if multiple remain

✅ **Valid Words:**
• Real English words
• 3+ letters
• No proper nouns/abbreviations

❌ **Invalid:**
• Repeats
• Invalid words
• Over 60s
• Wrong letter

🏆 **Winning:**
• Last standing
• Collect coins
• Gain points

Good luck! 🍀
        """
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(rules_text, reply_markup=reply_markup)
    
    async def join_game(self, query, user: User, chat_id: int):
        with self.game_lock:
            if chat_id not in self.active_games:
                await query.answer("❌ No active game!")
                return
            game = self.active_games[chat_id]
            if game.state != GameState.WAITING:
                await query.answer("❌ Game started!")
                return
            if any(p.user_id == user.id for p in game.players):
                await query.answer("Already joined!")
                return
        
        self.db.create_or_update_user(user)
        user_data = self.db.get_user(user.id)
        if user_data['coins'] < game.stake:
            await query.answer(f"❌ Need {game.stake} coins!")
            return
        
        game.players.append(GamePlayer(user.id, user.username or user.first_name, game.stake))
        mention = f"[{user.first_name}](tg://user?id={user.id})"
        await query.message.chat.send_message(f"{mention} joined. Now {len(game.players)} players.", parse_mode='Markdown')
        
        players_text = "\n".join([f"• {p.username}" for p in game.players])
        creator_name = next(p.username for p in game.players if p.user_id == game.creator_id)
        game_text = f"""
🎮 **Word Chain Game Lobby**

👤 **Creator:** {creator_name}
💰 **Entry Fee:** {game.stake} coins
👥 **Players:** {len(game.players)}

**Current Players:**
{players_text}

⏰ **Waiting...**

Use /join!
        """
        keyboard = [[InlineKeyboardButton("🎮 Join Game", callback_data="join_game")]]
        if len(game.players) >= 2:
            keyboard.append([InlineKeyboardButton("▶️ Start Game", callback_data="start_wordchain")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(game_text, reply_markup=reply_markup)
        await query.answer(f"✅ {user.first_name} joined!")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Update {update} caused error: {context.error}")
        if update and update.effective_chat:
            await update.effective_chat.send_message("An error occurred. Please try again later.")

def main():
    global bot
    bot = GameBot()
    
    if not BOT_TOKEN or not WEBHOOK_URL:
        raise ValueError("BOT_TOKEN and WEBHOOK_URL environment variables must be set")
    
    async def setup_bot():
        await bot.start_bot()
        await bot.application.bot.set_webhook(url=WEBHOOK_URL)
        await asyncio.sleep(0.1)  # Allow pending tasks to complete
    
    asyncio.run(setup_bot())
    
    from flask import Flask, request
    app = Flask(__name__)
    
    @app.route('/healthz', methods=['GET'])
    def health_check():
        return "OK", 200
    
    @app.route('/webhook', methods=['POST'])
    def webhook():
        try:
            update = Update.de_json(request.get_json(), bot.application.bot)
            asyncio.run(bot.application.process_update(update))
        except Exception as e:
            logger.error(f"Error processing update: {e}")
        return "OK", 200
    
    print("🎮 Game Bot starting as web service...")
    print("📝 Ensure:")
    print("   1. BOT_TOKEN is set in environment")
    print("   2. DATABASE_URL is set in environment")
    print("   3. WEBHOOK_URL is set in environment (e.g., https://your-service.onrender.com/webhook)")
    print("   4. 'words.txt' is present")
    print("🚀 Launching...")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))

if __name__ == "__main__":
    main()
