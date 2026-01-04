import os
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import time
from datetime import datetime
import sqlite3
from flask import Flask, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

USERNAME = os.getenv('SCRAP_USER', 'enelint%5CesUsuario')
PASSWORD1 = os.getenv('SCRAP_PASS1', 'Pass1')
PASSWORD2 = os.getenv('SCRAP_PASS2', 'Pass2')

app = Flask(__name__)

# --- CONFIGURACIÓN ---
DB_NAME = "datos_captura.db"
USERNAME = "enelint%5CesUsuario"
PASSWORD1 = "Pass1"
PASSWORD2 = "Pass2"
URL_BASE = "https://eworkerbrrc.endesa.es/PIVision/#/Displays/88153/Balance-Combustible-Bco"

DATOS_A_BUSCAR = (
    ('\\PI-BRRC-S1\\BRRC00-0LBL111A', 'BARRANCO - TANQUE ALMACEN FO'),
    ('\\PI-BRRC-S1\\BRRC00-0LBL111B', 'BARRANCO - TANQUE ALMACEN GO A'),
    ('\\PI-BRRC-S1\\BRRC00-0LBL111C', 'BARRANCO - TANQUE ALMACEN GO B'),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL001JT01A', 'BARRANCO - TANQUE DIARIO GO 1'),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL002JT01A', 'BARRANCO - TANQUE DIARIO GO 2'),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL003JT01A', 'BARRANCO - TANQUE DIARIO GO 3'),
    ('\\PI-BRRC-S1\\BRRC0210EGB30CL001JT01A', 'BARRANCO - TANQUE DIARIO GO 4'),
    ('\\PI-BRRC-S1\\BRRC00-0LTBM127', 'BARRANCO - TANQUE GO VAPORES 80MW'),
    ('\\PI-JINA-S1\\JINA00-145J045822', 'JINAMAR - NIVEL TANQUE TO2A'),
    ('\\PI-JINA-S1\\JINAGT-208J021809', 'JINAMAR - NIVEL TQ GO 2 LM TURBINAS GAS'),
    ('\\PI-JINA-S1\\JINA00-145J045826', 'JINAMAR - NIVEL GO DIESEL 4/5')
)

# --- FUNCIONES DE BASE DE DATOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS lecturas 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                       descripcion TEXT, valor TEXT, fecha TEXT)''')
    conn.commit()
    conn.close()

# --- LÓGICA DE SCRAPPING ---
def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura de datos...")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # En Docker/Ubuntu 24.04 el path suele ser este:
    driver = webdriver.Chrome(options=options)
    
    try:
        # Intento de login (simplificado para el ejemplo)
        # Nota: La autenticación básica en URL puede no ser soportada en navegadores recientes.
        # Si falla, considerar usar extensiones o autenticación manual mediante send_keys.
        url_auth = f"https://{USERNAME}:{PASSWORD1}@{URL_BASE.replace('https://', '')}"
        driver.get(url_auth)
        time.sleep(10) # Esperar carga

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for tag, descripcion in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                valor = elemento.text
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, valor, fecha_actual))
            except:
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, "Error/No encontrado", fecha_actual))
        
        conn.commit()
        conn.close()
        print("Captura finalizada con éxito.")
    except Exception as e:
        print(f"Error en el proceso: {e}")
    finally:
        driver.quit()

# --- RUTAS WEB (FLASK) ---
@app.route('/')
def index():
    conn = sqlite3.connect(DB_NAME)
    # Recuperamos las últimas 50 lecturas
    df = pd.read_sql_query("SELECT * FROM lecturas ORDER BY id DESC LIMIT 50", conn)
    conn.close()
    
    html = """
    <html>
        <head>
            <title>Panel Control Combustible</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
            <meta http-equiv="refresh" content="60">
        </head>
        <body class="container mt-5">
            <h2 class="mb-4">Últimas capturas de datos</h2>
            <table class="table table-striped table-hover">
                <thead class="table-dark">
                    <tr><th>Fecha</th><th>Descripción</th><th>Valor</th></tr>
                </thead>
                <tbody>
                    {% for row in data %}
                    <tr><td>{{ row[3] }}</td><td>{{ row[1] }}</td><td>{{ row[2] }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </body>
    </html>
    """
    return render_template_string(html, data=df.values.tolist())

# --- PROGRAMADOR ---
if __name__ == "__main__":
    init_db()
    
    # Configurar tarea programada cada hora
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger="interval", hours=1)
    scheduler.start()

    # Ejecutar una vez al arrancar para tener datos iniciales
    # Se ejecuta en un hilo separado o antes de bloquear con app.run pero cuidado con el bloqueo
    # Para simplificar en Flask dev server, lo lanzamos aqui, pero ten en cuenta que app.run bloquea.
    # En producción con gunicorn sería diferente, pero aquí está bien.
    # Nota: Si el scraping tarda mucho, puede retrasar el inicio del server web.
    try:
        # Ejecutamos scraping en segundo plano o esperamos?
        # El usuario pidió "Ejecutar una vez al arrancar".
        # Lo haremos, sabiendo que puede tardar.
        print("Ejecutando primera captura al inicio...")
        ejecutar_scrapping()
    except Exception as e:
        print(f"Error en primera captura: {e}")

    # Iniciar servidor web en puerto 5000
    app.run(host='0.0.0.0', port=5000)

