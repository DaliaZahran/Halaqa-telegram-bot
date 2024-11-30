import os
# from constants import TOKEN, SUPABASE_URL, SUPABASE_KEY
from typing import List, Union, Dict, Optional, Any
import requests

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

import logging
import json
from pathlib import Path
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()

# Get tokens and keys from environment variables
TOKEN = os.getenv('TELEGRAM_TOKEN')


# Ensure TOKEN is loaded
if TOKEN:
    print("Token loaded successfully")
else:
    print("Failed to load token. Check Railway environment settings.")
    
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States and Constants
NAVIGATING_MENU = range(1)
CACHE_DIR = Path("file_cache")
CACHE_DIR.mkdir(exist_ok=True)

MENU_FILE = "menu_structure.json"

# Define user state storage
user_states: Dict[int, List[str]] = {}

class BotManager:
    @staticmethod
    def load_menu_structure() -> dict:
        """Load the menu structure from JSON file."""
        try:
            with open(MENU_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Menu file {MENU_FILE} not found")
            return {}

    @staticmethod
    def get_keyboard_for_menu(menu_dict: dict) -> ReplyKeyboardMarkup:
        """Create a keyboard for the current menu level."""
        keyboard = []
        
        # Add menu items as buttons
        for key in menu_dict.keys():
            keyboard.append([KeyboardButton(key)])

        # Add back button if not in root menu
        if keyboard:
            keyboard.append([KeyboardButton("🔙 رجوع")])

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    @staticmethod
    def get_menu_item(menu: dict, path: List[str]) -> Union[dict, None]:
        """Navigate to a specific menu item using the given path."""
        current = menu
        for item in path:
            if item not in current:
                return None
            current = current[item]
        return current

class FileHandler:
    @staticmethod
    async def download_file(file_url: str, timeout: int = 180) -> Optional[bytes]:
        """
        Download a file from a given URL with a configurable timeout.
        
        :param file_url: URL of the file to download
        :param timeout: Timeout in seconds (default 180)
        :return: File content as bytes or None
        """
        try:
            # Remove token and query parameters to get clean filename
            clean_url = file_url.split('?')[0]
            
            # Add authorization header for Supabase
            headers = {
                'apikey': SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}'
            }
            
            # Download the file
            response = requests.get(
                file_url, 
                headers=headers, 
                timeout=timeout
            )
            response.raise_for_status()
            
            # Log the file size for debugging
            logger.info(f"Downloaded file size: {len(response.content)} bytes")
            
            return response.content
        except requests.exceptions.Timeout:
            logger.error(f"Download timed out for URL: {file_url}")
            return None
        except requests.RequestException as e:
            logger.error(f"Error downloading file from {file_url}: {e}")
            return None

    @staticmethod
    async def send_file(
        update: Update, 
        file_url: str, 
        description: str = '', 
        file_type: Optional[str] = None
    ) -> bool:
        """
        Send a file from a given URL
        
        :param update: Telegram update object
        :param file_url: URL of the file to send
        :param description: Optional description for the file
        :param file_type: Optional file type (audio, document, etc.)
        :return: True if file sent successfully, False otherwise
        """
        try:
            # Send a "downloading" message
            downloading_message = await update.message.reply_text("جاري تحميل الملف...")
            
            # Download the file
            file_content = await FileHandler.download_file(file_url)
            
            if file_content:
                # Remove token and query parameters to get clean filename
                clean_url = file_url.split('?')[0]
                
                # Determine file extension
                if not file_type:
                    # Try to guess file type from URL
                    if clean_url.lower().endswith(('.mp3', '.wav', '.ogg')):
                        file_type = 'audio'
                    elif clean_url.lower().endswith(('.pdf', '.doc', '.docx', '.txt')):
                        file_type = 'document'
                    else:
                        file_type = 'document'
                
                # Create a unique temp file name
                user_id = update.effective_user.id
                file_ext = os.path.splitext(clean_url)[1] or '.pdf'
                temp_file_path = CACHE_DIR / f"{user_id}_temp{file_ext}"
                
                # Write file content
                with open(temp_file_path, 'wb') as temp_file:
                    temp_file.write(file_content)
                
                # Send the file based on type
                with open(temp_file_path, 'rb') as file:
                    if file_type == 'audio':
                        await update.message.reply_audio(
                            audio=file,
                            caption=description,
                            parse_mode='HTML'
                        )
                    else:
                        await update.message.reply_document(
                            document=file,
                            caption=description,
                            parse_mode='HTML'
                        )
                
                # Delete temporary message and file
                await downloading_message.delete()
                temp_file_path.unlink()
                
                return True
            else:
                # Edit the downloading message to show an error
                await downloading_message.edit_text("تعذر تحميل الملف. يرجى المحاولة مرة أخرى.")
                return False
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await update.message.reply_text("حدث خطأ أثناء إرسال الملف.")
            return False
    
