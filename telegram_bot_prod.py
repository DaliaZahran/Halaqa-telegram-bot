import os
# from constants import TOKEN, SUPABASE_URL, SUPABASE_KEY
from typing import List, Union, Dict, Optional, Any
import requests
import re
import tempfile
# import shutil

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

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
    def cleanup_temp_files(max_age_hours: int = 24*30):
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
        timeout: int = 300,
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
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        file_url: str,
        description: str = '',
        file_type: Optional[str] = None,
        custom_filename: Optional[str] = None,
        supabase_key: Optional[str] = None
    ) -> bool:
        try:
            # Notify user that the file is being downloaded
            downloading_message = await update.message.reply_text("جاري تحميل الملف...")
            print('file_url ==> ', file_url)
            # Download the file (call your download logic here)
            file_content = await FileHandler.download_file(
                file_url,
                timeout=600,  # Increased timeout for large files
                supabase_key=supabase_key
            )

            if file_content:
                logging.info(
                    f"Successfully downloaded file of size {len(file_content)} bytes")

                clean_url = file_url.split('?')[0]

                # Determine file type if not provided
                if not file_type:
                    file_type = 'document'  # Default type if not specified

                # Determine file extension
                file_ext = os.path.splitext(clean_url)[1] or '.pdf'

                # Generate filename for temporary storage
                user_id = update.effective_user.id
                temp_file_path = FileHandler.TEMP_DIR / \
                    f"{user_id}_temp{file_ext}"

                # Save the file
                with open(temp_file_path, 'wb') as temp_file:
                    temp_file.write(file_content)

                # Send the file to the user with extended timeout
                with open(temp_file_path, 'rb') as file:
                    await context.bot.send_chat_action(
                        chat_id=update.message.chat_id, action="upload_document"
                    )
                    await asyncio.sleep(2)  # Small delay to avoid rate limits

                    # Send the file
                    await update.message.reply_document(
                        document=file,
                        caption=description,
                        read_timeout=300  # Increased timeout for sending large files
                    )

                # Delete temporary message and file after sending
                await downloading_message.delete()
                temp_file_path.unlink()

                return True
            else:
                logging.error("Failed to download file content")
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

        # If main menu button is pressed, reset to root menu
        if text == "🏠 القائمة الرئيسية":
            user_states[user_id] = []
            menu_structure = BotManager.load_menu_structure()

            await update.message.reply_text(
                "🌟 تم العودة إلى القائمة الرئيسية\nيمكنك اختيار القسم المطلوب من القائمة أدناه",
                reply_markup=BotManager.get_keyboard_for_menu(menu_structure),
                parse_mode='HTML'
            )
            return NAVIGATING_MENU

        current_path = user_states[user_id]
        menu_structure = BotManager.load_menu_structure()

        if text == "🔙 رجوع":
            if current_path:
                current_path.pop()
        else:
            current_menu = BotManager.get_menu_item(
                menu_structure, current_path)

            if current_menu and text in current_menu:
                next_level = current_menu[text]
                print('next_level, ', next_level)
                file_sent = False  # Flag to track if a file was sent

                # Handle multiple files
                if 'file_ids' in next_level:
                    files = next_level['file_ids']
                    print('DALIA :::: files', files)
                    for file_metadata in files:
                        file_url = file_metadata['file_id']
                        file_type = file_metadata.get('type', None)
                        custom_filename = file_metadata.get('filename', None)
                        success = await FileHandler.send_file(update, file_url, '', file_type, custom_filename)
                        if success:
                            file_sent = True

                # Handle single file
                elif 'file_id' in next_level:
                    file_url = next_level['file_id']
                    print('DALIA :::: file_url', file_url)
                    file_type = next_level.get('type', None)
                    custom_filename = next_level.get('filename', None)
                    success = await FileHandler.send_file(update, file_url, '', file_type, custom_filename)
                    if success:
                        file_sent = True

                # Handle external link
                if 'link' in next_level:
                    link = next_level['link']
                    keyboard = [
                        [InlineKeyboardButton("📎 فتح الرابط", url=link)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.message.reply_text(f"رابط: {link}", reply_markup=reply_markup, parse_mode='HTML')

                # Handle external list of links
                if 'links' in next_level:
                    links = next_level['links']
                    joined_links = '\n\n'.join(links)
                    await update.message.reply_text(f"تدريبات خارجية:\n{joined_links}", parse_mode='HTML')

                # If it's a submenu, navigate into it
                if any(isinstance(val, dict) for val in next_level.values()):
                    current_path.append(text)

                # **Do not send menu name if a file was sent**
                if file_sent:
                    return NAVIGATING_MENU

        # Get the current menu level after navigation
        current_menu = BotManager.get_menu_item(menu_structure, current_path)

        if current_menu:
            await update.message.reply_text(
                f"قائمة {current_path[-1] if current_path else 'الرئيسية'}:",
                reply_markup=BotManager.get_keyboard_for_menu(current_menu)
            )
        else:
            await update.message.reply_text("الرجاء اختيار خيار صحيح.")

        return NAVIGATING_MENU

    @staticmethod
    async def return_to_main_menu(update: Update, context: CallbackContext) -> int:
        """Handle the return to main menu command."""
        user_id = update.effective_user.id

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
    # application = Application.builder().token(TOKEN).build()
    application = Application.builder().token(
        '7063300350:AAFTn_UAxLXkn1KSV_MBiTrlX6vHg3Qk7q0').build()
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
