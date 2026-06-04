import sqlite3


def setup_database():
    conn = sqlite3.connect('truckhelper.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            telegram_username TEXT,
            first_name TEXT,
            last_name TEXT,
            language_code TEXT,
            registration_date TEXT,
            last_active TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS driving_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            start_time TEXT,
            end_time TEXT,
            duration INTEGER,
            session_type TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            daily_driving_minutes INTEGER DEFAULT 0,
            daily_rest_minutes INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weekly_stats (
            stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            week_start_date TEXT,
            weekly_driving_minutes INTEGER DEFAULT 0,
            extended_driving_count INTEGER DEFAULT 0,
            reduced_rest_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rest_sessions (
            rest_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            start_time TEXT,
            end_time TEXT,
            duration_minutes INTEGER,
            rest_type TEXT,
            week_start_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    conn.commit()
    conn.close()


if __name__ == '__main__':
    setup_database()
    print("Database setup complete.")
