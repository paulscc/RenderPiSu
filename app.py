

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import uuid
from firebase_config import initialize_firebase
from functools import wraps
from ariadne import QueryType, MutationType, make_executable_schema, graphql_sync
from ariadne.explorer.playground import PLAYGROUND_HTML  # ✅ nuevo lugar
from firebase_admin import firestore

app = Flask(__name__)
CORS(app)

db, bucket = initialize_firebase()

request_tracker = {}

def limpiar_tracker():
    
    now = time.time()
    to_delete = []
    for key, data in request_tracker.items():
        if now - data['first_request'] > 3600:  # 1 hora
            to_delete.append(key)
    for key in to_delete:
        del request_tracker[key]

def rate_limit(max_requests=10, time_window=60):
   
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
        
            if len(request_tracker) > 1000:
                limpiar_tracker()
            

            user_id = request.headers.get('X-User-ID')
            

            if not user_id:
       
                if request.form:
                    user_id = request.form.get('usuario_id') or request.form.get('userId')
             
                elif request.is_json:
                    try:
                        data = request.get_json()
                        user_id = data.get('usuario_id') or data.get('userId')
                    except:
                        pass
            
            if not user_id:
                return jsonify({
                    'error': 'Se requiere usuario_id o userId en el body, o X-User-ID en headers',
                    'code': 'USER_ID_REQUIRED'
                }), 401
            
     
            endpoint = request.endpoint or 'unknown'
            key = f"{user_id}:{endpoint}"
            now = time.time()
        
            if key not in request_tracker:
                request_tracker[key] = {
                    'count': 0,
                    'first_request': now,
                    'last_request': now
                }
            
            tracker = request_tracker[key]
            
       
            if now - tracker['first_request'] > time_window:
        
                tracker['count'] = 0
                tracker['first_request'] = now
            
        
            tracker['count'] += 1
            tracker['last_request'] = now
            
         
            if tracker['count'] > max_requests:
                tiempo_restante = int(time_window - (now - tracker['first_request']))
                return jsonify({
                    'error': f'Demasiadas peticiones. Máximo {max_requests} por minuto',
                    'code': 'RATE_LIMIT_EXCEEDED',
                    'retry_after': tiempo_restante,
                    'requests_made': tracker['count']
                }), 429
            
       
            response = f(*args, **kwargs)
            
  
            if isinstance(response, tuple):
                if len(response) == 3:
                    json_response, status_code, existing_headers = response
                    headers = dict(existing_headers) if existing_headers else {}
                elif len(response) == 2:
                    json_response, status_code = response
                    headers = {}
                else:
                    json_response = response[0]
                    status_code = 200
                    headers = {}
            else:
                json_response = response
                status_code = 200
                headers = {}
            
        
            headers.update({
                'X-RateLimit-Limit': str(max_requests),
                'X-RateLimit-Remaining': str(max(0, max_requests - tracker['count'])),
                'X-RateLimit-Reset': str(int(tracker['first_request'] + time_window))
            })
            
            return json_response, status_code, headers
        
        return wrapped
    return decorator

def verificar_reporte_duplicado(usuario_id, categoria, lat, lng, time_window=300):
    
    try:
        cutoff_time = datetime.utcnow() - timedelta(seconds=time_window)
        
        reportes = db.collection('reportes')\
            .where('usuario_id', '==', usuario_id)\
            .where('categoria', '==', categoria)\
            .where('timestamp', '>', cutoff_time)\
            .limit(10)\
            .stream()
        
       
        for doc in reportes:
            data = doc.to_dict()
            ubicacion = data.get('ubicacion', {})
            
         
            lat_diff = abs(ubicacion.get('lat', 0) - lat)
            lng_diff = abs(ubicacion.get('lng', 0) - lng)
            
            if lat_diff < 0.001 and lng_diff < 0.001:
                return True, {
                    'id': doc.id,
                    **data
                }
        
        return False, None
        
    except Exception as e:
        print(f"Error en verificar_reporte_duplicado: {str(e)}")
        return False, None

