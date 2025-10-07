from supabase import create_client, Client
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_STORAGE_BUCKET = os.getenv('SUPABASE_STORAGE_BUCKET', 'reportes-fotos')

def initialize_supabase() -> Client:
    """Inicializar cliente de Supabase"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL y SUPABASE_KEY deben estar configurados en .env")
    
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase

supabase = initialize_supabase()