
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import uuid
from supabase_config import supabase, SUPABASE_STORAGE_BUCKET
from functools import wraps
from ariadne import QueryType, MutationType, make_executable_schema, graphql_sync
from ariadne.explorer.playground import PLAYGROUND_HTML
import time

app = Flask(__name__)
CORS(app)

request_tracker = {}

def limpiar_tracker():
    """Limpiar registros antiguos del tracker"""
    now = time.time()
    to_delete = []
    for key, data in request_tracker.items():
        if now - data['first_request'] > 3600:  # 1 hora
            to_delete.append(key)
    for key in to_delete:
        del request_tracker[key]

def rate_limit(max_requests=10, time_window=60):
    """Decorador para limitar peticiones por usuario"""
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
    """Verificar si existe un reporte duplicado reciente"""
    try:
        cutoff_time = datetime.utcnow() - timedelta(seconds=time_window)
        
        # Buscar reportes recientes del mismo usuario y categoría
        response = supabase.table('reportes')\
            .select('*')\
            .eq('usuario_id', usuario_id)\
            .eq('categoria', categoria)\
            .gte('created_at', cutoff_time.isoformat())\
            .limit(10)\
            .execute()
        
        reportes = response.data
        
        # Verificar si hay alguno en la misma ubicación
        for reporte in reportes:
            lat_diff = abs(reporte.get('lat', 0) - lat)
            lng_diff = abs(reporte.get('lng', 0) - lng)
            
            if lat_diff < 0.001 and lng_diff < 0.001:
                return True, reporte
        
        return False, None
        
    except Exception as e:
        print(f"Error en verificar_reporte_duplicado: {str(e)}")
        return False, None

def asegurar_usuario_existe(usuario_id):
    """Crear usuario si no existe"""
    try:
        response = supabase.table('usuarios')\
            .select('id')\
            .eq('usuario_id', usuario_id)\
            .limit(1)\
            .execute()
        
        if not response.data:
            # Crear usuario
            supabase.table('usuarios')\
                .insert({'usuario_id': usuario_id})\
                .execute()
            print(f"✅ Usuario {usuario_id} creado automáticamente")
    except Exception as e:
        print(f"⚠️ Error al verificar/crear usuario: {str(e)}")

# ============================================
# GRAPHQL SCHEMA
# ============================================

