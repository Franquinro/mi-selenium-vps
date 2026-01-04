import os
import time
import base64
import sqlite3
import pandas as pd
from datetime import datetime
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
    print(f"[{datetime.now()}] Iniciando captura...")
    driver = build_driver()
    
    try:
        set_basic_auth_header(driver, USERNAME, PASSWORD1)
        driver.get(PI_BASE_URL)
        time.sleep(5)
        
        target_url = PI_BASE_URL + DISPLAY_HASH
        print(f"Navegando a: {target_url}")
        driver.get(target_url)
        
        primer_tag = DATOS_A_BUSCAR[0][0]
        print(f"Esperando tag: {primer_tag}...")
        
        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, f"//div[contains(@title, '{primer_tag}')]"))
            )
        except Exception as te:
            print(f"Timeout esperando elementos. URL actual: {driver.current_url}")
            driver.save_screenshot(SCREENSHOT_PATH)
            raise te

        time.sleep(5)
        driver.save_screenshot(SCREENSHOT_PATH)

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM lecturas")
        fecha_actual = datetime.now().strftime("%H:%M:%S")
        
        for tag, descripcion, nivel_max in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                valor = (elemento.text or "").strip()
                if not valor:
                    valor = driver.execute_script("return arguments[0].innerText;", elemento).strip()
                cursor.execute("INSERT INTO lecturas VALUES (?, ?, ?, ?)", 
                             (descripcion, valor or "---", fecha_actual, nivel_max))
            except:
                cursor.execute("INSERT INTO lecturas VALUES (?, ?, ?, ?)", 
                             (descripcion, "Error", fecha_actual, nivel_max))
        
        conn.commit()
        conn.close()
        print("Captura finalizada con éxito.")

    except Exception as e:
        print(f"Error detectado: {e}")
        try: driver.save_screenshot(SCREENSHOT_PATH)
        except: pass
    finally:
        driver.quit()

