import time
import os
from flask import Flask, request, jsonify, render_template, render_template_string

# Явно указываем Flask искать оверлей в папке templates/
template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'templates'))
app = Flask(__name__, template_folder=template_dir)

# Оперативное гоночное хранилище текущего состояния пелотона
DATABASE = {}
# Чистая база подтвержденных времен кругов пилотов лиги
SESSION_BEST_LAPS = {}
# Судейский журнал истории кругов для отсечки Out-Lap
PILOT_LAP_LOGS = {}

# ВНУТРЕННЯЯ ПАМЯТЬ СЕРВЕРНОГО ДВУХСТОРОННЕГО ТРИГГЕР-ЗАМКА
SERVER_PIT_DB = {}

# НАСТРОЙКИ ЭТАПА ПО УМОЛЧАНИЮ (ИЗМЕНЯЮТСЯ ЧЕРЕЗ АДМИНКУ)
TOTAL_RACE_LAPS = 15
TRACK_NAME = "MONZA"

def process_server_pit_clocks(pilot, current_state, now_time, current_delta):
    """Серверный демпфер: замораживает живую разницу времени без математических откатов назад"""
    try:
        state = int(current_state)
    except:
        state = 2

    # Инициализация структуры пилота
    if pilot not in SERVER_PIT_DB:
        SERVER_PIT_DB[pilot] = {
            "stable_state": "TRACK",          # TRACK, PIT, ON_TRACK_BANNER
            "pit_start_timestamp": 0.0,       # Фактический заезд в боксы
            "track_attempt_timestamp": 0.0,   # Момент, когда пульт прислал статус 2
            "frozen_inner_time_str": "",      # Зафиксированная строчка PIT X.Xs для удержания
            "banner_expire_timestamp": 0.0    # До какого момента держать зеленый баннер (30 секунд)
        }
        
    p = SERVER_PIT_DB[pilot]
    
    # 1. СИТУАЦИЯ: Пилот въехал или стоит в БОКСАХ / МЕНЮ (Статус 1 из пульта)
    if state == 1:
        p["banner_expire_timestamp"] = 0.0 
        p["track_attempt_timestamp"] = 0.0 
        
        if p["stable_state"] != "PIT":
            p["stable_state"] = "PIT"
            p["pit_start_timestamp"] = now_time
            
        live_duration = now_time - p["pit_start_timestamp"]
        return "IN_BOX", "IN BOX", f"PIT {live_duration:.1f}s"

    # 2. СИТУАЦИЯ: Машина едет по трассе (Статус 2 из пульта)
    else:
        if p["stable_state"] == "PIT":
            # Засекаем микро-секунду начала попытки выезда (запускаем 5-секундный карантин)
            if p["track_attempt_timestamp"] == 0.0:
                p["track_attempt_timestamp"] = now_time
                
            # Проверяем, сколько секунд удерживается стабильный флаг 2
            elapsed_attempt = now_time - p["track_attempt_timestamp"]
            
            if elapsed_attempt < 5.0:
                # ТЗ: Мы флаг НЕ МЕНЯЕМ, на оверлее горит IN BOX, а таймер внутри продолжает плавно бежать вперед
                live_duration = now_time - p["pit_start_timestamp"]
                return "IN_BOX", "IN BOX", f"PIT {live_duration:.1f}s"
            else:
                # ПРОВЕРКА ПРОЙДЕНА: Статус "На трассе" стабильно продержался 5 секунд подряд!
                p["stable_state"] = "ON_TRACK_BANNER"
                
                # ЖЕСТКИЙ ФИКС: Берем живую разницу между текущей секундой завершения демпфера (now_time) 
                # и точкой начала пит-стопа. Время застынет пиксель в пиксель на той цифре, что горела на экране!
                total_visual_duration = now_time - p["pit_start_timestamp"]
                p["frozen_inner_time_str"] = f"PIT {max(0.0, total_visual_duration):.1f}s"
                
                # Задаем удержание зеленой плашки ровно на 30 секунд по ТЗ
                p["banner_expire_timestamp"] = now_time + 30.0
                p["track_attempt_timestamp"] = 0.0

        # Ситуация Б: Действует 30-секундное удержание зафиксированного баннера выезда
        if p["stable_state"] == "ON_TRACK_BANNER":
            if now_time < p["banner_expire_timestamp"]:
                return "ON_TRACK", "ON TRACK", p["frozen_inner_time_str"]
            else:
                p["stable_state"] = "TRACK"
                p["banner_expire_timestamp"] = 0.0

        # Ситуация В: Пилот полноценно валит по трассе — отдаем стандартную оранжевую дельту преследователя
        return "DELTA", current_delta, ""