type_defs = """
    type Query {
        reportes(limit: Int, categoria: String, estado: String, usuario_id: String): [Reporte!]!
        reporte(id: ID!): Reporte
        estadisticas: Estadisticas!
        misReportes(usuario_id: String!): [Reporte!]!
    }
    
    type Mutation {
        crearReporte(input: ReporteInput!): ReporteResponse!
        actualizarEstado(id: ID!, estado: String!, usuario_id: String!): ReporteResponse!
    }
    
    type Reporte {
        id: ID!
        categoria: String!
        ubicacion: Ubicacion!
        descripcion: String
        estado: String!
        usuario_id: String!
        createdAt: String!
        updatedAt: String
        version: Int!
    }
    
    type Ubicacion {
        lat: Float!
        lng: Float!
    }
    
    input ReporteInput {
        categoria: String!
        lat: Float!
        lng: Float!
        descripcion: String
        fotoUrl: String
        usuario_id: String!
    }
    
    type ReporteResponse {
        success: Boolean!
        message: String!
        reporte: Reporte
        code: String
    }
    
    type Estadisticas {
        total: Int!
        pendientes: Int!
        en_proceso: Int!
        resueltos: Int!
        por_categoria: [CategoriaStats!]!
        por_usuario: [UsuarioStats!]!
    }
    
    type CategoriaStats {
        categoria: String!
        cantidad: Int!
    }
    
    type UsuarioStats {
        usuario_id: String!
        cantidad: Int!
    }
"""

query = QueryType()
mutation = MutationType()

@query.field("reportes")
def resolve_reportes(_, info, limit=50, categoria=None, estado=None, usuario_id=None):
    
    try:
        query_ref = db.collection('reportes')
        
        if categoria:
            query_ref = query_ref.where('categoria', '==', categoria)
        if estado:
            query_ref = query_ref.where('estado', '==', estado)
        if usuario_id:
            query_ref = query_ref.where('usuario_id', '==', usuario_id)
        
        query_ref = query_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
        docs = query_ref.stream()
        
        reportes = []
        for doc in docs:
            data = doc.to_dict()
            reportes.append({
                'id': doc.id,
                'categoria': data.get('categoria'),
                'ubicacion': {
                    'lat': data.get('ubicacion', {}).get('lat'),
                    'lng': data.get('ubicacion', {}).get('lng')
                },
                'descripcion': data.get('descripcion', ''),
                'estado': data.get('estado', 'pendiente'),
                'usuario_id': data.get('usuario_id', 'anonimo'),
                'createdAt': data.get('createdAt', ''),
                'updatedAt': data.get('updatedAt'),
                'version': data.get('version', 1)
            })
        
        return reportes
    except Exception as e:
        print(f"Error en resolve_reportes: {str(e)}")
        return []

@query.field("misReportes")
def resolve_mis_reportes(_, info, usuario_id):
    """Obtener reportes de un usuario específico"""
    try:
        docs = db.collection('reportes')\
            .where('usuario_id', '==', usuario_id)\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(100)\
            .stream()
        
        reportes = []
        for doc in docs:
            data = doc.to_dict()
            reportes.append({
                'id': doc.id,
                'categoria': data.get('categoria'),
                'ubicacion': {
                    'lat': data.get('ubicacion', {}).get('lat'),
                    'lng': data.get('ubicacion', {}).get('lng')
                },
                'descripcion': data.get('descripcion', ''),
                'estado': data.get('estado', 'pendiente'),
                'usuario_id': data.get('usuario_id'),
                'createdAt': data.get('createdAt', ''),
                'updatedAt': data.get('updatedAt'),
                'version': data.get('version', 1)
            })
        
        return reportes
    except Exception as e:
        print(f"Error en resolve_mis_reportes: {str(e)}")
        return []

@query.field("reporte")
def resolve_reporte(_, info, id):
    
    try:
        doc = db.collection('reportes').document(id).get()
        
        if not doc.exists:
            return None
        
        data = doc.to_dict()
        return {
            'id': doc.id,
            'categoria': data.get('categoria'),
            'ubicacion': {
                'lat': data.get('ubicacion', {}).get('lat'),
                'lng': data.get('ubicacion', {}).get('lng')
            },
            'descripcion': data.get('descripcion', ''),
            'estado': data.get('estado', 'pendiente'),
            'usuario_id': data.get('usuario_id', 'anonimo'),
            'createdAt': data.get('createdAt', ''),
            'updatedAt': data.get('updatedAt'),
            'version': data.get('version', 1)
        }
    except Exception as e:
        print(f"Error en resolve_reporte: {str(e)}")
        return None

