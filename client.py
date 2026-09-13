import sys
import time
import socket
import struct
import math
import requests
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit
from PyQt6.QtCore import QThread, pyqtSignal

# Импортируем тихий дешифратор пакетов из твоего файла gt7telemetry.py
from gt7telemetry import salsa20_dec

SendPort = 33739
ReceivePort = 33740
class TelemetryWorker(QThread):
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    data_signal = pyqtSignal(dict)

    def __init__(self, ps_ip, server_url, pilot_name):
        super().__init__()
        self.ps_ip = ps_ip
        self.server_url = server_url
        self.pilot_name = pilot_name
        self.running = True
        self.sock = None

    def run(self):
        self.log_signal.emit(f"🚀 Стрим-датчик температур запущен.\nPS5 IP: {self.ps_ip}\nТрансляция данных в реальном времени.")
        
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(('0.0.0.0', ReceivePort))
            self.sock.settimeout(1.5)
            self.log_signal.emit(f"📡 UDP сокет {ReceivePort} успешно открыт.")
        except Exception as e:
            self.log_signal.emit(f"💥 Ошибка сокета телеметрии: {e}")
            self.status_signal.emit("🔴 Ошибка порта")
            return

        last_send_time = 0
        send_interval = 1.0
        pknt = 0

        while self.running:
            try:
                pknt += 1
                if pknt > 100 or last_send_time == 0:
                    self.sock.sendto(b'A', (self.ps_ip, SendPort))
                    pknt = 0

                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                self.log_signal.emit("⏳ Тайм-аут: Нет ответа от PS5. Выезжайте из боксов!")
                continue
            except Exception:
                if not self.running: break
                continue

            if len(data) == 296:
                try:
                    ddata = salsa20_dec(data)
                    if len(ddata) == 0: continue

                    def clean_f(val_tuple):
                        v = float(val_tuple[0]) # Добавили [0] внутрь очистки float
                        return 0.0 if math.isnan(v) or math.isinf(v) else v

                    # ФИКС: Добавили индекс [0] к распаковке целых чисел, чтобы убрать ошибку 'not tuple'
                    position = int(struct.unpack('h', ddata[0x84:0x84+2])[0])
                    current_lap = int(struct.unpack('h', ddata[0x74:0x74+2])[0])
                    last_lap_ms = int(struct.unpack('i', ddata[0x7C:0x7C+4])[0])
                    
                    fuel_now = clean_f(struct.unpack('f', ddata[0x44:0x44+4]))
                    fuel_max = clean_f(struct.unpack('f', ddata[0x48:0x48+4]))
                    fuel_percent = int((fuel_now / fuel_max) * 100) if fuel_max > 0 else 100

                    # СЧИТЫВАЕМ ЧИСТУЮ ТЕМПЕРАТУРУ 4-Х ШИН (°C) С ОФИЦИАЛЬНОГО БЛОКА 0x60
                    t_FL = clean_f(struct.unpack('f', ddata[0x60:0x60+4]))
                    t_FR = clean_f(struct.unpack('f', ddata[0x64:0x64+4]))
                    t_RL = clean_f(struct.unpack('f', ddata[0x68:0x68+4]))
                    t_RR = clean_f(struct.unpack('f', ddata[0x6C:0x6C+4]))

                    tyres = {
                        "FL": max(0, min(200, int(t_FL))),
                        "FR": max(0, min(200, int(t_FR))),
                        "RL": max(0, min(200, int(t_RL))),
                        "RR": max(0, min(200, int(t_RR)))
                    }

                    if 0 < last_lap_ms < 3600000:
                        minutes = int(last_lap_ms / 60000)
                        seconds = int((last_lap_ms % 60000) / 1000)
                        ms = last_lap_ms % 1000
                        lap_time_str = f"{minutes:02d}:{seconds:02d}.{ms:03d}"
                    else:
                        lap_time_str = "00:00.000"

                    payload = {
                        "pilot_name": self.pilot_name,
                        "position": position if position > 0 else "-",
                        "current_lap": current_lap if current_lap > 0 else "-",
                        "lap_time": lap_time_str,
                        "tyre_wear": tyres,
                        "fuel_pct": fuel_percent
                    }

                    self.data_signal.emit(payload)
                    self.status_signal.emit("🟢 Температуры шин успешно отправлены!")

                    current_time = time.time()
                    if current_time - last_send_time >= send_interval:
                        try: requests.post(f"{self.server_url}/api/telemetry", json=payload, timeout=0.3)
                        except: pass
                        last_send_time = current_time

                except Exception as e:
                    self.log_signal.emit(f"🧩 Ошибка гоночного потока: {e}")
            
            time.sleep(0.016)

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

    def initUI(self):
        self.setWindowTitle("GT7 Телеметрия — Пульт Температур")
        self.resize(450, 520)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("<b>1. ПРОВЕРКА СВЯЗИ С СЕРВЕРОМ:</b>"))
        self.btn_check_server = QPushButton("🔍 Проверить статус сервера Flask")
        self.btn_check_server.clicked.connect(self.check_server_status)
        layout.addWidget(self.btn_check_server)
        
        self.lbl_server_status = QLabel("⚪ Статус сервера: Не проверено")
        layout.addWidget(self.lbl_server_status)
        
        layout.addWidget(QLabel("<hr><b>2. НАСТРОЙКИ ПИЛОТА:</b>"))
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

        self.lbl_status = QLabel("⚪ Телеметрия: Остановлено")
        layout.addWidget(self.lbl_status)
        
        # Поля вывода гонки (Выводим градусы вместо %)
        self.lbl_pos = QLabel("📊 Позиция в гонке: --")
        layout.addWidget(self.lbl_pos)
        self.lbl_lap = QLabel("⏱️ Текущий круг: -- | Время круга (Last): --")
        layout.addWidget(self.lbl_lap)
        self.lbl_tyres = QLabel("🌡️ Температура шин (FL/FR/RL/RR): --°C | --°C | --°C | --°C")
        layout.addWidget(self.lbl_tyres)
        self.lbl_fuel = QLabel("⛽ Топливо в баке: --%")
        layout.addWidget(self.lbl_fuel)

        layout.addWidget(QLabel("Окно логгера верификации:"))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)

        self.setLayout(layout)

    def check_server_status(self):
        url = self.input_server.text()
        try:
            res = requests.get(f"{url}/api/data", timeout=1.0)
            if res.status_code == 200:
                self.lbl_server_status.setText("🟢 Статус сервера: ОНЛАЙН (Отвечает)")
                self.log_output.append("📥 Верификация успешна: Сервер запущен!")
            else:
                self.lbl_server_status.setText(f"🟡 Статус сервера: ОШИБКА (Код {res.status_code})")
        except Exception as e:
            self.lbl_server_status.setText("🔴 Статус сервера: ОФФЛАЙН (Не найден)")
            self.log_output.append(f"❌ Ошибка связи. Ты точно запустил server.py в отдельном окне?")

    def toggle_stream(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
            self.worker = None
            self.btn_action.setText("🏎️ Запустить трансляцию телеметрии")
            self.lbl_status.setText("⚪ Телеметрия: Остановлено")
            self.log_output.append("⏹️ Трансляция остановлена. Порт успешно закрыт.")
        else:
            self.log_output.clear()
            self.worker = TelemetryWorker(self.input_ip.text(), self.input_server.text(), self.input_name.text())
            self.worker.status_signal.connect(self.lbl_status.setText)
            self.worker.log_signal.connect(self.log_output.append)
            self.worker.data_signal.connect(self.update_display)
            self.worker.start()
            self.btn_action.setText("⏹️ Остановить трансляцию телеметрии")

    def update_display(self, data):
        self.lbl_pos.setText(f"📊 Позиция в гонке: {data['position']}")
        self.lbl_lap.setText(f"⏱️ Текущий круг: {data['current_lap']} | Время (Last): {data['lap_time']}")
        tw = data['tyre_wear']
        # Выводим знак градусов Цельсия
        self.lbl_tyres.setText(f"🌡️ Температура шин: FL:{tw['FL']}°C | FR:{tw['FR']}°C | RL:{tw['RL']}°C | RR:{tw['RR']}°C")
        self.lbl_fuel.setText(f"⛽ Топливо в баке: {data['fuel_pct']}%")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = ClientApp()
    ex.show()
    sys.exit(app.exec())
