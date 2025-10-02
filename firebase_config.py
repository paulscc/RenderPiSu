"""
Firebase Configuration Module
Inicializa Firebase Admin SDK con credenciales
"""

import firebase_admin
from firebase_admin import credentials, firestore, storage
import os
import json
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Variable global para verificar si ya se inicializó
_initialized = False
_db = None
_bucket = None


def initialize_firebase():
    """
    Inicializa Firebase Admin SDK
    
    Soporta dos métodos de autenticación:
    1. Archivo JSON de credenciales (desarrollo local)
    2. Variables de entorno (producción en Render)
    
    Returns:
        tuple: (db, bucket) - Cliente de Firestore y bucket de Storage
    """
    global _initialized, _db, _bucket
    
    # Si ya se inicializó, retornar las instancias existentes
    if _initialized:
        return _db, _bucket
    
    try:
        # ============================================
        # MÉTODO 1: Usar archivo JSON (Desarrollo Local)
        # ============================================
        if os.path.exists('serviceAccountKey.json'):
            print("🔑 Inicializando Firebase con archivo JSON...")
            cred = credentials.Certificate('serviceAccountKey.json')
            
            # Leer el storage bucket del archivo JSON
            with open('serviceAccountKey.json', 'r') as f:
                service_account = json.load(f)
                project_id = service_account.get('project_id')
                storage_bucket = f"{project_id}.firebasestorage.app"
        
        # ============================================
        # MÉTODO 2: Usar variables de entorno (Producción)
        # ============================================
        else:
            print("🔑 Inicializando Firebase con variables de entorno...")
            
            # Obtener credenciales de variables de entorno
            project_id = os.getenv('FIREBASE_PROJECT_ID')
            private_key = os.getenv('FIREBASE_PRIVATE_KEY')
            client_email = os.getenv('FIREBASE_CLIENT_EMAIL')
            
            # Validar que existan las variables
            if not all([project_id, private_key, client_email]):
                raise ValueError(
                    "❌ Faltan variables de entorno de Firebase.\n"
                    "Necesitas: FIREBASE_PROJECT_ID, FIREBASE_PRIVATE_KEY, FIREBASE_CLIENT_EMAIL"
                )
            
            # Reemplazar literalmente \n por saltos de línea reales
            # Esto es importante porque Render puede escapar los saltos de línea
            private_key = private_key.replace('\\n', '\n')
            
            # Crear diccionario de credenciales
            cred_dict = {
                "type": "service_account",
                "project_id": project_id,
                "private_key": private_key,
                "client_email": client_email,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            
            cred = credentials.Certificate(cred_dict)
            storage_bucket = os.getenv('STORAGE_BUCKET', f"{project_id}.firebasestorage.app")
        
        # ============================================
        # Inicializar Firebase App
        # ============================================
        firebase_admin.initialize_app(cred, {
            'storageBucket': storage_bucket
        })
        
        # Obtener instancias de Firestore y Storage
        _db = firestore.client()
        _bucket = storage.bucket()
        
        _initialized = True
        
        print(f"✅ Firebase inicializado correctamente")
        print(f"📦 Storage Bucket: {storage_bucket}")
        
        return _db, _bucket
        
    except Exception as e:
        print(f"❌ Error al inicializar Firebase: {str(e)}")
        raise


def get_db():
    """
    Obtiene la instancia del cliente de Firestore
    
    Returns:
        firestore.Client: Cliente de Firestore
    """
    global _db
    if _db is None:
        initialize_firebase()
    return _db


def get_bucket():
    """
    Obtiene la instancia del bucket de Storage
    
    Returns:
        storage.Bucket: Bucket de Cloud Storage
    """
    global _bucket
    if _bucket is None:
        initialize_firebase()
    return _bucket


# ============================================
# FUNCIONES AUXILIARES ÚTILES
# ============================================

def check_firebase_connection():
    """
    Verifica que la conexión con Firebase esté funcionando
    
    Returns:
        bool: True si la conexión es exitosa
    """
    try:
        db = get_db()
        # Intentar leer una colección (no importa si está vacía)
        db.collection('_health_check').limit(1).get()
        print("✅ Conexión con Firestore OK")
        return True
    except Exception as e:
        print(f"❌ Error en conexión con Firestore: {str(e)}")
        return False


def test_storage_connection():
    """
    Verifica que la conexión con Storage esté funcionando
    
    Returns:
        bool: True si la conexión es exitosa
    """
    try:
        bucket = get_bucket()
        # Verificar que el bucket existe
        bucket.exists()
        print("✅ Conexión con Storage OK")
        return True
    except Exception as e:
        print(f"❌ Error en conexión con Storage: {str(e)}")
        return False


# ============================================
# TESTING (ejecutar solo si se corre directamente)
# ============================================
if __name__ == "__main__":
    print("🧪 Probando configuración de Firebase...\n")
    
    try:
        # Inicializar
        db, bucket = initialize_firebase()
        
        # Probar conexiones
        print("\n📡 Probando conexiones...")
        firestore_ok = check_firebase_connection()
        storage_ok = test_storage_connection()
        
        if firestore_ok and storage_ok:
            print("\n✅ ¡Todas las conexiones funcionan correctamente!")
            
            # Mostrar información del proyecto
            print("\n📊 Información del proyecto:")
            print(f"   - Storage Bucket: {bucket.name}")
            
        else:
            print("\n⚠️ Hay problemas con algunas conexiones")
            
    except Exception as e:
        print(f"\n❌ Error durante las pruebas: {str(e)}")
        print("\n💡 Sugerencias:")
        print("   1. Verifica que exista 'serviceAccountKey.json' o las variables de entorno")
        print("   2. Verifica que las credenciales sean correctas")
        print("   3. Verifica que Firestore y Storage estén habilitados en Firebase Console")