@query.field("estadisticas")
def resolve_estadisticas(_, info):
    """Estadísticas con conteo por usuario"""
    try:
        reportes = db.collection('reportes').stream()
        
        total = 0
        pendientes = 0
        en_proceso = 0
        resueltos = 0
        categorias = {}
        usuarios = {}
        
        for doc in reportes:
            data = doc.to_dict()
            total += 1
            
            estado = data.get('estado', 'pendiente')
            if estado == 'pendiente':
                pendientes += 1
            elif estado == 'en_proceso':
                en_proceso += 1
            elif estado == 'resuelto':
                resueltos += 1
            
            categoria = data.get('categoria', 'otro')
            categorias[categoria] = categorias.get(categoria, 0) + 1
            
            usuario_id = data.get('usuario_id', 'anonimo')
            usuarios[usuario_id] = usuarios.get(usuario_id, 0) + 1
        
        por_categoria = [{'categoria': cat, 'cantidad': cant} for cat, cant in categorias.items()]
        por_usuario = [{'usuario_id': uid, 'cantidad': cant} for uid, cant in usuarios.items()]
        
        return {
            'total': total,
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'resueltos': resueltos,
            'por_categoria': por_categoria,
            'por_usuario': sorted(por_usuario, key=lambda x: x['cantidad'], reverse=True)[:10]
        }
    except Exception as e:
        print(f"Error en resolve_estadisticas: {str(e)}")
        return {
            'total': 0,
            'pendientes': 0,
            'en_proceso': 0,
            'resueltos': 0,
            'por_categoria': [],
            'por_usuario': []
        }

@mutation.field("crearReporte")
def resolve_crear_reporte(_, info, input):
    
    try:
        categoria = input.get('categoria')
        lat = input.get('lat')
        lng = input.get('lng')
        descripcion = input.get('descripcion', '')
        foto_url = input.get('fotoUrl')
        usuario_id = input.get('usuario_id')
        
        if not usuario_id:
            return {
                'success': False,
                'message': 'Se requiere usuario_id',
                'reporte': None,
                'code': 'USER_ID_REQUIRED'
            }
        
        if not categoria or lat is None or lng is None:
            return {
                'success': False,
                'message': 'Faltan campos requeridos: categoria, lat, lng',
                'reporte': None,
                'code': 'MISSING_FIELDS'
            }
        
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return {
                'success': False,
                'message': 'Coordenadas fuera de rango',
                'reporte': None,
                'code': 'INVALID_COORDINATES'
            }
        
 
        es_duplicado, reporte_existente = verificar_reporte_duplicado(
            usuario_id, categoria, lat, lng
        )
        
        if es_duplicado:
            return {
                'success': False,
                'message': 'Ya reportaste un incidente similar hace menos de 5 minutos en esta ubicación',
                'reporte': None,
                'code': 'DUPLICATE_REPORT'
            }
        
        # Crear documento
        created_at = datetime.utcnow().isoformat()
        
        reporte_data = {
            'fotoUrl': foto_url,
            'categoria': categoria,
            'ubicacion': {
                'lat': float(lat),
                'lng': float(lng)
            },
            'descripcion': descripcion,
            'estado': 'pendiente',
            'usuario_id': usuario_id,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'createdAt': created_at,
            'version': 1
        }
        
        doc_ref = db.collection('reportes').add(reporte_data)
        reporte_id = doc_ref[1].id
        
        return {
            'success': True,
            'message': 'Reporte creado exitosamente',
            'reporte': {
                'id': reporte_id,
                'categoria': categoria,
                'ubicacion': {'lat': float(lat), 'lng': float(lng)},
                'descripcion': descripcion,
                'estado': 'pendiente',
                'usuario_id': usuario_id,
                'createdAt': created_at,
                'updatedAt': None,
                'version': 1
            },
            'code': 'SUCCESS'
        }
        
    except Exception as e:
        print(f"Error en resolve_crear_reporte: {str(e)}")
        return {
            'success': False,
            'message': f'Error al crear reporte: {str(e)}',
            'reporte': None,
            'code': 'INTERNAL_ERROR'
        }