@app.route('/api/telemetry', methods=['POST'])
def receive_telemetry():
    data = request.json
    if not data or "pilot_name" not in data: return jsonify({"status": "error"}), 400
    pilot = data["pilot_name"]
    
    raw_last_ms = int(data.get("raw_last_lap_ms", 0))
    current_lap = int(data.get("current_lap", 0))
    pit_state = int(data.get("pit_state", 2)) 

    # Серверная отсечка выездного круга (Out-Lap)
    if pilot not in PILOT_LAP_LOGS:
        PILOT_LAP_LOGS[pilot] = {"last_seen_lap": current_lap, "was_in_pit_on_this_or_prev_lap": True}
    if current_lap != PILOT_LAP_LOGS[pilot]["last_seen_lap"]:
        if pit_state != 1 and not PILOT_LAP_LOGS[pilot]["was_in_pit_on_this_or_prev_lap"]:
            PILOT_LAP_LOGS[pilot]["was_in_pit_on_this_or_prev_lap"] = False
        else:
            PILOT_LAP_LOGS[pilot]["was_in_pit_on_this_or_prev_lap"] = True
        PILOT_LAP_LOGS[pilot]["last_seen_lap"] = current_lap
    if pit_state == 1:
        PILOT_LAP_LOGS[pilot]["was_in_pit_on_this_or_prev_lap"] = True

    if current_lap >= 2 and not PILOT_LAP_LOGS[pilot]["was_in_pit_on_this_or_prev_lap"] and raw_last_ms > 0:
        if 35000 < raw_last_ms < 600000:
            if pilot not in SESSION_BEST_LAPS or raw_last_ms < SESSION_BEST_LAPS[pilot]:
                SESSION_BEST_LAPS[pilot] = raw_last_ms

    DATABASE[pilot] = {
        "position": data.get("position", "-"),
        "current_lap": int(current_lap),
        "pit_state": int(pit_state), 
        "last_update": time.time()
    }
    return jsonify({"status": "success"})
# АВТО-АДМИНКА: Генерируется сервером из оперативной памяти, внешние файлы .html не нужны!
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    global TOTAL_RACE_LAPS, TRACK_NAME
    success = False
    if request.method == 'POST':
        TRACK_NAME = request.form.get("track_name", "MONZA").strip().upper()
        TOTAL_RACE_LAPS = int(request.form.get("total_laps", 15))
        success = True
        
    html = """
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>Пульт Управления Лиги</title>
    <style>
        body { background: #111; color: #fff; font-family: Arial, sans-serif; padding: 30px; text-align: center; }
        .box { max-width: 400px; margin: 0 auto; background: #222; padding: 25px; border-radius: 8px; border: 2px solid #e31e24; box-shadow: 0 4px 15px rgba(227,30,36,0.3); }
        h2 { color: #e31e24; text-transform: uppercase; letter-spacing: 1px; margin-top: 0; }
        label { display: block; text-align: left; margin: 12px 0 5px; font-weight: bold; font-size: 14px; color: #aaa; }
        input { width: 100%; padding: 10px; border-radius: 4px; border: 1px solid #444; background: #333; color: white; box-sizing: border-box; font-size: 15px; }
        button { width: 100%; background: #e31e24; color: white; border: none; padding: 12px; font-size: 16px; font-weight: bold; border-radius: 4px; cursor: pointer; margin-top: 20px; text-transform: uppercase; }
        .status { margin-top: 15px; color: #55ff55; font-weight: bold; font-size: 13px; }
    </style></head><body><div class="box">
        <h2>🎛️ НАСТРОЙКА ЭТАПА</h2>
        <form method="POST">
            <label>📍 Название гоночного трека:</label>
            <input type="text" name="track_name" value="{{ track_name }}" placeholder="Monza Circuit" required>
            <label>⏱️ Общее количество кругов:</label>
            <input type="number" name="total_laps" value="{{ total_laps }}" min="1" max="200" required>
            <button type="submit">Применить на Стрим 🚀</button>
        </form>
        {% if success %}<div class="status">🟢 Конфигурация успешно обновлена!</div>{% endif %}
    </div></body></html>
    """
    return render_template_string(html, total_laps=TOTAL_RACE_LAPS, track_name=TRACK_NAME, success=success)


