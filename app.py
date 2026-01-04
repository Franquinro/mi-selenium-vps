import os
import time
import sqlite3
import pandas as pd
from datetime import datetime
from flask import Flask, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# --- CONFIGURACIÓN ---
# Se leen de las variables de entorno configuradas en Coolify
USERNAME = os.getenv('SCRAP_USER', 'usuario_por_defecto')
PASSWORD1 = os.getenv('SCRAP_PASS1', 'pass1_por_defecto')
PASSWORD2 = os.getenv('SCRAP_PASS2', 'pass2_por_defecto')

URL_BASE = "https://eworkerbrrc.endesa.es/PIVision/#/Displays/88153/Balance-Combustible-Bco"
DB_NAME = "datos_temporales.db"

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

def init_db():
    """Inicializa la base de datos en cada arranque del contenedor."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS lecturas 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                       descripcion TEXT, valor TEXT, fecha TEXT)''')
    conn.commit()
    conn.close()

def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura de datos Selenium...")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080") # Aumentamos resolución para asegurar visibilidad

    driver = webdriver.Chrome(options=options)
    
    try:
        # Intentamos con la primera contraseña
        auth_url = f"https://{USERNAME}:{PASSWORD1}@{URL_BASE.replace('https://', '')}"
        driver.get(auth_url)
        
        # Espera explícita: hasta 40 segundos a que cargue la estructura de símbolos de PI Vision
        wait = WebDriverWait(driver, 40)
        
        try:
            # Esperamos a que aparezca al menos un contenedor de símbolos
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "symbol-host")))
        except:
            print("Fallo primer login, intentando con contraseña 2...")
            auth_url2 = f"https://{USERNAME}:{PASSWORD2}@{URL_BASE.replace('https://', '')}"
            driver.get(auth_url2)
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "symbol-host")))

        # Pequeño margen para que los valores numéricos se pueblen tras cargar la estructura
        time.sleep(10)

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for tag, descripcion in DATOS_A_BUSCAR:
            try:
                # Localizamos el div que contiene el TAG en su atributo title
                # Usamos contains para ser flexibles con los saltos de línea del title
                xpath = f"//div[contains(@title, '{tag}')]"
                elemento = driver.find_element(By.XPATH, xpath)
                
                # Extraemos el texto (Selenium captura el texto del span interno automáticamente)
                valor = elemento.text.strip()
                
                # Si .text falla, intentamos con innerText vía JS
                if not valor:
                    valor = driver.execute_script("return arguments[0].innerText;", elemento).strip()

                print(f"OK: {descripcion} -> {valor}")
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, valor, fecha_actual))
            except Exception as e:
                print(f"Error capturando {descripcion}: {str(e)[:50]}")
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, "No detectado", fecha_actual))
        
        conn.commit()
        conn.close()
        print("Proceso de guardado finalizado.")

    except Exception as e:
        print(f"Error crítico en Selenium: {e}")
    finally:
        driver.quit()

# --- RUTAS FLASK ---
@app.route('/')
def index():
    try:
        conn = sqlite3.connect(DB_NAME)
        # Obtenemos los últimos 11 registros (una captura completa)
        df = pd.read_sql_query("SELECT descripcion, valor, fecha FROM lecturas ORDER BY id DESC LIMIT 11", conn)
        conn.close()
        data = df.values.tolist()
    except:
        data = []

    html = """
    <!(DOCTYPE html)>
    <html>
        <head>
            <title>Niveles Combustible - Tiempo Real</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
            <meta http-equiv="refresh" content="60">
            <style>
                body { background-color: #f8f9fa; }
                .card { border-radius: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
                .table thead { background-color: #212529; color: white; }
                .status-tag { font-size: 0.8em; color: #6c757d; }
            </style>
        </head>
        <body class="container py-5">
            <div class="row justify-content-center">
                <div class="col-md-10">
                    <div class="card p-4 bg-white">
                        <div class="d-flex justify-content-between align-items-center mb-4">
                            <h2 class="m-0">Última Captura de Niveles</h2>
                            <span class="badge bg-primary">Auto-refresh: 60s</span>
                        </div>
                        
                        <table class="table table-hover border">
                            <thead>
                                <tr>
                                    <th>Descripción</th>
                                    <th>Valor Detectado</th>
                                    <th>Fecha/Hora Captura</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for row in data %}
                                <tr>
                                    <td class="fw-bold text-secondary">{{ row[0] }}</td>
                                    <td><span class="badge bg-success fs-6">{{ row[1] }}</span></td>
                                    <td class="status-tag">{{ row[2] }}</td>
                                </tr>
                                {% endfor %}
                                {% if not data %}
                                <tr><td colspan="3" class="text-center p-4">Esperando la primera captura del sistema...</td></tr>
                                {% endif %}
                            </tbody>
                        </table>
                        <p class="text-muted small mt-3">El script se ejecuta automáticamente cada 1 hora.</p>
                    </div>
                </div>
            </div>
        </body>
    </html>
    """
    return render_template_string(html, data=data)

if __name__ == "__main__":
    init_db()
    
    # Configurar tarea programada
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger="interval", hours=1)
    scheduler.start()

    # Ejecutar una captura inmediata al arrancar para no tener la web vacía
    # Lo envolvemos en un hilo o lo ejecutamos antes de app.run
    print("Ejecutando captura inicial...")
    ejecutar_scrapping()

    # IMPORTANTE: host='0.0.0.0' para Coolify/Docker
    app.run(host='0.0.0.0', port=5000)
