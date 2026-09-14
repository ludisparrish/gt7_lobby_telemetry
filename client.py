import sys
import time
import socket
import struct
import math
import requests
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit
from PyQt6.QtCore import QThread, pyqtSignal

# Дешифратор пакетов из твоего файла gt7telemetry.py
from gt7telemetry import salsa20_dec

# UDP порты Gran Turismo 7
SendPort = 33739
ReceivePort = 33740

# =====================================================================
# АРХИТЕКТУРНЫЙ КЛАСС-ПАРСЕР И ОДНОКЛЮЧЕВЫЕ МЕТОДЫ НОРМАЛИЗАЦИИ
# =====================================================================
class GT7PacketParser:
    def __init__(self, decrypted_data):
        self.data = decrypted_data

    def _get_raw(self, fmt, offset):
        try:
            res = struct.unpack(fmt, self.data[offset:offset+struct.calcsize(fmt)])
            return res[0] 
        except:
            return 0

    def normalize_position(self) -> int:
        return int(self._get_raw('h', 0x84))

    def normalize_current_lap(self) -> int:
        return int(self._get_raw('h', 0x74))

    def normalize_last_lap_ms(self) -> int:
        return int(self._get_raw('i', 0x7C))

    def normalize_track_id(self) -> int:
        return int(self._get_raw('i', 0x88))

    def normalize_speed_kmh(self) -> int:
        try:
            v = float(self._get_raw('f', 0x4C))
            v = 0.0 if math.isnan(v) or math.isinf(v) else v
            return int(v * 3.6)
        except:
            return 0

    def normalize_fuel_pct(self) -> int:
        try:
            fuel_now = float(self._get_raw('f', 0x44))
            fuel_max = float(self._get_raw('f', 0x48))
            if math.isnan(fuel_now) or math.isinf(fuel_now) or fuel_max <= 0: return 100
            return int((fuel_now / fuel_max) * 100)
        except:
            return 100

    def normalize_tyre_temps(self) -> dict:
        try:
            t_FL = float(self._get_raw('f', 0x60))
            t_FR = float(self._get_raw('f', 0x64))
            t_RL = float(self._get_raw('f', 0x68))
            t_RR = float(self._get_raw('f', 0x6C))
            return {
                "FL": int(t_FL) if not math.isnan(t_FL) else 0, "FR": int(t_FR) if not math.isnan(t_FR) else 0,
                "RL": int(t_RL) if not math.isnan(t_RL) else 0, "RR": int(t_RR) if not math.isnan(t_RR) else 0
            }
        except:
            return {"FL": 0, "FR": 0, "RL": 0, "RR": 0}

    def normalize_pit_and_track_status(self) -> int:
        speed_kmh = self.normalize_speed_kmh()
        if speed_kmh < 1:
            return 1  # СТОИТ В БОКСАХ / В МЕНЮ
        else:
            return 2  # НА ТРАССЕ / В ДВИЖЕНИИ

    def parse_all(self) -> dict:
        return {
            "position": self.normalize_position(),
            "current_lap": self.normalize_current_lap(),
            "last_lap_ms": self.normalize_last_lap_ms(),
            "track_id": self.normalize_track_id(),
            "speed_kmh": self.normalize_speed_kmh(),
            "fuel_pct": self.normalize_fuel_pct(),
            "tyre_temps": self.normalize_tyre_temps(),
            "pit_state": self.normalize_pit_and_track_status()
        }
# =====================================================================
# ИЗОЛИРОВАННЫЙ АСИНХРОННЫЙ ОТПРАВЩИК PAYLOAD НА СЕРВЕР (МАКСИМАЛЬНО ТУПОЙ)
# =====================================================================
class HTTPPublishWorker(QThread):
    server_status_signal = pyqtSignal(str)

    def __init__(self, server_url, pilot_name):
        super().__init__()
        self.server_url = server_url
        self.pilot_name = pilot_name
        self.running = True
        self.latest_data = None

    def update_data(self, clean_data):
        self.latest_data = clean_data

    def run(self):
        while self.running:
            if self.latest_data:
                self._send_payload(self.latest_data)
            time.sleep(1.0)

    def _send_payload(self, clean_data):
        payload = {
            "pilot_name": self.pilot_name,
            "position": clean_data["position"],
            "current_lap": clean_data["current_lap"],
            "raw_last_lap_ms": clean_data["last_lap_ms"], # Сюда улетит честное время или 0, если круг выездной
            "pit_state": clean_data["pit_state"], 
            "fuel_pct": clean_data["fuel_pct"],
            "track_id": clean_data["track_id"]
        }
        try:
            res = requests.post(f"{self.server_url}/api/telemetry", json=payload, timeout=0.8)
            if res.status_code == 200:
                self.server_status_signal.emit("🟢 Сервер: Данные успешно доставлены!")
            else:
                self.server_status_signal.emit(f"🔴 Сервер: Ошибка базы данных (Код {res.status_code})")
        except:
            self.server_status_signal.emit("🔴 Сервер: Сбой сети / Локальный хостинг упал!")

    def stop(self):
        self.running = False


