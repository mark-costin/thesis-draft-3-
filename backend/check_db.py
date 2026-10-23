import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    print("connected as:", conn.execute(text("SELECT current_database(), current_user")).one())
    print("tables:", conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar())
    print("patients visible:", conn.execute(text("SELECT count(*) FROM patients")).scalar())
