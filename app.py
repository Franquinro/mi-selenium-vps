import os
import time
import base64
import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template_string, send_file
from apscheduler.schedulers.background import BackgroundScheduler

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# --- CONFIGURACIÓN ---
USERNAME = r"enelint\es43282213p"
PASSWORD1 = os.getenv("SCRAP_PASS1", "")
PASSWORD2 = os.getenv("SCRAP_PASS2", "")

DB_NAME = "temp_niveles.db"
SCREENSHOT_PATH = "debug_vps.png"
WINDOW_W, WINDOW_H = 1920, 1080

PI_BASE_URL = "https://eworkerbrrc.endesa.es/PIVision/"
DISPLAY_ID = "88153"
DISPLAY_HASH = f"#/Displays/{DISPLAY_ID}/Balance-Combustible-Bco?mode=kiosk&hidetoolbar&redirect=false"

# Tupla con (tag, descripción, nivel_máximo_metros)
DATOS_A_BUSCAR = (
    ('\\PI-BRRC-S1\\BRRC00-0LBL111A', 'TANQUE ALMACEN FO', 18),
    ('\\PI-BRRC-S1\\BRRC00-0LBL111B', 'TANQUE ALMACEN GO A', 18),
    ('\\PI-BRRC-S1\\BRRC00-0LBL111C', 'TANQUE ALMACEN GO B', 18),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL001JT01A', 'TANQUE DIARIO GO 1', 13),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL002JT01A', 'TANQUE DIARIO GO 2', 13),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL003JT01A', 'TANQUE DIARIO GO 3', 13),
    ('\\PI-BRRC-S1\\BRRC0210EGB30CL001JT01A', 'TANQUE DIARIO GO 4', 13),
    ('\\PI-BRRC-S1\\BRRC00-0LTBM127', 'TANQUE GO VAPORES 80MW', 7),
    ('\\PI-JINA-S1\\JINA00-145J045822', 'NIVEL TANQUE TO2A', 16),
    ('\\PI-JINA-S1\\JINAGT-208J021809', 'NIVEL TQ GO 2 LM TURBINAS GAS', 13),
    ('\\PI-JINA-S1\\JINA00-145J045826', 'NIVEL GO DIESEL 4/5', 3)
)

def set_basic_auth_header(driver, user, password):
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
        "headers": {"Authorization": f"Basic {token}"}
    })

def build_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua, "platform": "Windows"})
    
    return driver

def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura programada...")
    driver = build_driver()
    
    try:
        set_basic_auth_header(driver, USERNAME, PASSWORD1)
        driver.get(PI_BASE_URL)
        time.sleep(5)
        
        target_url = PI_BASE_URL + DISPLAY_HASH
        driver.get(target_url)
        
        primer_tag = DATOS_A_BUSCAR[0][0]
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, f"//div[contains(@title, '{primer_tag}')]"))
        )

        time.sleep(8) # Espera adicional para que los valores numéricos carguen tras la estructura
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # IMPORTANTE: Ya NO borramos todo. Solo borramos datos más viejos de 48 horas para mantener el disco limpio
        limite_borrado = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("DELETE FROM lecturas WHERE fecha < ?", (limite_borrado,))
        
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        for tag, descripcion, nivel_max in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                valor_raw = (elemento.text or "").strip()
                if not valor_raw:
                    valor_raw = driver.execute_script("return arguments[0].innerText;", elemento).strip()
                
                # Intentamos limpiar el valor para que sea solo el número
                valor_limpio = valor_raw.replace('m', '').replace('³', '').replace(',', '.').strip()
                try:
                    # Guardamos el valor numérico si es posible, si no el string
                    float(valor_limpio)
                    valor_final = valor_limpio
                except:
                    valor_final = valor_raw

                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha, nivel_max) VALUES (?, ?, ?, ?)", 
                             (descripcion, valor_final or "---", fecha_actual, nivel_max))
            except:
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha, nivel_max) VALUES (?, ?, ?, ?)", 
                             (descripcion, "Error", fecha_actual, nivel_max))
        
        conn.commit()
        conn.close()
        print(f"[{datetime.now()}] Captura finalizada con éxito.")

    except Exception as e:
        print(f"Error detectado: {e}")
        driver.save_screenshot(SCREENSHOT_PATH)
    finally:
        driver.quit()