@mutation.field("actualizarEstado")
def resolve_actualizar_estado(_, info, id, estado, usuario_id):
    
    try:
        estados_validos = ['pendiente', 'en_proceso', 'resuelto']
        if estado not in estados_validos:
            return {
                'success': False,
                'message': f'Estado inválido. Usar: {", ".join(estados_validos)}',
                'reporte': None,
                'code': 'INVALID_STATE'
            }
        
        doc_ref = db.collection('reportes').document(id)
     
        @firestore.transactional
        def actualizar_con_version(transaction, doc_ref):
            snapshot = doc_ref.get(transaction=transaction)
            
            if not snapshot.exists:
                return None, 'NOTFOUND'
            
            data = snapshot.to_dict()
            version_actual = data.get('version', 1)
            
      
            updated_at = datetime.utcnow().isoformat()
            transaction.update(doc_ref, {
                'estado': estado,
                'updatedAt': updated_at,
                'version': version_actual + 1,
                'updated_by': usuario_id
            })
            
            return {
                'id': id,
                'categoria': data.get('categoria'),
                'ubicacion': data.get('ubicacion'),
                'descripcion': data.get('descripcion', ''),
                'estado': estado,
                'usuario_id': data.get('usuario_id'),
                'createdAt': data.get('createdAt', ''),
                'updatedAt': updated_at,
                'version': version_actual + 1
            }, 'SUCCESS'
        
        transaction = db.transaction()
        resultado, code = actualizar_con_version(transaction, doc_ref)
        
        if code == 'NOTFOUND':
            return {
                'success': False,
                'message': 'Reporte no encontrado',
                'reporte': None,
                'code': 'NOT_FOUND'
            }
        
        return {
            'success': True,
            'message': 'Estado actualizado exitosamente',
            'reporte': resultado,
            'code': 'SUCCESS'
        }
        
    except Exception as e:
        print(f"Error en resolve_actualizar_estado: {str(e)}")
        return {
            'success': False,
            'message': f'Error al actualizar: {str(e)}',
            'reporte': None,
            'code': 'INTERNAL_ERROR'
        }


schema = make_executable_schema(type_defs, query, mutation)



@app.route('/graphql', methods=['GET'])
def graphql_playground():
    return PLAYGROUND_HTML, 200

@app.route('/graphql', methods=['POST'])
def graphql_server():
    data = request.get_json()
    success, result = graphql_sync(schema, data, context_value=request, debug=app.debug)
    return jsonify(result), 200 if success else 400



@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'MINGAFIX API v2.0 - REST + GraphQL + Rate Limiting',
        'status': 'online',
        'features': {
            'rate_limiting': '30 peticiones por minuto por usuario',
            'duplicate_detection': '5 minutos de ventana',
            'concurrency_control': 'Control de versiones optimista'
        }
    }), 200

