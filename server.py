import time
import os
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
DATABASE = {}

@app.route('/')
def home_index():
    html_home = """
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>GT7 Телеметрия</title>
    <style>
        body { font-family: Arial; background: #111; color: white; text-align: center; padding-top: 100px; }
        .card { background: #222; max-width: 500px; margin: 0 auto; padding: 30px; border-radius: 8px; border-top: 4px solid #e10600; }
        .status { background: #00ff00; color: black; display: inline-block; padding: 5px 15px; border-radius: 4px; font-weight: bold; }
    </style></head><body><div class="card"><h1>GT7 TELEMETRY SERVER</h1><div class="status">🟢 СЕРВЕР ОНЛАЙН</div>
    <p>Телеметрия градусов всех 4 шин запущена.</p><a href="/overlay" style="color:#ff0000; font-weight:bold;">📺 Открыть ТВ-Оверлей</a></div></body></html>
    """
    return render_template_string(html_home)

@app.route('/api/telemetry', methods=['POST'])
def receive_telemetry():
    data = request.json
    if not data or "pilot_name" not in data: return jsonify({"status": "error"}), 400
    pilot = data["pilot_name"]
    DATABASE[pilot] = {
        "position": data.get("position", "-"),
        "current_lap": data.get("current_lap", "-"),
        "lap_time": data.get("lap_time", "00:00.000"),
        "tyre_wear": data.get("tyre_wear", {"FL": 60, "FR": 60, "RL": 60, "RR": 60}),
        "fuel_pct": data.get("fuel_pct", 100),
        "last_update": time.time()
    }
    return jsonify({"status": "success"})

@app.route('/api/data')
def get_data():
    now = time.time()
    active = {p: d for p, d in DATABASE.items() if now - d["last_update"] < 4.0}
    try:
        return jsonify(dict(sorted(active.items(), key=lambda x: int(x['position']) if str(x['position']).isdigit() else 99)))
    except:
        return jsonify(active)

@app.route('/overlay')
def get_overlay():
    html_template = """
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <style>
        body { font-family: 'Arial', sans-serif; background: transparent; color: white; margin: 15px; overflow: hidden; }
        .race-table { width: 850px; background: rgba(10, 10, 10, 0.88); border-collapse: collapse; border-radius: 6px; overflow: hidden; }
        .race-header { background: #e10600; color: white; font-weight: bold; font-size: 12px; padding: 10px; text-align: left; text-transform: uppercase; }
        .pilot-row { border-bottom: 1px solid rgba(255,255,255,0.06); }
        .td-cell { padding: 8px 8px; font-size: 15px; vertical-align: middle; }
        .pos-badge { background: #2a2a2a; padding: 3px 8px; border-radius: 4px; font-weight: bold; color: #ffcc00; }
        
        /* НОВАЯ КВАДРАТНАЯ СЕТКА ДЛЯ ВСЕХ 4 КОЛЕС (2 колонки по 55px) */
        .tyre-grid { display: grid; grid-template-columns: 55px 55px; gap: 4px; font-size: 10px; font-weight: bold; text-align: center; width: 114px; margin: 0 auto; }
        .tyre-box { height: 16px; border-radius: 2px; color: black; line-height: 16px; font-family: sans-serif; }
        
        .fuel-bar-bg { width: 100%; background: #222; height: 16px; border-radius: 3px; overflow: hidden; position: relative; }
        .fuel-bar-fill { height: 100%; background: #ffaa00; width: 100%; }
        .fuel-text { position: absolute; width: 100%; text-align: center; font-size: 11px; font-weight: bold; line-height: 16px; color: white; top: 0; }
    </style>
    <script>
        async function refreshOverlay() {
            try {
                let response = await fetch('/api/data'); let pilots = await response.json();
                let tableBody = document.getElementById('table-body'); tableBody.innerHTML = '';
                if (Object.keys(pilots).length === 0) {
                    tableBody.innerHTML = `<tr><td colspan='6' style='padding:20px; text-align:center; color:#666;'>⏳ Ожидание подключения пилотов лиги...</td></tr>`; return;
                }
                for (let name in pilots) {
                    let p = pilots[name];
                    
                    // ЦВЕТОВАЯ КАРТА НАГРЕВА: Синий -> Зеленый -> Желтый -> Красный перегрев
                    let getCol = (temp) => {
                        if (temp < 50) return '#00bfff';  // Холодная
                        if (temp < 75) return '#00ff66';  // Оптимальная
                        if (temp < 95) return '#ffcc00';  // Повышенная
                        return '#ff3333';                 // ПЕРЕГРЕВ
                    };

                    tableBody.innerHTML += `
                        <tr class="pilot-row">
                            <td class="td-cell" style="text-align:center; width: 8%;"><span class="pos-badge">P${p.position}</span></td>
                            <td class="td-cell" style="font-weight:bold; width: 22%;">${name}</td>
                            <td class="td-cell" style="color: #aaa; width: 12%;">Круг ${p.current_lap}</td>
                            <td class="td-cell" style="font-family: monospace; font-weight: bold; color: #00ffcc; width: 18%;">⏱️ ${p.lap_time}</td>
                            
                            <!-- ОТРИСОВКА КВАДРАТА ШИН (ЛЕВАЯ ОСЬ И ПРАВАЯ ОСЬ) -->
                            <td class="td-cell" style="width: 22%; text-align: center;">
                                <div class="tyre-grid">
                                    <div class="tyre-box" style="background:${getCol(p.tyre_wear.FL)};">FL:${p.tyre_wear.FL}°</div>
                                    <div class="tyre-box" style="background:${getCol(p.tyre_wear.FR)};">FR:${p.tyre_wear.FR}°</div>
                                    <div class="tyre-box" style="background:${getCol(p.tyre_wear.RL)};">RL:${p.tyre_wear.RL}°</div>
                                    <div class="tyre-box" style="background:${getCol(p.tyre_wear.RR)};">RR:${p.tyre_wear.RR}°</div>
                                </div>
                            </td>
                            
                            <td class="td-cell" style="width: 18%;">
                                <div class="fuel-bar-bg"><div class="fuel-bar-fill" style="width: ${p.fuel_pct}%;"></div><div class="fuel-text">⛽ ${p.fuel_pct}%</div></div>
                            </td>
                        </tr>`;
                }
            } catch (e) {}
        }
        setInterval(refreshOverlay, 500);
    </script></head><body><table class="race-table"><thead><tr><th class="race-header" style="text-align:center;">Поз</th><th class="race-header">Пилот лиги</th><th class="race-header">Круг</th><th class="race-header">Время (Last)</th><th class="race-header" style="text-align:center;">Шины (FL/FR | RL/RR)</th><th class="race-header">Топливо</th></tr></thead><tbody id="table-body"></tbody></table></body></html>
    """
    return render_template_string(html_template)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
