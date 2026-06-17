import sqlite3
from datetime import datetime
    # Журнал смен
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS driver_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            shift_duration REAL DEFAULT 0,
            rest_hours REAL DEFAULT 0,
            driving_hours REAL DEFAULT 0,
            used_10th_hour INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date)
        )
    ''')
def init_db():
    conn = sqlite3.connect('driver_shifts.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS driver_shifts (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        date TEXT UNIQUE,
        shift_duration REAL,
        rest_hours REAL,
        driving_hours REAL,
        used_10th_hour INTEGER DEFAULT 0,
        notes TEXT
    )''')
    conn.commit()
    conn.close()
    print("Database initialized")

if __name__ == "__main__":
    init_db()
