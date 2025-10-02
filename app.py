# ============================================
# MINGAFIX API - Flask + Firebase (CORREGIDA)
# ============================================

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import uuid
from firebase_config import initialize_firebase

# IMPORTACIÓN REQUERIDA: Importa firestore desde firebase_admin
from firebase_admin import firestore



app = Flask(__name__)
CORS(app)

# Inicializar Firebase
db, bucket = initialize_firebase()

# ============================================
# ENDPOINTS
# ============================================

@app.route('/', methods=['GET'])
def home():
    """Endpoint de información de la API"""
    return jsonify({
        'message': 'MINGAFIX API v1.0 - Python',
        'status': 'online',
        'endpoints': {
            'POST /reportes': 'Crear nuevo reporte',
            'GET /reportes': 'Obtener todos los reportes',
            'GET /reportes/<id>': 'Obtener reporte por ID',
            'PATCH /reportes/<id>': 'Actualizar estado del reporte'
        }
    }), 200


@app.route('/reportes', methods=['POST'])
def crear_reporte():
    """
    Crear un nuevo reporte ciudadano
    
    ACEPTA DOS FORMATOS:
    
    1. JSON (sin foto):
    {
        "categoria": "bache",
        "lat": -0.8131,
        "lng": -77.7172,
        "descripcion": "Descripción",
        "fotoUrl": "https://ejemplo.com/foto.jpg"
    }
    
    2. Form-data (con foto):
    - categoria: bache
    - lat: -0.8131
    - lng: -77.7172
    - descripcion: Descripción
    - foto: [archivo]
    """
    try:
        # ===== DETECTAR TIPO DE CONTENIDO =====
        content_type = request.content_type
        
        # Si es JSON
        if content_type and 'application/json' in content_type:
            data = request.get_json()
            categoria = data.get('categoria')
            lat = data.get('lat')
            lng = data.get('lng')
            descripcion = data.get('descripcion', '')
            foto_url = data.get('fotoUrl')
            foto_file = None
            
        # Si es form-data o multipart
        else:
            categoria = request.form.get('categoria')
            lat = request.form.get('lat')
            lng = request.form.get('lng')
            descripcion = request.form.get('descripcion', '')
            foto_url = request.form.get('fotoUrl')
            foto_file = request.files.get('foto')
        
        # ===== VALIDAR DATOS REQUERIDOS =====
        if not categoria or not lat or not lng:
            return jsonify({
                'error': 'Faltan datos requeridos',
                'required': ['categoria', 'lat', 'lng'],
                'received': {
                    'categoria': categoria,
                    'lat': lat,
                    'lng': lng
                }
            }), 400
        
        # Validar coordenadas
        try:
            lat_float = float(lat)
            lng_float = float(lng)
            
            if not (-90 <= lat_float <= 90) or not (-180 <= lng_float <= 180):
                return jsonify({
                    'error': 'Coordenadas fuera de rango',
                    'details': 'lat debe estar entre -90 y 90, lng entre -180 y 180'
                }), 400
        except ValueError:
            return jsonify({
                'error': 'Coordenadas inválidas',
                'details': 'lat y lng deben ser números'
            }), 400
        
        # ===== SUBIR FOTO SI EXISTE =====
        if foto_file and foto_file.filename:
            try:
                # Validar tipo de archivo
                allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
                extension = foto_file.filename.rsplit('.', 1)[1].lower()
                
                if extension not in allowed_extensions:
                    return jsonify({
                        'error': 'Tipo de archivo no permitido',
                        'allowed': list(allowed_extensions)
                    }), 400
                
                # Generar nombre único
                nombre_archivo = f"reportes/{uuid.uuid4()}.{extension}"
                
                # Subir a Firebase Storage
                blob = bucket.blob(nombre_archivo)
                blob.upload_from_string(
                    foto_file.read(),
                    content_type=foto_file.content_type
                )
                blob.make_public()
                
                # Obtener URL pública
                foto_url = blob.public_url
                
            except Exception as e:
                return jsonify({
                    'error': 'Error al subir foto',
                    'details': str(e)
                }), 500
        
        # ===== CREAR DOCUMENTO EN FIRESTORE =====
        created_at = datetime.utcnow().isoformat()
        
        reporte_data = {
            'fotoUrl': foto_url,
            'categoria': categoria,
            'ubicacion': {
                'lat': lat_float,
                'lng': lng_float
            },
            'descripcion': descripcion,
            'estado': 'pendiente',
            'timestamp': firestore.SERVER_TIMESTAMP,
            'createdAt': created_at
        }
        
        # Guardar en Firestore
        doc_ref = db.collection('reportes').add(reporte_data)
        reporte_id = doc_ref[1].id
        
        # ===== RESPUESTA =====
        response_data = {
            'id': reporte_id,
            'fotoUrl': foto_url,
            'categoria': categoria,
            'ubicacion': {
                'lat': lat_float,
                'lng': lng_float
            },
            'descripcion': descripcion,
            'estado': 'pendiente',
            'createdAt': created_at
        }
        
        return jsonify({
            'success': True,
            'message': 'Reporte creado exitosamente',
            'id': reporte_id,
            'data': response_data
        }), 201
        
    except Exception as e:
        print(f"Error inesperado: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': 'Error interno del servidor',
            'details': str(e)
        }), 500


