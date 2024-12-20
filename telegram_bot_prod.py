import os
# from constants import TOKEN, SUPABASE_URL, SUPABASE_KEY
from typing import List, Union, Dict, Optional, Any
import requests
import re
import tempfile
# import shutil

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
            keyboard.append([
                KeyboardButton("🔙 رجوع"),
                KeyboardButton("🏠 القائمة الرئيسية")  # New main menu button
            ])
        else:
            keyboard.append([KeyboardButton("🏠 القائمة الرئيسية")])

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
    # Configure a temporary directory for file caching
    TEMP_DIR = Path(tempfile.mkdtemp(prefix='telegram_bot_'))

    @staticmethod
    def cleanup_temp_files(max_age_hours: int = 24):
        """
        Clean up temporary files older than the specified hours.

        :param max_age_hours: Maximum age of temporary files in hours
        """
        import time

        try:
            current_time = time.time()
            for item in FileHandler.TEMP_DIR.glob('*'):
                # Check if the file is older than max_age_hours
                if item.is_file() and (current_time - item.stat().st_mtime) > (max_age_hours * 3600):
                    try:
                        item.unlink()
                    except Exception as e:
                        logging.error(
                            f"Error deleting temporary file {item}: {e}")
        except Exception as e:
            logging.error(f"Error during temp file cleanup: {e}")

    @staticmethod
    async def parse_google_drive_link(file_url: str) -> Optional[str]:
        """
        Parse and convert Google Drive public link to a direct download link.

        :param file_url: Google Drive file URL
        :return: Direct download link or None
        """
        # Patterns to match different Google Drive link formats
        drive_patterns = [
            # New format
            r'https://drive\.google\.com/file/d/([^/]+)/view\?usp=drive_link',
            r'https://drive\.google\.com/file/d/([^/]+)/view\?usp=sharing',
            r'https://drive\.google\.com/open\?id=([^&]+)',
            r'https://drive\.google\.com/uc\?id=([^&]+)',
        ]

        for pattern in drive_patterns:
            match = re.search(pattern, file_url)
            if match:
                file_id = match.group(1)
                # Construct direct download link
                return f'https://drive.google.com/uc?export=download&id={file_id}'

        return None

    @staticmethod
    async def download_file(
        file_url: str,
        timeout: int = 180,
        supabase_key: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Download a file from a given URL with a configurable timeout.
        Supports Supabase and Google Drive public links.

        :param file_url: URL of the file to download
        :param timeout: Timeout in seconds (default 180)
        :param supabase_key: Optional Supabase API key
        :return: File content as bytes or None
        """
        try:
            # Check if it's a Google Drive link and convert to direct download link
            if 'drive.google.com' in file_url:
                direct_link = await FileHandler.parse_google_drive_link(file_url)
                if not direct_link:
                    logging.error(f"Invalid Google Drive link: {file_url}")
                    return None
                file_url = direct_link

            # Remove token and query parameters to get clean filename
            clean_url = file_url.split('?')[0]

            # Determine headers based on URL type
            headers = {}
            if supabase_key and ('supabase' in file_url or supabase_key in file_url):
                headers = {
                    'apikey': supabase_key,
                    'Authorization': f'Bearer {supabase_key}'
                }

            # Download the file
            response = requests.get(
                file_url,
                headers=headers,
                timeout=timeout,
                stream=True  # Add streaming to handle larger files
            )
            response.raise_for_status()

            # Log the file size for debugging
            content = response.content
            logging.info(f"Downloaded file size: {len(content)} bytes")

            return content

        except requests.exceptions.SSLError as ssl_err:
            logging.error(f"SSL Certificate Verification Error: {ssl_err}")
            return None
        except requests.exceptions.ConnectionError as conn_err:
            logging.error(f"Connection Error: {conn_err}")
            return None
        except requests.exceptions.Timeout:
            logging.error(f"Download timed out for URL: {file_url}")
            return None
        except requests.RequestException as e:
            logging.error(f"Comprehensive download error from {file_url}: {e}")
            return None

    @staticmethod
    async def send_file(
        update,
        file_url: str,
        description: str = '',
        file_type: Optional[str] = None,
        custom_filename: Optional[str] = None,
        supabase_key: Optional[str] = None
    ) -> bool:
        """
        Send a file from a given URL

        :param update: Telegram update object
        :param file_url: URL of the file to send
        :param description: Optional description for the file
        :param file_type: Optional file type (audio, document, etc.)
        :param custom_filename: Optional custom filename to use
        :param supabase_key: Optional Supabase API key
        :return: True if file sent successfully, False otherwise
        """
        try:
            # Send a "downloading" message
            downloading_message = await update.message.reply_text("جاري تحميل الملف...")

            # Download the file
            file_content = await FileHandler.download_file(
                file_url,
                supabase_key=supabase_key
            )

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

                # Determine file extension
                file_ext = os.path.splitext(clean_url)[1] or '.pdf'

                # Use custom filename if provided, otherwise generate a temp filename
                user_id = update.effective_user.id
                if custom_filename:
                    # Ensure the filename has the correct extension
                    if not custom_filename.lower().endswith(file_ext):
                        custom_filename += file_ext
                    temp_file_path = FileHandler.TEMP_DIR / custom_filename
                else:
                    temp_file_path = FileHandler.TEMP_DIR / \
                        f"{user_id}_temp{file_ext}"

                # Write file content
                with open(temp_file_path, 'wb') as temp_file:
                    temp_file.write(file_content)

                # Send the file based on type
                with open(temp_file_path, 'rb') as file:
                    if file_type == 'audio':
                        await update.message.reply_audio(
                            audio=file,
                            # caption=description,
                            parse_mode='HTML'
                        )
                    else:
                        await update.message.reply_document(
                            document=file,
                            # caption=description,
                            parse_mode='HTML'
                        )

                # Delete temporary message and delete file afterwards
                await downloading_message.delete()
                temp_file_path.unlink()

                return True
            else:
                # Edit the downloading message to show an error
                await downloading_message.edit_text("تعذر تحميل الملف. يرجى المحاولة مرة أخرى.")
                return False
        except Exception as e:
            logging.error(f"Error sending file: {e}")
            await update.message.reply_text("حدث خطأ أثناء إرسال الملف.")
            return False


class TelegramBot:
    @staticmethod
    async def start(update: Update, context: CallbackContext) -> int:
        """Handle the /start command."""
        user_id = update.effective_user.id

        # Delete the start command message
        try:
            await update.message.delete()
        except Exception as e:
            logging.error(f"Could not delete start command message: {e}")

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

        # Delete the user's original message
        try:
            await update.message.delete()
        except Exception as e:
            logging.error(f"Could not delete user's message: {e}")

        # Ensure user states exist
        if user_id not in user_states:
            user_states[user_id] = []

        # If main menu button is pressed, reset to root menu
        if text == "🏠 القائمة الرئيسية":
            user_states[user_id] = []
            menu_structure = BotManager.load_menu_structure()

            welcome_message = (
                "🌟 تم العودة إلى القائمة الرئيسية\n"
                "يمكنك اختيار القسم المطلوب من القائمة أدناه"
            )

            await update.message.reply_text(
                welcome_message,
                reply_markup=BotManager.get_keyboard_for_menu(menu_structure),
                parse_mode='HTML'
            )
            return NAVIGATING_MENU

        current_path = user_states[user_id]
        menu_structure = BotManager.load_menu_structure()

        if text == "🔙 رجوع":
            # Go back one level
            if current_path:
                current_path.pop()
        else:
            # Get current menu level
            current_menu = BotManager.get_menu_item(
                menu_structure, current_path)

            if current_menu and text in current_menu:
                next_level = current_menu[text]

                # If the item is a dictionary with 'file_id', it's a document to send
                if isinstance(next_level, dict):
                    # Check for file
                    if 'file_id' in next_level:
                        file_url = next_level['file_id']
                        description = next_level.get('description', '')
                        file_type = next_level.get('type', None)
                        custom_filename = next_level.get('filename', None)

                        # Attempt to send the file
                        await FileHandler.send_file(
                            update,
                            file_url,
                            description,
                            file_type,
                            custom_filename
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

    @staticmethod
    async def return_to_main_menu(update: Update, context: CallbackContext) -> int:
        """Handle the return to main menu command."""
        user_id = update.effective_user.id

        # Delete the main menu command message
        try:
            await update.message.delete()
        except Exception as e:
            logging.error(f"Could not delete main menu command message: {e}")

        # Reset user state to root menu
        user_states[user_id] = []

        menu_structure = BotManager.load_menu_structure()

        welcome_message = (
            "🌟 تم العودة إلى القائمة الرئيسية\n"
            "يمكنك اختيار القسم المطلوب من القائمة أدناه"
        )

        await update.message.reply_text(
            welcome_message,
            reply_markup=BotManager.get_keyboard_for_menu(menu_structure),
            parse_mode='HTML'
        )

        return NAVIGATING_MENU


def main() -> None:
    """Start the bot."""
    # Periodic cleanup of temporary files
    FileHandler.cleanup_temp_files()

    # Create the Application and pass it your bot's token
    application = Application.builder().token(TOKEN).build()

    # Set up command and message handlers
    application.add_handler(CommandHandler('start', TelegramBot.start))
    application.add_handler(CommandHandler(
        'main_menu', TelegramBot.return_to_main_menu))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, TelegramBot.handle_menu_navigation))

    # Run the bot
    application.run_polling()


if __name__ == '__main__':
    main()