@app.route('/')
def index():
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query("SELECT * FROM lecturas", conn)
        conn.close()
        
        data_barranco = df.iloc[:8].values.tolist()
        data_jinamar = df.iloc[8:].values.tolist()
    except:
        data_barranco = []
        data_jinamar = []
    
    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monitor Niveles Combustible - PI Vision</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%);
                color: #e0e0e0;
                padding: 12px;
                min-height: 100vh;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                align-items: center;
            }
            
            .dashboard-header {
                text-align: center;
                margin-bottom: 15px;
                padding: 12px;
                background: rgba(255, 255, 255, 0.05);
                border-radius: 10px;
                backdrop-filter: blur(10px);
                box-shadow: 0 3px 15px rgba(0, 0, 0, 0.3);
                width: 80%;
            }
            
            .dashboard-header h1 {
                font-size: 1.6em;
                font-weight: 700;
                color: #ffffff;
                margin-bottom: 4px;
                text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
            }
            
            .dashboard-header .subtitle {
                font-size: 0.8em;
                color: #a0aec0;
                letter-spacing: 0.4px;
            }
            
            .plant-container {
                background: rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 15px;
                box-shadow: 0 6px 25px rgba(0, 0, 0, 0.3);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                width: 80%;
            }
            
            .plant-title {
                font-size: 1.2em;
                font-weight: 700;
                color: #ffffff;
                margin-bottom: 12px;
                padding-bottom: 8px;
                border-bottom: 3px solid;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            
            .plant-container.barranco .plant-title {
                border-color: #4299e1;
            }
            
            .plant-container.jinamar .plant-title {
                border-color: #48bb78;
            }
            
            .plant-icon {
                width: 28px;
                height: 28px;
                border-radius: 6px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 1.1em;
                font-weight: bold;
            }
            
            .plant-container.barranco .plant-icon {
                background: linear-gradient(135deg, #4299e1, #3182ce);
                color: white;
            }
            
            .plant-container.jinamar .plant-icon {
                background: linear-gradient(135deg, #48bb78, #38a169);
                color: white;
            }
            
            .widgets-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 12px;
            }
            
            .widget {
                background: rgba(255, 255, 255, 0.06);
                border-radius: 10px;
                padding: 14px;
                border: 1px solid rgba(255, 255, 255, 0.08);
                transition: all 0.3s ease;
                box-shadow: 0 3px 12px rgba(0, 0, 0, 0.2);
            }
            
            .widget:hover {
                transform: translateY(-3px);
                box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
                border-color: rgba(255, 255, 255, 0.15);
            }
            
            .widget-header {
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                margin-bottom: 10px;
            }
            
            .tank-name {
                font-weight: 700;
                font-size: 0.85em;
                color: #ffffff;
                line-height: 1.2;
                flex: 1;
            }
            
            .timestamp {
                font-size: 0.65em;
                color: #718096;
                white-space: nowrap;
                margin-left: 8px;
                padding: 3px 7px;
                background: rgba(0, 0, 0, 0.2);
                border-radius: 5px;
            }
            
            .value-display {
                display: flex;
                align-items: baseline;
                gap: 6px;
                margin-bottom: 6px;
            }
            
            .value-number {
                font-size: 2em;
                font-weight: 700;
                color: #ffffff;
                line-height: 1;
            }
            
            .value-unit {
                font-size: 0.9em;
                color: #a0aec0;
                font-weight: 600;
            }
            
            .max-indicator {
                font-size: 0.7em;
                color: #4299e1;
                font-weight: 600;
                margin-bottom: 8px;
            }
            
            .level-indicator {
                position: relative;
                height: 22px;
                background: rgba(0, 0, 0, 0.3);
                border-radius: 11px;
                overflow: hidden;
                box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.3);
            }
            
            .level-fill {
                height: 100%;
                border-radius: 11px;
                transition: width 0.8s ease, background 0.3s ease;
                display: flex;
                align-items: center;
                justify-content: flex-end;
                padding-right: 10px;
                font-size: 0.68em;
                font-weight: 700;
                color: white;
                text-shadow: 0 1px 3px rgba(0, 0, 0, 0.5);
            }
            
            .level-low {
                background: linear-gradient(90deg, #f56565, #e53e3e);
            }
            
            .level-medium {
                background: linear-gradient(90deg, #ed8936, #dd6b20);
            }
            
            .level-high {
                background: linear-gradient(90deg, #48bb78, #38a169);
            }
            
            .level-error {
                background: linear-gradient(90deg, #718096, #4a5568);
            }
            
            @media (max-width: 1600px) {
                .widgets-grid {
                    grid-template-columns: repeat(3, 1fr);
                }
            }
            
            @media (max-width: 1200px) {
                .widgets-grid {
                    grid-template-columns: repeat(2, 1fr);
                }
                
                .dashboard-header,
                .plant-container {
                    width: 90%;
                }
            }
            
            @media (max-width: 768px) {
                .widgets-grid {
                    grid-template-columns: 1fr;
                }
                
                .dashboard-header,
                .plant-container {
                    width: 95%;
                }
                
                .dashboard-header h1 {
                    font-size: 1.3em;
                }
                
                .value-number {
                    font-size: 1.8em;
                }
            }
        </style>
    </head>
    <body>
        <div class="dashboard-header">
            <h1>🏭 Monitor de Niveles de Combustible</h1>
            <div class="subtitle">Sistema PI Vision - Actualización Automática</div>
        </div>
        
        <div class="plant-container barranco">
            <div class="plant-title">
                <div class="plant-icon">B</div>
                PLANTA BARRANCO
            </div>
            <div class="widgets-grid">
                {% for row in data_barranco %}
                <div class="widget">
                    <div class="widget-header">
                        <div class="tank-name">{{ row[0] }}</div>
                        <div class="timestamp">{{ row[2] }}</div>
                    </div>
                    <div class="value-display">
                        {% set valor_limpio = row[1].replace('m', '').replace('³', '').strip() %}
                        <div class="value-number">{{ valor_limpio if valor_limpio not in ['Error', '---'] else row[1] }}</div>
                        <div class="value-unit">{% if valor_limpio not in ['Error', '---'] %}m{% endif %}</div>
                    </div>
                    <div class="max-indicator">Máximo: {{ row[3] }} m</div>
                    <div class="level-indicator">
                        {% set valor_limpio = row[1].replace('m', '').replace('³', '').replace(',', '.').strip() %}
                        {% if valor_limpio.replace('.', '', 1).isdigit() %}
                            {% set nivel_actual = valor_limpio|float %}
                            {% set nivel_max = row[3]|float %}
                            {% set porcentaje = (nivel_actual / nivel_max * 100)|round(1) %}
                            {% set clase_nivel = 'level-high' if porcentaje >= 60 else ('level-medium' if porcentaje >= 30 else 'level-low') %}
                            <div class="level-fill {{ clase_nivel }}" style="width: {{ porcentaje }}%">{{ porcentaje }}%</div>
                        {% else %}
                            <div class="level-fill level-error" style="width: 100%">{{ row[1] }}</div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <div class="plant-container jinamar">
            <div class="plant-title">
                <div class="plant-icon">J</div>
                PLANTA JINAMAR
            </div>
            <div class="widgets-grid">
                {% for row in data_jinamar %}
                <div class="widget">
                    <div class="widget-header">
                        <div class="tank-name">{{ row[0] }}</div>
                        <div class="timestamp">{{ row[2] }}</div>
                    </div>
                    <div class="value-display">
                        {% set valor_limpio = row[1].replace('m', '').replace('³', '').strip() %}
                        <div class="value-number">{{ valor_limpio if valor_limpio not in ['Error', '---'] else row[1] }}</div>
                        <div class="value-unit">{% if valor_limpio not in ['Error', '---'] %}m{% endif %}</div>
                    </div>
                    <div class="max-indicator">Máximo: {{ row[3] }} m</div>
                    <div class="level-indicator">
                        {% set valor_limpio = row[1].replace('m', '').replace('³', '').replace(',', '.').strip() %}
                        {% if valor_limpio.replace('.', '', 1).isdigit() %}
                            {% set nivel_actual = valor_limpio|float %}
                            {% set nivel_max = row[3]|float %}
                            {% set porcentaje = (nivel_actual / nivel_max * 100)|round(1) %}
                            {% set clase_nivel = 'level-high' if porcentaje >= 60 else ('level-medium' if porcentaje >= 30 else 'level-low') %}
                            <div class="level-fill {{ clase_nivel }}" style="width: {{ porcentaje }}%">{{ porcentaje }}%</div>
                        {% else %}
                            <div class="level-fill level-error" style="width: 100%">{{ row[1] }}</div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, data_barranco=data_barranco, data_jinamar=data_jinamar)

@app.route('/debug')
def debug():
    if os.path.exists(SCREENSHOT_PATH):
        return send_file(SCREENSHOT_PATH, mimetype='image/png')
    return "Captura no disponible", 404

if __name__ == "__main__":
    conn = sqlite3.connect(DB_NAME)
    conn.execute('CREATE TABLE IF NOT EXISTS lecturas (descripcion TEXT, valor TEXT, fecha TEXT, nivel_max INTEGER)')
    conn.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger="interval", hours=1)
    scheduler.start()
    ejecutar_scrapping()
    app.run(host='0.0.0.0', port=5000)