class TelegramBot:
    @staticmethod
    async def start(update: Update, context: CallbackContext) -> int:
        """Handle the /start command."""
        user_id = update.effective_user.id
        
        # Reset user state to root menu
        user_states[user_id] = []

        menu_structure = BotManager.load_menu_structure()

        welcome_message = (
            "🌟 مرحباً بكم في بوت حلقات المعلمة ولاء\n\n"
            "يمكنك اختيار القسم المطلوب من القائمة أدناه"
        )

        await update.message.reply_text(
            welcome_message,
            reply_markup=BotManager.get_keyboard_for_menu(menu_structure),
            parse_mode='HTML'
        )

        return NAVIGATING_MENU

    @staticmethod
    async def handle_menu_navigation(update: Update, context: CallbackContext) -> int:
        """Handle menu navigation and file sending."""
        user_id = update.effective_user.id
        text = update.message.text

        # Ensure user states exist
        if user_id not in user_states:
            user_states[user_id] = []

        current_path = user_states[user_id]
        menu_structure = BotManager.load_menu_structure()

        if text == "🔙 رجوع":
            # Go back one level
            if current_path:
                current_path.pop()
        else:
            # Get current menu level
            current_menu = BotManager.get_menu_item(menu_structure, current_path)

            if current_menu and text in current_menu:
                next_level = current_menu[text]
                
                # If the item is a dictionary with 'file_id', it's a document to send
                if isinstance(next_level, dict):
                    # Check for file
                    if 'file_id' in next_level:
                        file_url = next_level['file_id']
                        description = next_level.get('description', '')
                        file_type = next_level.get('type', None)
                        
                        # Attempt to send the file
                        await FileHandler.send_file(
                            update, 
                            file_url, 
                            description, 
                            file_type
                        )
                    
                    # Check for external link
                    if 'link' in next_level:
                        link = next_level['link']
                        description = next_level.get('description', '')
                        
                        # Create an inline keyboard with the link
                        keyboard = [
                            [InlineKeyboardButton(
                                "📎 فتح الرابط", 
                                url=link
                            )]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        # Send message with link
                        await update.message.reply_text(
                            f"{description}\n\nرابط: {link}",
                            reply_markup=reply_markup,
                            parse_mode='HTML'
                        )
                    
                    # If it's a submenu, navigate into it
                    if any(isinstance(val, dict) for val in next_level.values()):
                        current_path.append(text)

        # Get the current menu level after navigation
        current_menu = BotManager.get_menu_item(menu_structure, current_path)

        if current_menu:
            # Generate menu title
            menu_title = f"قائمة {current_path[-1] if current_path else 'الرئيسية'}:"
            await update.message.reply_text(
                menu_title,
                reply_markup=BotManager.get_keyboard_for_menu(current_menu)
            )
        else:
            await update.message.reply_text("الرجاء اختيار خيار صحيح.")

        return NAVIGATING_MENU

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token
    application = Application.builder().token(TOKEN).build()

    # Set up command and message handlers
    application.add_handler(CommandHandler('start', TelegramBot.start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, TelegramBot.handle_menu_navigation))

    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()






# next:
# deploy
# update menu and files.