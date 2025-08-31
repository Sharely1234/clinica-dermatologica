# app.py (reemplaza el contenido por este)
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image as keras_image
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash

import numpy as np
import json
import os
from datetime import datetime
from PIL import Image
import base64
from io import BytesIO

from flask import Flask, render_template


app = Flask(__name__)
app.secret_key = 'clave_super_secreta'

# Usar variable de entorno o valor por defecto
MONGO_URI = os.environ.get("MONGO_URI") or "mongodb+srv://211153_db_user:712LiSa0@cluster0.d2nfxah.mongodb.net/clinica_db?retryWrites=true&w=majority"
app.config["MONGO_URI"] = MONGO_URI

try:
    mongo = PyMongo(app)
    mongo.db.list_collection_names()  # prueba de conexión
    print("Conexión a MongoDB OK")
except Exception as e:
    mongo = None
    print("Error conectando a MongoDB:", e)

# Luego, en tus rutas, agregar verificación
def db():
    if mongo is None:
        raise Exception("No hay conexión a la base de datos")
    return mongo.db

mongo = PyMongo(app)

# Crear carpeta uploads si no existe
os.makedirs("static/uploads", exist_ok=True)

# Diccionario de modelos disponibles (carga al iniciar)
MODELOS = {
    "vgg16": {
        "nombre": "VGG16",
        "modelo": load_model("Modelos/modelo_vgg16_cancer_piel.h5"),
        "clases": json.load(open("DatosIA/clases_skin_labels.json"))
    },
    "cnn": {
        "nombre": "CNN Personalizado",
        "modelo": load_model("Modelos/modelo_entrenadoJM2.h5"),
        "clases": json.load(open("DatosIA/clases_cancer2.json"))
    }
}

@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        usuario = request.form['username']
        password = request.form['password']
        rol = request.form['rol']

        existente = db().usuarios.find_one({"username": usuario})
        if existente:
            error = "El usuario ya existe"
        else:
            mongo.db.usuarios.insert_one({
                "username": usuario,
                "password": generate_password_hash(password),
                "rol": rol
            })
            return redirect(url_for('login'))
    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        usuario = request.form['username']
        password = request.form['password']

        user = mongo.db.usuarios.find_one({"username": usuario})
        if user and check_password_hash(user['password'], password):
            session['usuario'] = usuario
            session['rol'] = user.get('rol', 'medico')
            return redirect(url_for('dashboard'))
        else:
            error = "Credenciales inválidas"
    return render_template('login.html', error=error)

@app.route('/dashboard')
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', usuario=session['usuario'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# RUTA ORIGINAL - subir imagen por formulario
@app.route('/detectar', methods=['POST'])
def detectar():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    imagen = request.files['imagen']
    nombre_paciente = request.form['nombre_paciente']
    modelo_key = request.form['modelo']

    if imagen.filename == '' or not nombre_paciente or modelo_key not in MODELOS:
        return "Faltan datos necesarios."

    ruta_guardado = os.path.join('static/uploads', imagen.filename)
    imagen.save(ruta_guardado)

    modelo_seleccionado = MODELOS[modelo_key]
    modelo = modelo_seleccionado["modelo"]
    clases = modelo_seleccionado["clases"]

    # Determinar tamaño según modelo
    if modelo_key == "cnn":
        target_size = (300, 300)
    else:
        target_size = (224, 224)

    img = keras_image.load_img(ruta_guardado, target_size=target_size)
    img_array = keras_image.img_to_array(img) / 255.0
    img_array = np.expand_dims(img_array, axis=0)

    predicciones = modelo.predict(img_array)
    indice = np.argmax(predicciones)
    etiqueta = list(clases.keys())[list(clases.values()).index(int(indice))]
    porcentaje = round(float(np.max(predicciones)) * 100, 2)

    mongo.db.analisis.insert_one({
        "usuario": session['usuario'],
        "paciente": nombre_paciente,
        "modelo": modelo_key,
        "imagen": imagen.filename,
        "resultado": etiqueta,
        "probabilidad": porcentaje,
        "fecha": datetime.now()
    })

    return render_template('resultado.html', etiqueta=etiqueta, porcentaje=porcentaje,
                           paciente=nombre_paciente, modelo_usado=modelo_seleccionado["nombre"])


@app.route('/historial')
def historial():
    if 'usuario' not in session:
        return redirect('/login')

    paciente = request.args.get("paciente", "").strip().lower()
    tipo = request.args.get("tipo", "").strip().lower()
    mes = request.args.get("mes", "")

    rol = session.get('rol')
    if rol == 'admin':
        registros = list(mongo.db.analisis.find())
    else:
        registros = list(mongo.db.analisis.find({"usuario": session['usuario']}))

    # aplicar filtros
    if paciente:
        registros = [r for r in registros if paciente in r["paciente"].lower()]
    if tipo:
        registros = [r for r in registros if r["resultado"].lower() == tipo]
    if mes:
        registros = [
            r for r in registros
            if r.get("fecha") and r["fecha"].strftime("%Y-%m") == mes
        ]

    return render_template("historial.html", registros=registros, rol=rol)


