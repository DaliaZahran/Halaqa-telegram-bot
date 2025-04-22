# Fix import order - group standard library imports first
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import List, Union, Dict, Optional, Any
import asyncio
import time
import pandas as pd
import requests
from io import StringIO

# Third-party imports next
from dotenv import load_dotenv
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application, 
    ContextTypes, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    CallbackContext
)


# Load environment variables from .env file
load_dotenv()

# Get tokens and keys from environment variables
TOKEN = os.getenv('TELEGRAM_TOKEN')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
# SPREADSHEET_ID = "1JkH43uYYx6w6nSU9_AyRTNoRHe_6WBmwCQa9suDJTks"
# ---for dev use only---
# SPREADSHEET_ID = "1VohF-QrsQn8Dqohxnafa2bB-fA3QJv6GuAlbhMSNTTA"


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

ADMIN_IDS = [1063033882, 1007456634]  # real Telegram user IDs

class GoogleSheetsHandler:
    """Handles Google Sheets operations for menu structure."""
    
    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        self._menu_structure = None
        self._last_update = 0
        self._update_interval = 300  # Update menu structure every 5 minutes
        
    def _get_all_sheet_names(self) -> List[str]:
        """Fetch all sheet names from the spreadsheet using Google Sheets API."""
        try:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("Missing GOOGLE_API_KEY environment variable.")

            url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}?fields=sheets.properties.title&key={api_key}"
            response = requests.get(url)
            response.raise_for_status()

            data = response.json()
            sheet_titles = [sheet['properties']['title'] for sheet in data.get('sheets', [])]

            logger.info(f"Found sheets: {sheet_titles}")
            return sheet_titles

        except Exception as e:
            logger.error(f"Error fetching sheet names: {e}")
            return []
    

    def _get_sheet_data(self, sheet_name: str) -> pd.DataFrame:
        """Fetch a specific sheet's data using public CSV export link."""
        try:
            # Encode the sheet name correctly for URL (replace spaces with %20 etc.)
            encoded_sheet_name = sheet_name.replace(' ', '%20')
            sheet_url = f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_sheet_name}"

            logger.info(f"Fetching sheet: {sheet_name} from URL: {sheet_url}")
            response = requests.get(sheet_url)
            response.raise_for_status()

            response.encoding = 'utf-8'

            df = pd.read_csv(StringIO(response.text), encoding='utf-8', encoding_errors='replace')

            # Clean up column names
            df.columns = [str(col).strip() for col in df.columns]
            df = df.dropna(how='all', axis=1)

            return df

        except Exception as e:
            logger.error(f"Error fetching data for sheet {sheet_name}: {e}")
            return pd.DataFrame()

    def _parse_file_entry(self, value: str) -> tuple[str, str, str]:
        """Parse a file entry that might contain both name and URL.
        Returns tuple of (display_name, url, type)"""
        if pd.isna(value):
            return None, None, None
            
        # Clean up the value
        value = str(value).strip()
        if not value:
            return None, None, None
            
        # Check if it's a URL
        if value.startswith('http://') or value.startswith('https://'):
            # Determine type based on URL
            if 'drive.google.com' in value:
                # For Google Drive links, try to determine type from the filename
                filename = value.split('/')[-1].split('?')[0]
                if any(ext in filename.lower() for ext in ['.mp3', '.m4a', '.wav']):
                    return filename, value, 'audio'
                elif any(ext in filename.lower() for ext in ['.pdf', '.doc', '.docx']):
                    return filename, value, 'document'
                else:
                    return filename, value, 'document'
            else:
                return "تدريب", value, 'external_link'
                
        # Check if it's a multiline entry (name and URL on separate lines)
        lines = [line.strip() for line in value.split('\n') if line.strip()]
        if len(lines) >= 2:
            display_name = lines[0]
            url = lines[1]
            
            # Determine type based on URL or filename
            if any(ext in display_name.lower() or ext in url.lower() 
                    for ext in ['.mp3', '.m4a', '.wav']):
                return display_name, url, 'audio'
            elif any(ext in display_name.lower() or ext in url.lower() 
                    for ext in ['.pdf', '.doc', '.docx']):
                return display_name, url, 'document'
            else:
                return display_name, url, 'external_link'
                
        return None, None, None

    def get_menu_structure(self) -> dict:
        """Fetch and parse menu structure from all sheets in the spreadsheet."""
        try:
            menu_structure = {}

            sheet_names = self._get_all_sheet_names()
            if not sheet_names:
                logger.error("No sheets found in the spreadsheet.")
                return {}

            for sheet_name in sheet_names:
                logger.info(f"Processing sheet: {sheet_name}")

                df = self._get_sheet_data(sheet_name=sheet_name)
                if df.empty:
                    logger.warning(f"Sheet '{sheet_name}' is empty.")
                    continue

                current_sheet_tree = {}

                for idx, row in df.iterrows():
                    if row.isna().all():
                        continue
                    
                    # 🛑 Check Parent_Folder
                    if not str(row.get('Parent_Folder', '')).strip() or str(row.get('Parent_Folder', '')).strip() == 'none':
                        logger.warning(f"Skipping row {idx} due to empty Parent_Folder.")
                        continue
                    
                    current_level = current_sheet_tree
                    
                    # Folder hierarchy
                    folder_cols = [col for col in df.columns if col.startswith('Folder_')]
                    for folder_col in folder_cols:
                        folder_name = str(row.get(folder_col, '')).strip()
                        folder_name_normalized = folder_name.lower()
                        
                        # 🛑 Skip if empty, "-", "none" or "nan"
                        if not folder_name or folder_name_normalized in ['-', 'none', 'nan']:
                            continue
                        
                        if folder_name not in current_level:
                            current_level[folder_name] = {}
                        current_level = current_level[folder_name]

                    # File links
                    file_cols = [col for col in df.columns if col.startswith('File_link_')]
                    has_content = False
                    
                    for file_col in file_cols:
                        file_entry = str(row.get(file_col, '')).strip()
                        if not file_entry:
                            continue

                        display_name, file_url, file_type = self._parse_file_entry(file_entry)
                        if not display_name or not file_url:
                            logger.warning(f"Skipping invalid file entry in row {idx}.")
                            continue

                        has_content = True
                        if file_type == 'external_link':
                            current_level.setdefault('external_links', []).append({
                                'name': display_name,
                                'url': file_url
                            })
                        else:
                            current_level.setdefault('file_ids', []).append({
                                'file_id': file_url,
                                'type': file_type,
                                'filename': display_name
                            })
                    
                    # Only keep the menu item if it has content (files or submenus)
                    if not has_content and not any(isinstance(val, dict) for val in current_level.values()):
                        # Remove empty menu items by going back up the hierarchy
                        parent = current_sheet_tree
                        path = []
                        
                        # Build the path to the current node
                        for folder_col in folder_cols:
                            folder_name = str(row.get(folder_col, '')).strip()
                            if folder_name and folder_name.lower() not in ['-', 'none', 'nan']:
                                path.append(folder_name)
                        
                        # Remove the empty menu item and clean up empty parents
                        if path:
                            # Remove the leaf node
                            current = parent
                            for folder_name in path[:-1]:
                                current = current[folder_name]
                            if path[-1] in current:
                                del current[path[-1]]
                            
                            # Recursively clean up empty parent nodes
                            def clean_empty_parents(node, current_path):
                                if not current_path:
                                    return
                                    
                                parent = node
                                for folder_name in current_path[:-1]:
                                    parent = parent[folder_name]
                                    
                                # Check if the current node is empty
                                if not parent[current_path[-1]]:
                                    del parent[current_path[-1]]
                                    # Recursively check parent
                                    clean_empty_parents(node, current_path[:-1])
                            
                            clean_empty_parents(parent, path[:-1])

                menu_structure[sheet_name] = current_sheet_tree

            print("Final Menu Structure:")
            print(json.dumps(menu_structure, indent=2, ensure_ascii=False))
            return menu_structure

        except Exception as e:
            logger.error(f"Error building menu structure: {e}")
            import traceback
            traceback.print_exc()
            return {}


