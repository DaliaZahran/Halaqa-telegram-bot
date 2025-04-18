import os
from typing import List, Union, Dict, Optional, Any

from telegram import Update, Poll, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

import logging
import json
from pathlib import Path
from dotenv import load_dotenv

# Import the new content manager
from content_manager import ContentManager

# Load environment variables from .env file
load_dotenv()

# Get tokens and keys from environment variables
TOKEN = os.getenv('TELEGRAM_TOKEN')

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States and Constants
NAVIGATING_MENU = range(1)
CACHE_DIR = Path(