@app.route('/reportes', methods=['POST'])
@rate_limit(max_requests=30, time_window=60)
def crear_reporte():
    
    try:
        content_type = request.content_type
        
        if content_type and 'application/json' in content_type:
            data = request.get_json()
            categoria = data.get('categoria')
            lat = data.get('lat')
            lng = data.get('lng')
            descripcion = data.get('descripcion', '')
            foto_url = data.get('fotoUrl')
            
            usuario_id = data.get('usuario_id') or data.get('userId')
            foto_file = None
        else:
            categoria = request.form.get('categoria')
            lat = request.form.get('lat')
            lng = request.form.get('lng')
            descripcion = request.form.get('descripcion', '')
            foto_url = request.form.get('fotoUrl')
            
            usuario_id = request.form.get('usuario_id') or request.form.get('userId')
            foto_file = request.files.get('foto')
        
        if not usuario_id:
            return jsonify({
                'error': 'Se requiere usuario_id',
                'code': 'USER_ID_REQUIRED'
            }), 401
        
        if not categoria or not lat or not lng:
            return jsonify({
                'error': 'Faltan datos requeridos: categoria, lat, lng'
            }), 400
        
        lat_float = float(lat)
        lng_float = float(lng)
        
        
        es_duplicado, _ = verificar_reporte_duplicado(usuario_id, categoria, lat_float, lng_float)
        if es_duplicado:
            return jsonify({
                'error': 'Ya reportaste un incidente similar recientemente',
                'code': 'DUPLICATE_REPORT'
            }), 409
        
        
        if foto_file and foto_file.filename:
            extension = foto_file.filename.rsplit('.', 1)[1].lower()
            nombre_archivo = f"reportes/{uuid.uuid4()}.{extension}"
            blob = bucket.blob(nombre_archivo)
            blob.upload_from_string(foto_file.read(), content_type=foto_file.content_type)
            blob.make_public()
            foto_url = blob.public_url
        
        created_at = datetime.utcnow().isoformat()
        
        reporte_data = {
            'fotoUrl': foto_url,
            'categoria': categoria,
            'ubicacion': {'lat': lat_float, 'lng': lng_float},
            'descripcion': descripcion,
            'estado': 'pendiente',
            'usuario_id': usuario_id,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'createdAt': created_at,
            'version': 1
        }
        
        doc_ref = db.collection('reportes').add(reporte_data)
        reporte_id = doc_ref[1].id
        
        return jsonify({
            'success': True,
            'message': 'Reporte creado exitosamente',
            'id': reporte_id,
            'data': {
                'id': reporte_id,
                'categoria': categoria,
                'ubicacion': {'lat': lat_float, 'lng': lng_float},
                'descripcion': descripcion,
                'estado': 'pendiente',
                'usuario_id': usuario_id,
                'createdAt': created_at
            }
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reportes', methods=['GET'])
def obtener_reportes():
    
    try:
        limit = int(request.args.get('limit', 50))
        categoria = request.args.get('categoria')
        usuario_id = request.args.get('usuario_id')
        
        query = db.collection('reportes')
        if categoria:
            query = query.where('categoria', '==', categoria)
        if usuario_id:
            query = query.where('usuario_id', '==', usuario_id)
        
        query = query.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
        docs = query.stream()
        
        reportes = []
        for doc in docs:
            reporte = doc.to_dict()
            reporte['id'] = doc.id
            if 'timestamp' in reporte and reporte['timestamp']:
                reporte['timestamp'] = reporte['timestamp'].isoformat()
            reportes.append(reporte)
        
        return jsonify({'success': True, 'count': len(reportes), 'data': reportes}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reportes/test', methods=['POST'])
def crear_reporte_test():
    
    try:
        # Obtener datos
        categoria = request.form.get('categoria')
        lat = request.form.get('lat')
        lng = request.form.get('lng')
        descripcion = request.form.get('descripcion', '')
        usuario_id = request.form.get('usuario_id') or request.form.get('userId')
        foto_file = request.files.get('foto')
        
        print(f"🔍 DEBUG: Datos recibidos:")
        print(f"   categoria: {categoria}")
        print(f"   lat: {lat}, lng: {lng}")
        print(f"   usuario_id: {usuario_id}")
        print(f"   foto: {foto_file.filename if foto_file else 'No hay foto'}")
        
        
        if not usuario_id:
            return jsonify({
                'error': 'Falta usuario_id o userId',
                'received': {
                    'form_keys': list(request.form.keys()),
                    'files_keys': list(request.files.keys())
                }
            }), 400
        
        if not categoria or not lat or not lng:
            return jsonify({'error': 'Faltan campos requeridos'}), 400
        
        
        foto_url = None
        if foto_file and foto_file.filename:
            try:
                extension = foto_file.filename.rsplit('.', 1)[1].lower()
                nombre_archivo = f"reportes/{uuid.uuid4()}.{extension}"
                blob = bucket.blob(nombre_archivo)
                blob.upload_from_string(foto_file.read(), content_type=foto_file.content_type)
                blob.make_public()
                foto_url = blob.public_url
                print(f" Foto subida: {foto_url}")
            except Exception as e:
                print(f" Error subiendo foto: {str(e)}")
                return jsonify({'error': f'Error al subir foto: {str(e)}'}), 500
        
        
        created_at = datetime.utcnow().isoformat()
        
        reporte_data = {
            'fotoUrl': foto_url,
            'categoria': categoria,
            'ubicacion': {'lat': float(lat), 'lng': float(lng)},
            'descripcion': descripcion,
            'estado': 'pendiente',
            'usuario_id': usuario_id,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'createdAt': created_at,
            'version': 1
        }
        
        doc_ref = db.collection('reportes').add(reporte_data)
        reporte_id = doc_ref[1].id
        
        print(f" Reporte creado con ID: {reporte_id}")
        
        return jsonify({
            'success': True,
            'message': ' Reporte de prueba creado exitosamente',
            'id': reporte_id,
            'foto_url': foto_url,
            'data': {
                'id': reporte_id,
                'categoria': categoria,
                'ubicacion': {'lat': float(lat), 'lng': float(lng)},
                'descripcion': descripcion,
                'estado': 'pendiente',
                'usuario_id': usuario_id,
                'createdAt': created_at
            }
        }), 201
        
    except Exception as e:
        print(f" ERROR GENERAL: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': 'Error interno del servidor',
            'details': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)