class BotManager:
    """Manages the bot's menu structure and keyboard layouts."""
    
    def __init__(self):
        """Initialize the BotManager with Google Sheets handler."""
        self.sheets_handler = GoogleSheetsHandler(SPREADSHEET_ID)
        self._menu_structure = None
        self._last_update = 0
        self._update_interval = 300  # Update menu structure every 5 minutes

    def load_menu_structure(self, force_reload: bool = False) -> dict:
        """Load menu structure with caching; force reload if needed."""
        current_time = time.time()

        if (
            self._menu_structure is None or
            (current_time - self._last_update) > self._update_interval or
            force_reload
        ):
            self._menu_structure = self.sheets_handler.get_menu_structure()
            self._last_update = current_time

        return self._menu_structure

    @staticmethod
    def get_keyboard_for_menu(menu_dict: dict) -> ReplyKeyboardMarkup:
        """Create a keyboard for the current menu level."""
        keyboard = []

        # Add menu items as buttons
        for key in menu_dict.keys():
            if key != "file_ids":  # Skip file_ids as they're not menu items
                # Ensure proper encoding of Arabic text
                button_text = str(key).encode('utf-8').decode('utf-8')
                keyboard.append([KeyboardButton(button_text)])

        # Add back button if not in root menu
        if keyboard:
            keyboard.append([
                KeyboardButton("🔙 رجوع"),
                KeyboardButton("🏠 القائمة الرئيسية")
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
    """Handles file operations including downloads and temporary storage."""
    # Configure a temporary directory for file caching
    TEMP_DIR = Path(tempfile.mkdtemp(prefix='telegram_bot_'))

    @staticmethod
    def cleanup_temp_files(max_age_hours: int = 24*30):
        """
        Clean up temporary files older than the specified hours.

        :param max_age_hours: Maximum age of temporary files in hours
        """
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

              # Determine file extension
                if not file_type:
                    # Try to guess file type from URL
                    if clean_url.lower().endswith(('.mp3', '.m4a', '.mp4', '.wav', '.ogg')):
                        file_type = 'audio'
                    elif clean_url.lower().endswith(('.pdf', '.doc', '.docx', '.txt')):
                        file_type = 'document'
                    else:
                        file_type = 'document'

                if file_type == 'audio':
                    file_ext = '.m4a'
                else:
                    file_ext = os.path.splitext(clean_url)[1] or '.pdf'


                # Generate filename for temporary storage
                user_id = update.effective_user.id
                if custom_filename:
                    # Ensure the filename has the correct extension
                    if not custom_filename.lower().endswith(file_ext):
                        custom_filename += file_ext
                    temp_file_path = FileHandler.TEMP_DIR / custom_filename
                else:
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
                        filename=custom_filename,
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
    """Main bot class handling commands and menu navigation."""
    
    def __init__(self):
        """Initialize the TelegramBot with a BotManager instance."""
        self.bot_manager = BotManager()

    async def start(self, update: Update, context: CallbackContext) -> int:
        """Handle the /start command."""
        user_id = update.effective_user.id

        # Reset user state to root menu
        user_states[user_id] = []

        menu_structure = self.bot_manager.load_menu_structure()

        welcome_message = (
            "🌟 مرحباً بكم في بوت حلقات المعلمة ولاء\n\n"
            "يمكنك اختيار القسم المطلوب من القائمة أدناه"
        ).encode('utf-8').decode('utf-8')

        await update.message.reply_text(
            welcome_message,
            reply_markup=self.bot_manager.get_keyboard_for_menu(menu_structure),
            parse_mode='HTML'
        )

        return NAVIGATING_MENU

    async def handle_menu_navigation(self, update: Update, context: CallbackContext) -> int:
        """Handle menu navigation and file sending."""
        try:
            user_id = update.effective_user.id
            text = update.message.text.encode('utf-8').decode('utf-8')  # Ensure proper encoding

            # Ensure user states exist
            if user_id not in user_states:
                user_states[user_id] = []

            # If main menu button is pressed, reset to root menu
            if text == "🏠 القائمة الرئيسية":
                user_states[user_id] = []
                menu_structure = self.bot_manager.load_menu_structure()

                await update.message.reply_text(
                    "🌟 تم العودة إلى القائمة الرئيسية\nيمكنك اختيار القسم المطلوب من القائمة أدناه",
                    reply_markup=self.bot_manager.get_keyboard_for_menu(menu_structure),
                    parse_mode='HTML'
                )
                return NAVIGATING_MENU

            current_path = user_states[user_id]
            menu_structure = self.bot_manager.load_menu_structure()

            if text == "🔙 رجوع":
                if current_path:
                    current_path.pop()
            else:
                current_menu = self.bot_manager.get_menu_item(menu_structure, current_path)

                if current_menu and text in current_menu:
                    next_level = current_menu[text]
                    file_sent = False

                    # Handle multiple files
                    if 'file_ids' in next_level:
                        files = next_level.get('file_ids', [])
                        for file_metadata in files:
                            if not isinstance(file_metadata, dict):
                                logging.error(f"Invalid file metadata format: {file_metadata}")
                                continue
                                
                            file_url = file_metadata.get('file_id')
                            if not file_url or not isinstance(file_url, str):
                                logging.error("Invalid or missing file_id in metadata")
                                continue

                            file_type = file_metadata.get('type')
                            custom_filename = file_metadata.get('filename')
                            success = await FileHandler.send_file(
                                update=update,
                                context=context,
                                file_url=file_url,
                                description='',
                                file_type=file_type,
                                custom_filename=custom_filename
                            )
                            if success:
                                file_sent = True

                    # Handle external links
                    if 'external_links' in next_level:
                        links = next_level.get('external_links', [])
                        if links:
                            links_text = "<b>📂 تدريبات:</b>\n\n"
                            for (i,link_data) in enumerate(links):
                                if not isinstance(link_data, dict):
                                    continue

                                link_url = link_data.get('url')
                                link_name = link_data.get('name', f'تدريب {i+1}')

                                if link_url and isinstance(link_url, str):
                                    links_text += f"🔗 <a href='{link_url}'>{link_name}</a>\n"

                            if links_text:
                                await update.message.reply_text(
                                    links_text,
                                    parse_mode='HTML',
                                    disable_web_page_preview=True
                                )                    
                    
                    # If it's a submenu, navigate into it
                    if isinstance(next_level, dict) and any(
                        isinstance(val, dict) for val in next_level.values()
                    ):
                        current_path.append(text)

                    # Don't send menu name if a file was sent
                    if file_sent:
                        return NAVIGATING_MENU

            # Get the current menu level after navigation
            current_menu = self.bot_manager.get_menu_item(menu_structure, current_path)

            if current_menu:
                menu_name = current_path[-1] if current_path else 'الرئيسية'
                menu_name = menu_name.encode('utf-8').decode('utf-8')  # Ensure proper encoding
                await update.message.reply_text(
                    f"قائمة {menu_name}:",
                    reply_markup=self.bot_manager.get_keyboard_for_menu(current_menu)
                )
            else:
                await update.message.reply_text("الرجاء اختيار خيار صحيح.")

            return NAVIGATING_MENU

        except Exception as e:
            logging.error(f"Error in handle_menu_navigation: {e}")
            await update.message.reply_text("حدث خطأ. الرجاء المحاولة مرة أخرى.")
            return NAVIGATING_MENU
        
    async def return_to_main_menu(self, update: Update, context: CallbackContext) -> int:
        """Handle the return to main menu command."""
        user_id = update.effective_user.id

        # Reset user state to root menu
        user_states[user_id] = []

        menu_structure = self.bot_manager.load_menu_structure()

        welcome_message = (
            "🌟 تم العودة إلى القائمة الرئيسية\n"
            "يمكنك اختيار القسم المطلوب من القائمة أدناه"
        )

        await update.message.reply_text(
            welcome_message,
            reply_markup=self.bot_manager.get_keyboard_for_menu(menu_structure),
            parse_mode='HTML'
        )

        return NAVIGATING_MENU

    async def reload_menu_command(self, update: Update, context: CallbackContext) -> None:
        """Reload menu structure from Google Sheets (admin only)."""
        user_id = update.effective_user.id

        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ أنت غير مصرح لك باستخدام هذا الأمر.")
            return

        try:
            self.bot_manager.load_menu_structure(force_reload=True)
            await update.message.reply_text("✅ تم تحديث القائمة بنجاح.")
            logger.info(f"Menu structure reloaded by admin: {user_id}")
        except Exception as e:
            logger.error(f"Error reloading menu: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء تحديث القائمة. حاول لاحقاً.")

def main() -> None:
    """Start the bot."""
    # Periodic cleanup of temporary files
    FileHandler.cleanup_temp_files()

    # Create the Application and pass it your bot's token
    application = Application.builder().token(TOKEN).build()

    # Create bot instance
    bot = TelegramBot()

    # Set up command and message handlers
    application.add_handler(CommandHandler('start', bot.start))
    application.add_handler(CommandHandler('main_menu', bot.return_to_main_menu))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, bot.handle_menu_navigation))
    application.add_handler(CommandHandler('reload_menu', bot.reload_menu_command))
    
    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()