type_defs = """
    type Query {
        reportes(limit: Int, categoria: String, estado: String, usuario_id: String): [Reporte!]!
        reporte(id: ID!): Reporte
        estadisticas: Estadisticas!
        misReportes(usuario_id: String!): [Reporte!]!
        reportesCercanos(lat: Float!, lng: Float!, radio: Int): [ReporteCercano!]!
    }
    
    type Mutation {
        crearReporte(input: ReporteInput!): ReporteResponse!
        actualizarEstado(id: ID!, estado: String!, usuario_id: String!): ReporteResponse!
    }
    
    type Reporte {
        id: ID!
        categoria: String!
        lat: Float!
        lng: Float!
        descripcion: String
        estado: String!
        prioridad: String!
        usuario_id: String!
        foto_url: String
        created_at: String!
        updated_at: String
        version: Int!
        votos_positivos: Int!
        votos_negativos: Int!
    }
    
    type ReporteCercano {
        id: ID!
        categoria: String!
        lat: Float!
        lng: Float!
        descripcion: String
        estado: String!
        distancia_metros: Float!
        created_at: String!
    }
    
    input ReporteInput {
        categoria: String!
        lat: Float!
        lng: Float!
        descripcion: String
        fotoUrl: String
        usuario_id: String!
        prioridad: String
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
        rechazados: Int!
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
    """Obtener reportes con filtros"""
    try:
        query_builder = supabase.table('reportes').select('*')
        
        if categoria:
            query_builder = query_builder.eq('categoria', categoria)
        if estado:
            query_builder = query_builder.eq('estado', estado)
        if usuario_id:
            query_builder = query_builder.eq('usuario_id', usuario_id)
        
        response = query_builder\
            .order('created_at', desc=True)\
            .limit(limit)\
            .execute()
        
        return response.data or []
    except Exception as e:
        print(f"Error en resolve_reportes: {str(e)}")
        return []

@query.field("misReportes")
def resolve_mis_reportes(_, info, usuario_id):
    """Obtener reportes de un usuario específico"""
    try:
        response = supabase.table('reportes')\
            .select('*')\
            .eq('usuario_id', usuario_id)\
            .order('created_at', desc=True)\
            .limit(100)\
            .execute()
        
        return response.data or []
    except Exception as e:
        print(f"Error en resolve_mis_reportes: {str(e)}")
        return []

@query.field("reporte")
def resolve_reporte(_, info, id):
    """Obtener un reporte específico"""
    try:
        response = supabase.table('reportes')\
            .select('*')\
            .eq('id', id)\
            .limit(1)\
            .execute()
        
        if response.data:
            return response.data[0]
        return None
    except Exception as e:
        print(f"Error en resolve_reporte: {str(e)}")
        return None

@query.field("reportesCercanos")
def resolve_reportes_cercanos(_, info, lat, lng, radio=5000):
    """Buscar reportes cercanos usando función PostGIS"""
    try:
        response = supabase.rpc('buscar_reportes_cercanos', {
            'p_lat': lat,
            'p_lng': lng,
            'p_radio_metros': radio
        }).execute()
        
        return response.data or []
    except Exception as e:
        print(f"Error en resolve_reportes_cercanos: {str(e)}")
        return []

@query.field("estadisticas")
def resolve_estadisticas(_, info):
    """Obtener estadísticas generales"""
    try:
        response = supabase.table('reportes').select('*').execute()
        reportes = response.data or []
        
        total = len(reportes)
        pendientes = sum(1 for r in reportes if r.get('estado') == 'pendiente')
        en_proceso = sum(1 for r in reportes if r.get('estado') == 'en_proceso')
        resueltos = sum(1 for r in reportes if r.get('estado') == 'resuelto')
        rechazados = sum(1 for r in reportes if r.get('estado') == 'rechazado')
        
        categorias = {}
        usuarios = {}
        
        for r in reportes:
            cat = r.get('categoria', 'otro')
            categorias[cat] = categorias.get(cat, 0) + 1
            
            uid = r.get('usuario_id', 'anonimo')
            usuarios[uid] = usuarios.get(uid, 0) + 1
        
        por_categoria = [{'categoria': cat, 'cantidad': cant} for cat, cant in categorias.items()]
        por_usuario = [{'usuario_id': uid, 'cantidad': cant} for uid, cant in usuarios.items()]
        por_usuario.sort(key=lambda x: x['cantidad'], reverse=True)
        
        return {
            'total': total,
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'resueltos': resueltos,
            'rechazados': rechazados,
            'por_categoria': por_categoria,
            'por_usuario': por_usuario[:10]
        }
    except Exception as e:
        print(f"Error en resolve_estadisticas: {str(e)}")
        return {
            'total': 0,
            'pendientes': 0,
            'en_proceso': 0,
            'resueltos': 0,
            'rechazados': 0,
            'por_categoria': [],
            'por_usuario': []
        }

@mutation.field("crearReporte")
def resolve_crear_reporte(_, info, input):
    """Crear nuevo reporte"""
    try:
        categoria = input.get('categoria')
        lat = input.get('lat')
        lng = input.get('lng')
        descripcion = input.get('descripcion', '')
        foto_url = input.get('fotoUrl')
        usuario_id = input.get('usuario_id')
        prioridad = input.get('prioridad', 'media')
        
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
        
        # Verificar duplicados
        es_duplicado, _ = verificar_reporte_duplicado(usuario_id, categoria, lat, lng)
        if es_duplicado:
            return {
                'success': False,
                'message': 'Ya reportaste un incidente similar hace menos de 5 minutos',
                'reporte': None,
                'code': 'DUPLICATE_REPORT'
            }
        
        # Asegurar que el usuario existe
        asegurar_usuario_existe(usuario_id)
        
        # Crear reporte
        reporte_data = {
            'usuario_id': usuario_id,
            'categoria': categoria,
            'lat': float(lat),
            'lng': float(lng),
            'ubicacion': f'SRID=4326;POINT({lng} {lat})',
            'descripcion': descripcion,
            'foto_url': foto_url,
            'estado': 'pendiente',
            'prioridad': prioridad,
            'version': 1,
            'votos_positivos': 0,
            'votos_negativos': 0
        }
        
        response = supabase.table('reportes').insert(reporte_data).execute()
        
        if response.data:
            return {
                'success': True,
                'message': 'Reporte creado exitosamente',
                'reporte': response.data[0],
                'code': 'SUCCESS'
            }
        else:
            return {
                'success': False,
                'message': 'Error al crear reporte',
                'reporte': None,
                'code': 'INTERNAL_ERROR'
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
    """Actualizar estado de un reporte"""
    try:
        estados_validos = ['pendiente', 'en_proceso', 'resuelto', 'rechazado']
        if estado not in estados_validos:
            return {
                'success': False,
                'message': f'Estado inválido. Usar: {", ".join(estados_validos)}',
                'reporte': None,
                'code': 'INVALID_STATE'
            }
        
        # Actualizar (el trigger incrementará la versión automáticamente)
        update_data = {
            'estado': estado,
            'updated_by': usuario_id
        }
        
        response = supabase.table('reportes')\
            .update(update_data)\
            .eq('id', id)\
            .execute()
        
        if response.data:
            return {
                'success': True,
                'message': 'Estado actualizado exitosamente',
                'reporte': response.data[0],
                'code': 'SUCCESS'
            }
        else:
            return {
                'success': False,
                'message': 'Reporte no encontrado',
                'reporte': None,
                'code': 'NOT_FOUND'
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

# ============================================
# REST ENDPOINTS
# ============================================

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
        'message': 'MINGAFIX API v3.0 - Supabase + GraphQL + Rate Limiting',
        'status': 'online',
        'database': 'PostgreSQL + PostGIS (Supabase)',
        'features': {
            'rate_limiting': '30 peticiones por minuto por usuario',
            'duplicate_detection': '5 minutos de ventana',
            'concurrency_control': 'Control de versiones optimista',
            'geospatial': 'Búsquedas por proximidad con PostGIS'
        },
        'endpoints': {
            'graphql': '/graphql',
            'rest': {
                'crear_reporte': 'POST /reportes',
                'obtener_reportes': 'GET /reportes',
                'reporte_test': 'POST /reportes/test'
            }
        }
    }), 200

@app.route('/reportes', methods=['POST'])
@rate_limit(max_requests=30, time_window=60)
def crear_reporte():
    """Crear reporte vía REST"""
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
            prioridad = data.get('prioridad', 'media')
            foto_file = None
        else:
            categoria = request.form.get('categoria')
            lat = request.form.get('lat')
            lng = request.form.get('lng')
            descripcion = request.form.get('descripcion', '')
            foto_url = request.form.get('fotoUrl')
            usuario_id = request.form.get('usuario_id') or request.form.get('userId')
            prioridad = request.form.get('prioridad', 'media')
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
        
        # Verificar duplicados
        es_duplicado, _ = verificar_reporte_duplicado(usuario_id, categoria, lat_float, lng_float)
        if es_duplicado:
            return jsonify({
                'error': 'Ya reportaste un incidente similar recientemente',
                'code': 'DUPLICATE_REPORT'
            }), 409
        
        # Subir foto a Supabase Storage si existe
        if foto_file and foto_file.filename:
            try:
                extension = foto_file.filename.rsplit('.', 1)[1].lower()
                nombre_archivo = f"{uuid.uuid4()}.{extension}"
                
                # Subir a Supabase Storage
                file_bytes = foto_file.read()
                response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET)\
                    .upload(nombre_archivo, file_bytes, {
                        'content-type': foto_file.content_type
                    })
                
                # Obtener URL pública
                foto_url = supabase.storage.from_(SUPABASE_STORAGE_BUCKET)\
                    .get_public_url(nombre_archivo)
                    
            except Exception as e:
                print(f"Error subiendo foto: {str(e)}")
        
        # Asegurar que el usuario existe
        asegurar_usuario_existe(usuario_id)
        
        # Crear reporte
        reporte_data = {
            'usuario_id': usuario_id,
            'categoria': categoria,
            'lat': lat_float,
            'lng': lng_float,
            'ubicacion': f'SRID=4326;POINT({lng_float} {lat_float})',
            'descripcion': descripcion,
            'foto_url': foto_url,
            'estado': 'pendiente',
            'prioridad': prioridad,
            'version': 1,
            'votos_positivos': 0,
            'votos_negativos': 0
        }
        
        response = supabase.table('reportes').insert(reporte_data).execute()
        
        if response.data:
            reporte = response.data[0]
            return jsonify({
                'success': True,
                'message': 'Reporte creado exitosamente',
                'id': reporte['id'],
                'data': reporte
            }), 201
        else:
            return jsonify({'error': 'Error al crear reporte'}), 500
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/reportes', methods=['GET'])
def obtener_reportes():
    """Obtener reportes con filtros"""
    try:
        limit = int(request.args.get('limit', 50))
        categoria = request.args.get('categoria')
        estado = request.args.get('estado')
        usuario_id = request.args.get('usuario_id')
        
        query_builder = supabase.table('reportes').select('*')
        
        if categoria:
            query_builder = query_builder.eq('categoria', categoria)
        if estado:
            query_builder = query_builder.eq('estado', estado)
        if usuario_id:
            query_builder = query_builder.eq('usuario_id', usuario_id)
        
        response = query_builder\
            .order('created_at', desc=True)\
            .limit(limit)\
            .execute()
        
        return jsonify({
            'success': True,
            'count': len(response.data),
            'data': response.data
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reportes/cercanos', methods=['GET'])
def obtener_reportes_cercanos():
    """Obtener reportes cercanos a una ubicación"""
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        radio = int(request.args.get('radio', 5000))
        
        response = supabase.rpc('buscar_reportes_cercanos', {
            'p_lat': lat,
            'p_lng': lng,
            'p_radio_metros': radio
        }).execute()
        
        return jsonify({
            'success': True,
            'count': len(response.data),
            'data': response.data
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reportes/test', methods=['POST'])
def crear_reporte_test():
    """Endpoint de prueba sin rate limiting"""
    try:
        categoria = request.form.get('categoria')
        lat = request.form.get('lat')
        lng = request.form.get('lng')
        descripcion = request.form.get('descripcion', '')
        usuario_id = request.form.get('usuario_id') or request.form.get('userId')
        foto_file = request.files.get('foto')
        
        print(f"🔍 DEBUG: categoria={categoria}, lat={lat}, lng={lng}, usuario={usuario_id}")
        
        if not usuario_id:
            return jsonify({'error': 'Falta usuario_id'}), 400
        
        if not categoria or not lat or not lng:
            return jsonify({'error': 'Faltan campos requeridos'}), 400
        
        foto_url = None
        if foto_file and foto_file.filename:
            try:
                extension = foto_file.filename.rsplit('.', 1)[1].lower()
                nombre_archivo = f"{uuid.uuid4()}.{extension}"
                
                file_bytes = foto_file.read()
                response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET)\
                    .upload(nombre_archivo, file_bytes, {
                        'content-type': foto_file.content_type
                    })
                
                foto_url = supabase.storage.from_(SUPABASE_STORAGE_BUCKET)\
                    .get_public_url(nombre_archivo)
                    
                print(f"✅ Foto subida: {foto_url}")
            except Exception as e:
                print(f"❌ Error subiendo foto: {str(e)}")
        
        # Asegurar que el usuario existe
        asegurar_usuario_existe(usuario_id)
        
        reporte_data = {
            'usuario_id': usuario_id,
            'categoria': categoria,
            'lat': float(lat),
            'lng': float(lng),
            'ubicacion': f'SRID=4326;POINT({lng} {lat})',
            'descripcion': descripcion,
            'foto_url': foto_url,
            'estado': 'pendiente',
            'prioridad': 'media',
            'version': 1,
            'votos_positivos': 0,
            'votos_negativos': 0
        }
        
        response = supabase.table('reportes').insert(reporte_data).execute()
        
        if response.data:
            reporte = response.data[0]
            print(f"✅ Reporte creado: {reporte['id']}")
            return jsonify({
                'success': True,
                'message': '✅ Reporte de prueba creado',
                'id': reporte['id'],
                'foto_url': foto_url,
                'data': reporte
            }), 201
        else:
            return jsonify({'error': 'Error al crear reporte'}), 500
        
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)