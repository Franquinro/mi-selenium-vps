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
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {"Authorization": f"Basic {token}"}})

def build_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua, "platform": "Windows"})
    return driver

def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura...")
    driver = build_driver()
    try:
        set_basic_auth_header(driver, USERNAME, PASSWORD1)
        driver.get(PI_BASE_URL + DISPLAY_HASH)
        
        primer_tag = DATOS_A_BUSCAR[0][0]
        WebDriverWait(driver, 90).until(EC.presence_of_element_located((By.XPATH, f"//div[contains(@title, '{primer_tag}')]")))
        time.sleep(10)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        limite = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("DELETE FROM lecturas WHERE fecha < ?", (limite,))
        
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for tag, descripcion, nivel_max in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                v_raw = (elemento.text or driver.execute_script("return arguments[0].innerText;", elemento)).strip()
                v_num = v_raw.replace('m', '').replace('³', '').replace(',', '.').strip()
                try: float(v_num); final_val = v_num
                except: final_val = v_raw
                cursor.execute("INSERT INTO lecturas VALUES (?, ?, ?, ?)", (descripcion, final_val, fecha_actual, nivel_max))
            except:
                cursor.execute("INSERT INTO lecturas VALUES (?, ?, ?, ?)", (descripcion, "0", fecha_actual, nivel_max))
        conn.commit()
        conn.close()
        print("Captura OK.")
    except Exception as e:
        print(f"Error Scrapping: {e}")
        driver.save_screenshot(SCREENSHOT_PATH)
    finally:
        driver.quit()

@app.route('/')
def index():
    try:
        conn = sqlite3.connect(DB_NAME)
        hace_24h = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        df = pd.read_sql_query("SELECT * FROM lecturas WHERE fecha > ? ORDER BY fecha ASC", conn, params=(hace_24h,))
        conn.close()

        dashboard_data = []
        for _, desc, _ in DATOS_A_BUSCAR:
            df_t = df[df['descripcion'] == desc]
            if not df_t.empty:
                ult = df_t.iloc[-1]
                dashboard_data.append({
                    'desc': desc, 'val': ult['valor'], 'fecha': ult['fecha'].split(' ')[1],
                    'max': ult['nivel_max'], 
                    'h_v': df_t['valor'].tolist(), 
                    'h_f': [d.split(' ')[1][:5] for d in df_t['fecha'].tolist()]
                })
        
        d_barranco = dashboard_data[:8]
        d_jinamar = dashboard_data[8:]
        dashboard_json = json.dumps(dashboard_data)
    except Exception as e:
        print(f"Error Index: {e}")
        d_barranco, d_jinamar, dashboard_json = [], [], "[]"

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Niveles Combustible</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Inter', sans-serif; background: #0f172a; color: #f1f5f9; padding: 20px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; max-width: 1400px; margin: 0 auto; }
            .widget { background: #1e293b; border-radius: 12px; padding: 15px; border: 1px solid #334155; }
            .tank-name { color: #3b82f6; font-weight: 700; font-size: 0.9em; }
            .val { font-size: 2em; font-weight: 800; margin: 10px 0; }
            .bar-bg { height: 8px; background: #0f172a; border-radius: 4px; overflow: hidden; margin: 10px 0; }
            .bar-fill { height: 100%; transition: width 1s; }
            .chart-box { height: 80px; margin-top: 10px; }
            .green { background: #10b981; } .orange { background: #f59e0b; } .red { background: #ef4444; }
            h1 { text-align: center; margin-bottom: 30px; border-bottom: 1px solid #334155; padding-bottom: 10px; }
        </style>
    </head>
    <body>
        {% macro render_card(item) %}
        <div class="widget">
            <div style="display:flex; justify-content:space-between; font-size:0.7em;">
                <span class="tank-name">{{ item.desc }}</span>
                <span style="color:#64748b;">{{ item.fecha }}</span>
            </div>
            <div class="val">{{ item.val }} <small style="font-size:0.4em; color:#64748b;">m</small></div>
            {% set val_f = item.val|float %}
            {% set pct = (val_f / item.max * 100)|round(0) if item.max > 0 else 0 %}
            <div class="bar-bg">
                <div class="bar-fill {{ 'green' if pct > 60 else ('orange' if pct > 25 else 'red') }}" style="width: {{ pct }}%"></div>
            </div>
            <div style="font-size:0.65em; color:#64748b;">Capacidad: {{ pct }}% de {{ item.max }}m</div>
            <div class="chart-box"><canvas id="c-{{ item.desc|replace(' ', '-') }}"></canvas></div>
        </div>
        {% endmacro %}

        <h1>Monitor de Niveles (15 min)</h1>
        <h2 style="max-width: 1400px; margin: 20px auto; color: #94a3b8;">BARRANCO</h2>
        <div class="grid">
            {% for item in d_barranco %}{{ render_card(item) }}{% endfor %}
        </div>
        <h2 style="max-width: 1400px; margin: 40px auto 20px; color: #94a3b8;">JINAMAR</h2>
        <div class="grid">
            {% for item in d_jinamar %}{{ render_card(item) }}{% endfor %}
        </div>

        <script>
            const data = {{ dashboard_json|safe }};
            data.forEach(item => {
                const canvasId = 'c-' + item.desc.replace(/ /g, '-');
                const ctx = document.getElementById(canvasId).getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: item.h_f,
                        datasets: [{
                            data: item.h_v,
                            borderColor: '#3b82f6',
                            borderWidth: 2,
                            pointRadius: 0,
                            fill: true,
                            backgroundColor: 'rgba(59, 130, 246, 0.05)',
                            tension: 0.3
                        }]
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: { x: { display: false }, y: { ticks: { display: false }, grid: { display: false } } }
                    }
                });
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html, d_barranco=d_barranco, d_jinamar=d_jinamar, dashboard_json=dashboard_json)

@app.route('/debug')
def debug():
    return send_file(SCREENSHOT_PATH, mimetype='image/png') if os.path.exists(SCREENSHOT_PATH) else "No hay captura"

if __name__ == "__main__":
    conn = sqlite3.connect(DB_NAME); conn.execute('CREATE TABLE IF NOT EXISTS lecturas (descripcion TEXT, valor TEXT, fecha TEXT, nivel_max INTEGER)'); conn.close()
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger='cron', minute='0,15,30,45')
    scheduler.start()
    ejecutar_scrapping()
    app.run(host='0.0.0.0', port=5000)
