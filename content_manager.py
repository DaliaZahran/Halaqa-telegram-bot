import os
from typing import List, Dict, Any, Optional
from supabase import create_client, Client
from dotenv import load_dotenv
import json
import random
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ContentManager:
    _instance = None
    
    def __new__(cls):
        if not cls._instance:
            cls._instance = super(ContentManager, cls).__new__(cls)
            # Load environment variables
            load_dotenv()
            
            # Initialize Supabase client
            try:
                cls._instance.url = os.getenv('SUPABASE_URL')
                cls._instance.key = os.getenv('SUPABASE_KEY')
                cls._instance.supabase: Client = create_client(cls._instance.url, cls._instance.key)
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
                raise
        return cls._instance

    def get_menu_structure(self, parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieve menu structure, optionally filtered by parent
        
        :param parent_id: Optional UUID of parent menu to filter
        :return: List of menu items
        """
        try:
            query = self.supabase.table('menu_items')
            
            if parent_id:
                query = query.filter('menu_id', 'eq', parent_id)
            
            response = query.select('*').execute()
            
            return response.data
        except Exception as e:
            logger.error(f"Error retrieving menu structure: {e}")
            return []

    def get_quiz_questions(
        self, 
        difficulty: Optional[str] = None, 
        category: Optional[str] = None,
        language: str = 'ar',
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Retrieve quiz questions with optional filtering
        
        :param difficulty: Difficulty level (easy/medium/hard)
        :param category: Question category
        :param language: Language of questions
        :param limit: Maximum number of questions to retrieve
        :return: List of quiz questions
        """
        try:
            query = self.supabase.table('quizzes')
            
            # Apply optional filters
            if difficulty:
                query = query.filter('difficulty', 'eq', difficulty)
            
            if category:
                query = query.filter('category', 'eq', category)
            
            query = query.filter('language', 'eq', language)
            
            # Execute query with limit
            response = query.select('*').limit(limit).execute()
            
            return response.data
        except Exception as e:
            logger.error(f"Error retrieving quiz questions: {e}")
            return []

    def get_random_quiz_question(
        self, 
        difficulty: Optional[str] = None, 
        category: Optional[str] = None,
        language: str = 'ar'
    ) -> Optional[Dict[str, Any]]:
        """
        Get a random quiz question
        
        :param difficulty: Difficulty level (easy/medium/hard)
        :param category: Question category
        :param language: Language of questions
        :return: A single random quiz question or None
        """
        questions = self.get_quiz_questions(
            difficulty=difficulty, 
            category=category, 
            language=language
        )
        
        return random.choice(questions) if questions else None

    def add_quiz_question(
        self, 
        question: str, 
        options: List[str], 
        correct_option: int,
        difficulty: str = 'medium',
        category: Optional[str] = None,
        language: str = 'ar'
    ) -> Dict[str, Any]:
        """
        Add a new quiz question
        
        :param question: The quiz question text
        :param options: List of answer options
        :param correct_option: Index of the correct answer
        :param difficulty: Difficulty level
        :param category: Question category
        :param language: Language of the question
        :return: Created quiz question details
        """
        try:
            data = {
                'question': question,
                'options': json.dumps(options),
                'correct_option': correct_option,
                'difficulty': difficulty,
                'category': category,
                'language': language
            }
            
            response = self.supabase.table('quizzes').insert(data).execute()
            
            return response.data[0] if response.data else {}
        except Exception as e:
            logger.error(f"Error adding quiz question: {e}")
            return {}

    def update_menu_structure(self, menu_items: List[Dict[str, Any]]) -> bool:
        """
        Bulk update or insert menu items
        
        :param menu_items: List of menu items to update/insert
        :return: True if successful, False otherwise
        """
        try:
            # Upsert menu items (insert or update if exists)
            response = self.supabase.table('menu_items').upsert(menu_items).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Error updating menu structure: {e}")
            return False

# Example usage
def main():
    # Initialize content manager
    content_manager = ContentManager()
    
    # Get all quiz questions
    questions = content_manager.get_quiz_questions()
    print("All Quiz Questions:", questions)
    
    # Get a random quiz question
    random_question = content_manager.get_random_quiz_question(difficulty='easy')
    print("Random Question:", random_question)
    
    # Add a new quiz question
    new_question = content_manager.add_quiz_question(
        question='ما هي أكبر دولة عربية؟',
        options=['مصر', 'السودان', 'الجزائر', 'السعودية'],
        correct_option=3,
        difficulty='medium',
        category='geography'
    )
    print("New Question Added:", new_question)

if __name__ == '__main__':
    main()