@app.route('/eliminar/<id>')
def eliminar(id):
    if session.get('rol') != 'admin':
        return "Acceso no autorizado", 403
    mongo.db.analisis.delete_one({'_id': ObjectId(id)})
    return redirect(url_for('historial'))


# ---------------------------
# NUEVAS RUTAS PARA CAMARA
# ---------------------------

@app.route('/camara')
def camara():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    # Enviamos la lista de modelos para construir el select en la plantilla
    return render_template('camara.html', modelos=MODELOS)

@app.route('/detectar_frame', methods=['POST'])
def detectar_frame():
    """
    Recibe JSON: { imagen: "data:image/jpeg;base64,...", modelo: "cnn", nombre_paciente: "Juan" }
    Devuelve JSON con resultado y probabilidad.
    """
    if 'usuario' not in session:
        return jsonify({"error": "No autenticado"}), 401

    data = request.get_json()
    if not data or 'imagen' not in data:
        return jsonify({"error": "No se recibió imagen"}), 400

    modelo_key = data.get('modelo', 'cnn')
    nombre_paciente = data.get('nombre_paciente', 'PacienteCam')

    if modelo_key not in MODELOS:
        return jsonify({"error": "Modelo no válido"}), 400

    # Decodificar imagen base64 sin guardar obligatoriamente
    imagen_b64 = data['imagen'].split(',')[1] if ',' in data['imagen'] else data['imagen']
    try:
        imagen_bytes = base64.b64decode(imagen_b64)
    except Exception as e:
        return jsonify({"error": "Error decodificando imagen", "detalle": str(e)}), 400

    # Cargar imagen en memoria y preprocesar
    modelo_seleccionado = MODELOS[modelo_key]
    modelo = modelo_seleccionado["modelo"]
    clases = modelo_seleccionado["clases"]

    if modelo_key == "cnn":
        target_size = (300, 300)
    else:
        target_size = (224, 224)

    try:
        pil_img = Image.open(BytesIO(imagen_bytes)).convert('RGB')
        pil_img = pil_img.resize(target_size)
        img_array = keras_image.img_to_array(pil_img) / 255.0
        img_array = np.expand_dims(img_array, axis=0)
    except Exception as e:
        return jsonify({"error": "Error procesando imagen", "detalle": str(e)}), 500

    predicciones = modelo.predict(img_array)
    indice = np.argmax(predicciones)
    etiqueta = list(clases.keys())[list(clases.values()).index(int(indice))]
    porcentaje = round(float(np.max(predicciones)) * 100, 2)

    # No guardamos automáticamente en la DB para no inundarla. El frontend puede pedir guardar si lo desea.
    return jsonify({"resultado": etiqueta, "probabilidad": porcentaje})

@app.route('/guardar_resultado', methods=['POST'])
def guardar_resultado():
    """
    Guarda un resultado en la colección analisis.
    JSON esperado:
    {
      "imagen": "data:image/jpeg;base64,...." (opcional),
      "paciente": "Nombre",
      "modelo": "cnn",
      "resultado": "melanoma",
      "probabilidad": 92.3
    }
    """
    if 'usuario' not in session:
        return jsonify({"error": "No autenticado"}), 401

    data = request.get_json()
    paciente = data.get('paciente', 'PacienteCam')
    modelo_key = data.get('modelo', 'cnn')
    resultado = data.get('resultado', '')
    prob = data.get('probabilidad', 0)

    filename = None
    if 'imagen' in data and data['imagen']:
        imagen_b64 = data['imagen'].split(',')[1] if ',' in data['imagen'] else data['imagen']
        try:
            imagen_bytes = base64.b64decode(imagen_b64)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"cam_{session['usuario']}_{timestamp}.jpg"
            ruta = os.path.join('static/uploads', filename)
            with open(ruta, 'wb') as f:
                f.write(imagen_bytes)
        except Exception as e:
            return jsonify({"error": "No se pudo guardar imagen", "detalle": str(e)}), 500

    mongo.db.analisis.insert_one({
        "usuario": session['usuario'],
        "paciente": paciente,
        "modelo": modelo_key,
        "imagen": filename,
        "resultado": resultado,
        "probabilidad": prob,
        "fecha": datetime.now()
    })

    return jsonify({"ok": True})

# Ejecutar app
if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
