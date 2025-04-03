import os
from typing import List, Union, Dict, Optional, Any
import asyncio
import asyncpg
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get tokens and database connection info
TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States and Constants
NAVIGATING_MENU = range(1)
user_states: Dict[int, List[str]] = {}

class DatabaseManager:
    def __init__(self):
        self.pool = None

    async def init_pool(self):
        """Initialize the connection pool."""
        self.pool = await asyncpg.create_pool(DATABASE_URL)

    async def get_menu_items(self, parent_id: Optional[str] = None) -> List[dict]:
        """Get menu items for a given parent_id."""
        async with self.pool.acquire() as conn:
            if parent_id is None:
                # Get root menu items
                items = await conn.fetch("""
                    SELECT id, title 
                    FROM menu_items 
                    WHERE parent_id IS NULL 
                    ORDER BY order_index
                """)
            else:
                # Get child menu items
                items = await conn.fetch("""
                    SELECT id, title 
                    FROM menu_items 
                    WHERE parent_id = $1 
                    ORDER BY order_index
                """, parent_id)
            return [dict(item) for item in items]

    async def get_content(self, menu_item_id: str) -> Optional[dict]:
        """Get content for a menu item."""
        async with self.pool.acquire() as conn:
            content = await conn.fetchrow("""
                SELECT type, file_url, description, filename 
                FROM content 
                WHERE menu_item_id = $1
            """, menu_item_id)
            return dict(content) if content else None

    async def get_menu_links(self, menu_item_id: str) -> List[dict]:
        """Get links for a menu item."""
        async with self.pool.acquire() as conn:
            links = await conn.fetch("""
                SELECT url, description 
                FROM menu_links 
                WHERE menu_item_id = $1
            """, menu_item_id)
            return [dict(link) for link in links]

class BotManager:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_keyboard_for_menu(self, items: List[dict]) -> ReplyKeyboardMarkup:
        """Create a keyboard for the current menu level."""
        keyboard = []

        # Add menu items as buttons
        for item in items:
            keyboard.append([KeyboardButton(item['title'])])

        # Add navigation buttons
        if keyboard:
            keyboard.append([
                KeyboardButton("🔙 رجوع"),
                KeyboardButton("🏠 القائمة الرئيسية")
            ])
        else:
            keyboard.append([KeyboardButton("🏠 القائمة الرئيسية")])

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

class TelegramBot:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.bot_manager = BotManager(db_manager)

    async def start(self, update: Update, context: CallbackContext) -> int:
        """Handle the /start command."""
        user_id = update.effective_user.id
        user_states[user_id] = []

        # Get root menu items
        menu_items = await self.db.get_menu_items()

        welcome_message = (
            "🌟 مرحباً بكم في بوت حلقات المعلمة ولاء\n\n"
            "يمكنك اختيار القسم المطلوب من القائمة أدناه"
        )

        await update.message.reply_text(
            welcome_message,
            reply_markup=await self.bot_manager.get_keyboard_for_menu(menu_items),
            parse_mode='HTML'
        )

        return NAVIGATING_MENU

    async def handle_menu_navigation(self, update: Update, context: CallbackContext) -> int:
        """Handle menu navigation and content delivery."""
        user_id = update.effective_user.id
        text = update.message.text

        # Initialize user state if needed
        if user_id not in user_states:
            user_states[user_id] = []

        # Handle main menu request
        if text == "🏠 القائمة الرئيسية":
            user_states[user_id] = []
            menu_items = await self.db.get_menu_items()
            
            await update.message.reply_text(
                "🌟 تم العودة إلى القائمة الرئيسية",
                reply_markup=await self.bot_manager.get_keyboard_for_menu(menu_items)
            )
            return NAVIGATING_MENU

        # Get current menu items
        current_menu_id = user_states[user_id][-1] if user_states[user_id] else None
        menu_items = await self.db.get_menu_items(current_menu_id)

        # Handle back button
        if text == "🔙 رجوع":
            if user_states[user_id]:
                user_states[user_id].pop()
                parent_id = user_states[user_id][-1] if user_states[user_id] else None
                menu_items = await self.db.get_menu_items(parent_id)
        else:
            # Find selected menu item
            selected_item = next((item for item in menu_items if item['title'] == text), None)
            
            if selected_item:
                # Check for content
                content = await self.db.get_content(selected_item['id'])
                if content:
                    await FileHandler.send_file(
                        update,
                        content['file_url'],
                        content['description'],
                        content['type'],
                        content['filename']
                    )

                # Check for links
                links = await self.db.get_menu_links(selected_item['id'])
                if links:
                    for link in links:
                        keyboard = [[InlineKeyboardButton("📎 فتح الرابط", url=link['url'])]]
                        await update.message.reply_text(
                            f"{link['description']}\n\nرابط: {link['url']}",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )

                # Update navigation state
                child_items = await self.db.get_menu_items(selected_item['id'])
                if child_items:
                    user_states[user_id].append(selected_item['id'])
                    menu_items = child_items

        # Show current menu
        await update.message.reply_text(
            "اختر من القائمة:",
            reply_markup=await self.bot_manager.get_keyboard_for_menu(menu_items)
        )
        return NAVIGATING_MENU

async def main():
    """Start the bot."""
    # Initialize database
    db_manager = DatabaseManager()
    await db_manager.init_pool()

    # Initialize bot
    bot = TelegramBot(db_manager)
    
    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler('start', bot.start))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        bot.handle_menu_navigation
    ))

    # Start the bot
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