# =====================================================================
# ВЫСОКОЧАСТОТНЫЙ ПРИЕМНИК UDP С АВТОМАТИЧЕСКИМ ФИЛЬТРОМ OUT-LAP НА КЛИЕНТЕ
# =====================================================================
class TelemetryWorker(QThread):
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    data_signal = pyqtSignal(dict)

    def __init__(self, ps_ip, publisher_worker):
        super().__init__()
        self.ps_ip = ps_ip
        self.publisher = publisher_worker
        self.running = True
        self.sock = None
        
        # Гоночная память клиента для блокировки выездного круга по ТЗ
        self.out_lap_blocked = True
        self.lap_at_exit = 0
        self.last_seen_lap = 0

    def run(self):
        self.log_signal.emit(f"📡 Высокоскоростной UDP-приемник пакетов PS5 запущен.\nIP: {self.ps_ip}")
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(('0.0.0.0', ReceivePort))
            self.sock.settimeout(1.0)
        except Exception as e:
            self.log_signal.emit(f"💥 Ошибка сокета: {e}")
            self.status_signal.emit("🔴 Ошибка порта")
            return

        last_heartbeat_time = 0

        while self.running:
            current_time = time.monotonic()

            if current_time - last_heartbeat_time >= 1.0:
                try:
                    self.sock.sendto(b'A', (self.ps_ip, SendPort))
                    last_heartbeat_time = current_time
                except Exception as e:
                    self.log_signal.emit(f"⚠️ Ошибка Heartbeat: {e}")
                    time.sleep(0.5)
                    continue

            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                self.status_signal.emit("⏳ Ожидание пакетов от PS5...")
                continue
            except Exception:
                if not self.running: break
                continue

            if len(data) == 296:
                try:
                    ddata = salsa20_dec(data)
                    if len(ddata) == 0: continue

                    parser = GT7PacketParser(ddata)
                    clean_data = parser.parse_all()

                    current_lap = clean_data["current_lap"]
                    pit_state = clean_data["pit_state"]

                    # =====================================================================
                    # ТВОЯ ГЕНИАЛЬНАЯ ЛОГИКА ФИЛЬТРА ВЫЕЗДНОГО КРУГА (OUT-LAP) НА КЛИЕНТЕ
                    # =====================================================================
                    # 1. Если мы стоим в боксах/меню (1) — круг ВСЕГДА жестко помечается как выездной
                    if pit_state == 1:
                        if not self.out_lap_blocked:
                            self.log_signal.emit("🛠️ Заезд в боксы/меню. Включена блокировка круга выезда.")
                        self.out_lap_blocked = True
                        self.lap_at_exit = current_lap

                    # 2. Если мы едем по трассе (2), проверяем, сменился ли круг с момента выезда
                    elif pit_state == 2 and self.out_lap_blocked:
                        # Защита от старта лобби: на 0-м и 1-м круге сессия только инициализируется
                        if current_lap <= 1:
                            self.out_lap_blocked = True
                        # Если номер круга увеличился по сравнению с кругом выезда — Out-Lap официально завершен!
                        elif current_lap > self.lap_at_exit:
                            self.out_lap_blocked = False
                            self.log_signal.emit(f"🏁 Выездной круг завершен на черте! Начался боевой круг {current_lap}. Блокировка снята.")

                    # 3. НАКАЗАНИЕ: Если круг выездной — принудительно обнуляем время круга в payload для сервера
                    if self.out_lap_blocked:
                        clean_data["last_lap_ms"] = 0

                    self.last_seen_lap = current_lap

                    # Отправка данных
                    self.data_signal.emit(clean_data)
                    self.status_signal.emit("🟢 Связь с PS5: Телеметрия активна")
                    self.publisher.update_data(clean_data)

                except Exception as e:
                    self.log_signal.emit(f"🧩 Ошибка парсинга пакета: {e}")

    def stop(self):
        self.running = False
        if self.sock:
            try: self.sock.close()
            except: pass
class ClientApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.worker = None
        self.publisher = None

    def initUI(self):
        self.setWindowTitle("GT7 Телеметрия — Профессиональный Пульт")
        self.resize(450, 520)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("<b>1. ВЕРИФИКАЦИЯ СВЯЗИ С СЕРВЕРОМ:</b>"))
        self.btn_check_server = QPushButton("🔍 Проверить статус сервера Flask")
        self.btn_check_server.clicked.connect(self.check_server_status)
        layout.addWidget(self.btn_check_server)
        
        self.lbl_server_status = QLabel("⚪ Статус сервера: Не проверено")
        layout.addWidget(self.lbl_server_status)
        
        layout.addWidget(QLabel("<hr><b>2. КОНФИГУРАЦИЯ ПИЛОТА ЛИГИ:</b>"))
        layout.addWidget(QLabel("Никнейм пилота:"))
        self.input_name = QLineEdit("G. Ludis")
        layout.addWidget(self.input_name)

        layout.addWidget(QLabel("IP-адрес PlayStation 5:"))
        self.input_ip = QLineEdit("192.168.1.37")
        layout.addWidget(self.input_ip)

        layout.addWidget(QLabel("Адрес сервера трансляции:"))
        self.input_server = QLineEdit("http://127.0.0.1:8000")  
        layout.addWidget(self.input_server)

        self.btn_action = QPushButton("🏎️ Запустить трансляцию телеметрии")
        self.btn_action.clicked.connect(self.toggle_stream)
        layout.addWidget(self.btn_action)

        self.lbl_status = QLabel("⚪ Связь с PS5: Остановлено")
        layout.addWidget(self.lbl_status)
        
        self.lbl_pit_status = QLabel("<b>🚦 Гоночный статус: ⚪ [ ОЖИДАНИЕ СТАРТА ]</b>")
        layout.addWidget(self.lbl_pit_status)

        self.lbl_pos = QLabel("📊 Позиция в гонке: --")
        layout.addWidget(self.lbl_pos)
        self.lbl_lap = QLabel("⏱️ Текущий круг: -- | Скорость: -- км/ч")
        layout.addWidget(self.lbl_lap)
        
        self.lbl_fuel = QLabel("⛽ Топливо в баке: --%")
        layout.addWidget(self.lbl_fuel)

        layout.addWidget(QLabel("Окно логгера верификации:"))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)

        self.setLayout(layout)

    def check_server_status(self):
        url = self.input_server.text().strip().rstrip('/')
        try:
            res = requests.get(f"{url}/api/data", timeout=1.5)
            if res.status_code == 200: self.lbl_server_status.setText("🟢 Статус сервера: ОНЛАЙН")
            else: self.lbl_server_status.setText(f"🟡 Статус сервера: ОШИБКА")
        except: self.lbl_server_status.setText("🔴 Статус сервера: ОФФЛАЙН")

    def toggle_stream(self):
        url = self.input_server.text().strip().rstrip('/')
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
            self.worker = None
            if self.publisher:
                self.publisher.stop()
                self.publisher.wait()
                self.publisher = None
            self.btn_action.setText("🏎️ Запустить трансляцию телеметрии")
            self.lbl_status.setText("⚪ Телеметрия: Остановлено")
            self.lbl_pit_status.setText("<b>🚦 Гоночный статус: ⚪ [ ОЖИДАНИЕ СТАРТА ]</b>")
        else:
            self.log_output.clear()
            self.publisher = HTTPPublishWorker(url, self.input_name.text().strip())
            self.publisher.server_status_signal.connect(self.lbl_server_status.setText)
            self.publisher.start()
            
            self.worker = TelemetryWorker(self.input_ip.text().strip(), self.publisher)
            self.worker.status_signal.connect(self.lbl_status.setText)
            self.worker.log_signal.connect(self.log_output.append)
            self.worker.data_signal.connect(self.update_display)
            self.worker.start()
            self.btn_action.setText("⏹️ Остановить трансляцию телеметрии")

    def update_display(self, data):
        self.lbl_pos.setText(f"📊 Позиция в гонке: {data['position']}")
        self.lbl_lap.setText(f"⏱️ Текущий круг: {data['current_lap']} | Скорость: {data['speed_kmh']} км/ч")
        self.lbl_fuel.setText(f"⛽ Топливо в баке: {data['fuel_pct']}%")
        
        state = data.get("pit_state", 2)
        if state == 2:
            self.lbl_pit_status.setText("<b>🚦 Гоночный статус: <font color='#55ff55'>🟢 [ НА ТРАССЕ ]</font></b>")
        elif state == 1:
            self.lbl_pit_status.setText("<b>🚦 Гоночный статус: <font color='#0078ff'>🛠️ [ СТОИТ В БОКСАХ / МЕНЮ ]</font></b>")
        else:
            self.lbl_pit_status.setText("<b>🚦 Гоночный статус: <font color='#ffaa00'>🟠 [ НА ПИТ-ЛЕЙНЕ ]</font></b>")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = ClientApp()
    ex.show()
    sys.exit(app.exec())