@app.route('/reportes', methods=['GET'])
def obtener_reportes():
    """
    Obtener todos los reportes
    
    Query params opcionales:
    - limit (int): número máximo de resultados (default: 50)
    - categoria (string): filtrar por categoría
    - estado (string): filtrar por estado
    """
    try:
        # Parámetros de consulta
        limit = int(request.args.get('limit', 50))
        categoria = request.args.get('categoria')
        estado = request.args.get('estado')
        
        # Crear query
        query = db.collection('reportes')
        
        # Filtrar por categoría
        if categoria:
            query = query.where('categoria', '==', categoria)
        
        # Filtrar por estado
        if estado:
            query = query.where('estado', '==', estado)
        
        # Ordenar y limitar
        query = query.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
        
        # Ejecutar query
        docs = query.stream()
        
        # Convertir a lista
        reportes = []
        for doc in docs:
            reporte = doc.to_dict()
            reporte['id'] = doc.id
            
            # Convertir timestamp a string si existe
            if 'timestamp' in reporte and reporte['timestamp']:
                reporte['timestamp'] = reporte['timestamp'].isoformat()
            
            reportes.append(reporte)
        
        return jsonify({
            'success': True,
            'count': len(reportes),
            'data': reportes
        }), 200
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            'error': 'Error al obtener reportes',
            'details': str(e)
        }), 500


@app.route('/reportes/<reporte_id>', methods=['GET'])
def obtener_reporte(reporte_id):
    """Obtener un reporte específico por ID"""
    try:
        doc = db.collection('reportes').document(reporte_id).get()
        
        if not doc.exists:
            return jsonify({
                'error': 'Reporte no encontrado'
            }), 404
        
        reporte = doc.to_dict()
        reporte['id'] = doc.id
        
        # Convertir timestamp a string
        if 'timestamp' in reporte and reporte['timestamp']:
            reporte['timestamp'] = reporte['timestamp'].isoformat()
        
        return jsonify({
            'success': True,
            'data': reporte
        }), 200
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            'error': 'Error al obtener reporte',
            'details': str(e)
        }), 500


@app.route('/reportes/<reporte_id>', methods=['PATCH'])
def actualizar_reporte(reporte_id):
    """
    Actualizar el estado de un reporte
    
    Body (JSON):
    {
        "estado": "en_proceso"  // pendiente, en_proceso, resuelto
    }
    """
    try:
        # Verificar que sea JSON
        if not request.is_json:
            return jsonify({
                'error': 'Content-Type debe ser application/json'
            }), 415
        
        data = request.get_json()
        estado = data.get('estado')
        
        # Validar estado
        estados_validos = ['pendiente', 'en_proceso', 'resuelto']
        if not estado or estado not in estados_validos:
            return jsonify({
                'error': 'Estado inválido',
                'allowed': estados_validos
            }), 400
        
        # Verificar que el documento existe
        doc_ref = db.collection('reportes').document(reporte_id)
        if not doc_ref.get().exists:
            return jsonify({
                'error': 'Reporte no encontrado'
            }), 404
        
        # Actualizar documento
        doc_ref.update({
            'estado': estado,
            'updatedAt': datetime.utcnow().isoformat()
        })
        
        return jsonify({
            'success': True,
            'message': 'Reporte actualizado exitosamente',
            'id': reporte_id,
            'nuevo_estado': estado
        }), 200
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            'error': 'Error al actualizar reporte',
            'details': str(e)
        }), 500


@app.route('/reportes/<reporte_id>', methods=['DELETE'])
def eliminar_reporte(reporte_id):
    """Eliminar un reporte (solo para desarrollo/testing)"""
    try:
        doc_ref = db.collection('reportes').document(reporte_id)
        
        if not doc_ref.get().exists:
            return jsonify({
                'error': 'Reporte no encontrado'
            }), 404
        
        doc_ref.delete()
        
        return jsonify({
            'success': True,
            'message': 'Reporte eliminado exitosamente'
        }), 200
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            'error': 'Error al eliminar reporte',
            'details': str(e)
        }), 500


# ============================================
# MANEJO DE ERRORES
# ============================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'error': 'Endpoint no encontrado',
        'message': 'Verifica la URL y el método HTTP'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'error': 'Error interno del servidor',
        'message': str(error)
    }), 500


# ============================================
# EJECUTAR APP
# ============================================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)