-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Menus Table
CREATE TABLE menus (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    parent_id UUID REFERENCES menus(id),
    order_index INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Menu Items Table
CREATE TABLE menu_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    menu_id UUID REFERENCES menus(id),
    title TEXT NOT NULL,
    type TEXT CHECK(type IN ('file', 'link', 'submenu', 'quiz')),
    content JSONB,  -- Flexible storage for different item types
    file_url TEXT,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    order_index INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);

-- Quizzes Table
CREATE TABLE quizzes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question TEXT NOT NULL,
    options JSONB NOT NULL,
    correct_option INTEGER NOT NULL,
    difficulty TEXT CHECK(difficulty IN ('easy', 'medium', 'hard')),
    category TEXT,
    language TEXT DEFAULT 'ar',  -- Added language support
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sample Initial Data
-- Insert a root menu
INSERT INTO menus (name) VALUES ('Main Menu');

-- Insert some sample menu items
INSERT INTO menu_items (
    menu_id, 
    title, 
    type, 
    content, 
    file_url, 
    description
) VALUES (
    (SELECT id FROM menus WHERE name = 'Main Menu'),
    '📝 اختبر معلوماتك',
    'quiz',
    NULL,
    NULL,
    'اختبار المعلومات العامة'
);

-- Insert some sample quiz questions
INSERT INTO quizzes (
    question, 
    options, 
    correct_option, 
    difficulty, 
    category
) VALUES 
(
    'ما هي عاصمة فرنسا؟',
    '["لندن", "برلين", "باريس", "مدريد"]',
    2,
    'easy',
    'geography'
),
(
    'كم عدد كواكب المجموعة الشمسية؟',
    '["7", "8", "9", "6"]',
    1,
    'medium',
    'science'
);

-- Create function to automatically update timestamps
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for menus table
CREATE TRIGGER update_menus_modtime
BEFORE UPDATE ON menus
FOR EACH ROW
EXECUTE FUNCTION update_modified_column();

-- Trigger for menu_items table
CREATE TRIGGER update_menu_items_modtime
BEFORE UPDATE ON menu_items
FOR EACH ROW
EXECUTE FUNCTION update_modified_column();

-- Trigger for quizzes table
CREATE TRIGGER update_quizzes_modtime
BEFORE UPDATE ON quizzes
FOR EACH ROW
EXECUTE FUNCTION update_modified_column();