import os

import pyodbc
from dotenv import load_dotenv

load_dotenv()

conn_str = (
    f"DRIVER={{{os.getenv('SQL_DRIVER')}}};"
    f"SERVER={os.getenv('SQL_SERVER')},{os.getenv('SQL_PORT')};"
    f"DATABASE={os.getenv('SQL_DATABASE')};"
    f"UID={os.getenv('SQL_USERNAME')};"
    f"PWD={os.getenv('SQL_PASSWORD')};"
    "Encrypt=no;"
    "TrustServerCertificate=yes;"
)

print("Connecting...")

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

cursor.execute("SELECT DB_NAME()")
print("Connected to:", cursor.fetchone()[0])

cursor.execute("SELECT @@VERSION")
print(cursor.fetchone()[0])

conn.close()