@app.route('/api/data')
def get_data():
    now = time.time()
    active = {p: d for p, d in DATABASE.items() if now - d["last_update"] < 4.0}
    try: sorted_pilots = sorted(active.items(), key=lambda x: int(x['position']) if str(x['position']).isdigit() else 99)
    except: sorted_pilots = list(active.items())

    absolute_session_best_ms = 0
    purple_pilot_name = ""
    if SESSION_BEST_LAPS:
        valid_laps = {p: ms for p, ms in SESSION_BEST_LAPS.items() if ms > 0}
        if valid_laps:
            purple_pilot_name = min(valid_laps, key=valid_laps.get)
            absolute_session_best_ms = valid_laps[purple_pilot_name]

    purple_lap_str = "--:--.---"
    if absolute_session_best_ms > 0:
        minutes = int(absolute_session_best_ms / 60000)
        seconds = int((absolute_session_best_ms % 60000) / 1000)
        ms = absolute_session_best_ms % 1000
        purple_lap_str = f"{minutes:02d}:{seconds:02d}.{ms:03d}"

    # Железобетонный забор круга Лидера (P1) через структуру кортежа списка
    leader_current_lap = 1
    if len(sorted_pilots) > 0:
        try:
            # Первый элемент списка, выдёргиваем словарь [1] и ключ круга
            leader_current_lap = max(1, int(sorted_pilots[0][1]["current_lap"]))
        except:
            leader_current_lap = 1

    processed_pilots = []
    leader_best_ms = 0

    for idx, (name, data) in enumerate(sorted_pilots):
        pilot_best_ms = SESSION_BEST_LAPS.get(name, 0)
        pilot_lap = int(data.get("current_lap", 1))
        delta_str = ""

        if idx == 0:
            leader_best_ms = pilot_best_ms
            delta_str = "LEADER_ROW"
        else:
            # Умный маркер круговых пилотов внутри плашки имени
            if leader_current_lap - pilot_lap >= 1:
                lap_diff = leader_current_lap - pilot_lap
                delta_str = f"+{lap_diff} LAP" if lap_diff == 1 else f"+{lap_diff} LAPS"
            else:
                if pilot_best_ms > 0 and leader_best_ms > 0:
                    diff_ms = pilot_best_ms - leader_best_ms
                    delta_str = f"+{(diff_ms / 1000):.3f}s"
                else:
                    delta_str = "—"

        island_mode, island_text, inner_timer_text = process_server_pit_clocks(name, data["pit_state"], now, delta_str)

        processed_pilots.append({
            "position": data["position"],
            "pilot_name": name,
            "island_mode": island_mode,
            "island_text": island_text,
            "inner_timer_text": inner_timer_text  
        })

    return jsonify({
        "pilots": processed_pilots,
        "leader_lap": leader_current_lap,
        "total_laps": TOTAL_RACE_LAPS,
        "track_name": TRACK_NAME,
        "purple_pilot": purple_pilot_name,
        "purple_lap_time": purple_lap_str
    })


@app.route('/overlay')
def get_overlay():
    try:
        return render_template('overlay.html')
    except Exception as e:
        return f"<h3>💥 Ошибка загрузки вёрстки templates/overlay.html: {e}</h3>"


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