@app.route('/')
def index():
    try:
        conn = sqlite3.connect(DB_NAME)
        # Obtenemos datos de las últimas 24 horas para las tendencias
        hace_24h = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        df_completo = pd.read_sql_query("SELECT * FROM lecturas WHERE fecha > ? ORDER BY fecha ASC", conn, params=(hace_24h,))
        conn.close()

        # Preparar estructura de datos para el frontend
        dashboard_data = []
        for _, desc, _ in DATOS_A_BUSCAR:
            df_tanque = df_completo[df_completo['descripcion'] == desc]
            if not df_tanque.empty:
                ultimo = df_tanque.iloc[-1].to_dict()
                # Preparar datos para el gráfico (historial)
                historial_valores = df_tanque['valor'].tolist()
                historial_fechas = [d.split(" ")[1][:5] for d in df_tanque['fecha'].tolist()] # Solo HH:MM
                
                dashboard_data.append({
                    'descripcion': desc,
                    'valor_actual': ultimo['valor'],
                    'fecha': ultimo['fecha'],
                    'nivel_max': ultimo['nivel_max'],
                    'historial_v': historial_valores,
                    'historial_f': historial_fechas
                })
        
        data_barranco = dashboard_data[:8]
        data_jinamar = dashboard_data[8:]
    except Exception as e:
        print(f"Error en index: {e}")
        data_barranco = []
        data_jinamar = []
    
    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Niveles Combustible - Tendencia 24h</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Inter', sans-serif;
                background: #0f172a;
                color: #f1f5f9;
                padding: 20px;
                display: flex; flex-direction: column; align-items: center;
            }
            .dashboard-header { width: 90%; max-width: 1400px; margin-bottom: 25px; border-left: 4px solid #3b82f6; padding-left: 15px; }
            .plant-container { width: 90%; max-width: 1400px; margin-bottom: 40px; }
            .plant-title { font-size: 1.4em; font-weight: 700; margin-bottom: 20px; color: #94a3b8; display: flex; align-items: center; gap: 10px; }
            .widgets-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
            
            .widget {
                background: #1e293b; border-radius: 12px; padding: 18px;
                border: 1px solid #334155; transition: transform 0.2s;
            }
            .widget-header { display: flex; justify-content: space-between; margin-bottom: 12px; }
            .tank-name { font-weight: 700; font-size: 0.9em; color: #3b82f6; }
            .timestamp { font-size: 0.7em; color: #64748b; }
            
            .main-val-container { display: flex; align-items: baseline; gap: 8px; margin-bottom: 5px; }
            .value-number { font-size: 2.2em; font-weight: 800; color: #fff; }
            .value-unit { color: #64748b; font-weight: 600; }
            
            .level-bar-bg { height: 8px; background: #0f172a; border-radius: 4px; margin: 10px 0; overflow: hidden; }
            .level-bar-fill { height: 100%; transition: width 1s; }
            
            .chart-container { height: 80px; margin-top: 15px; }
            
            .bg-low { background: #ef4444; }
            .bg-med { background: #f59e0b; }
            .bg-high { background: #10b981; }
        </style>
    </head>
    <body>
        <div class="dashboard-header">
            <h1>Monitor de Niveles</h1>
            <p style="color: #64748b">Actualización: cada 15 min | Tendencia: últimas 24h</p>
        </div>

        <div class="plant-container">
            <div class="plant-title">📍 PLANTA BARRANCO</div>
            <div class="widgets-grid">
                {% for item in data_barranco %}
                {{ render_widget(item) }}
                {% endfor %}
            </div>
        </div>

        <div class="plant-container">
            <div class="plant-title">📍 PLANTA JINAMAR</div>
            <div class="widgets-grid">
                {% for item in data_jinamar %}
                {{ render_widget(item) }}
                {% endfor %}
            </div>
        </div>

        {% macro render_widget(item) %}
        <div class="widget">
            <div class="widget-header">
                <span class="tank-name">{{ item.descripcion }}</span>
                <span class="timestamp">{{ item.fecha.split(' ')[1] }}</span>
            </div>
            <div class="main-val-container">
                <span class="value-number">{{ item.valor_actual }}</span>
                <span class="value-unit">m</span>
            </div>
            
            {% set pct = (item.valor_actual|float / item.nivel_max|float * 100)|round(0) if item.valor_actual|float > 0 else 0 %}
            <div class="level-bar-bg">
                <div class="level-bar-fill {{ 'bg-high' if pct > 60 else ('bg-med' if pct > 25 else 'bg-low') }}" style="width: {{ pct }}%"></div>
            </div>
            <div style="font-size: 0.7em; color: #64748b">Capacidad: {{ pct }}% de {{ item.nivel_max }}m</div>

            <div class="chart-container">
                <canvas id="chart-{{ item.descripcion|replace(' ', '-') }}"></canvas>
            </div>
        </div>
        {% endmacro %}

        <script>
            const dataFull = {{ dashboard_json|safe }};
            
            dataFull.forEach(item => {
                const ctx = document.getElementById('chart-' + item.descripcion.replace(/ /g, '-')).getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: item.historial_f,
                        datasets: [{
                            data: item.historial_v,
                            borderColor: '#3b82f6',
                            borderWidth: 2,
                            pointRadius: 0,
                            fill: true,
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            tension: 0.4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            x: { display: false },
                            y: { 
                                display: true,
                                grid: { display: false },
                                ticks: { color: '#475569', font: { size: 9 }, maxTicksLimit: 3 }
                            }
                        }
                    }
                });
            });
        </script>
    </body>
    </html>
    """
    # Pasamos los datos serializados a JSON para los gráficos de Chart.js
    dashboard_json = json.dumps(data_barranco + data_jinamar)
    return render_template_string(html, data_barranco=data_barranco, data_jinamar=data_jinamar, dashboard_json=dashboard_json)

@app.route('/debug')
def debug():
    if os.path.exists(SCREENSHOT_PATH):
        return send_file(SCREENSHOT_PATH, mimetype='image/png')
    return "Captura no disponible", 404

if __name__ == "__main__":
    conn = sqlite3.connect(DB_NAME)
    # Aseguramos que la tabla tiene las columnas necesarias
    conn.execute('CREATE TABLE IF NOT EXISTS lecturas (descripcion TEXT, valor TEXT, fecha TEXT, nivel_max INTEGER)')
    conn.close()

    scheduler = BackgroundScheduler()
    # CAMBIO: Ejecución cada 15 minutos exactos (00, 15, 30, 45)
    scheduler.add_job(func=ejecutar_scrapping, trigger='cron', minute='0,15,30,45')
    scheduler.start()
    
    # Ejecución inicial para tener datos al arrancar
    ejecutar_scrapping()
    
    app.run(host='0.0.0.0', port=5000)
