# tyre_analyzer.py

class TyreAnalyzer:
    def __init__(self, base_wear_factor=0.000005, wear_multiplier_game=1.0):
        """
        base_wear_factor: Базовый износ за 1 радиан вращения колеса.
        wear_multiplier_game: Множитель износа шин в настройках лобби игры (например, x1, x5, x10).
        """
        self.base_wear = base_wear_factor * wear_multiplier_game
        
        # Начальное состояние шин (100.0%)
        self.tyre_life = {
            'FL': 100.0,
            'FR': 100.0,
            'RL': 100.0,
            'RR': 100.0
        }
        
        # Порядок колес в массивах UDP-пакетов GT7
        self.wheel_keys = ['FL', 'FR', 'RL', 'RR']

    def _get_color_by_temp(self, temp: float) -> str:
        """Внутренний метод для определения цвета по температуре шины."""
        if temp < 70.0:
            return "Blue"   # Холодная
        elif 70.0 <= temp < 95.0:
            return "Green"  # Идеальная рабочая зона
        elif 95.0 <= temp < 105.0:
            return "Amber"  # Начинается перегрев
        else:
            return "Red"    # Сильный перегрев (шина горит)

    def update(self, car_speed: float, wheel_rps: list, wheel_temps: list, tyre_radius: list) -> dict:
        """
        Принимает «сырые» данные из UDP-пакета за текущий кадр.
        Вычисляет износ и возвращает словарь с процентами и цветами.
        """
        output = {}

        for i, key in enumerate(self.wheel_keys):
            rps = abs(wheel_rps[i])
            temp = wheel_temps[i]
            radius = tyre_radius[i]

            # 1. Расчет пробуксовки / блокировки (Slip)
            wheel_linear_speed = rps * radius
            speed_diff = abs(wheel_linear_speed - car_speed)
            
            slip_multiplier = 1.0
            if speed_diff > 2.0:
                # Чем больше разница скоростей, тем сильнее «горит» резина
                slip_multiplier = 1.0 + (speed_diff * 1.5)

            # 2. Расчет температурного штрафа
            temp_multiplier = 1.0
            if temp > 95.0:
                # Каждые 10 градусов выше нормы существенно ускоряют износ
                temp_multiplier = 1.0 + ((temp - 95.0) * 0.1)

            # 3. Накопление износа за текущую итерацию цикла
            tick_wear = rps * self.base_wear * slip_multiplier * temp_multiplier
            self.tyre_life[key] -= tick_wear

            # Защита от отрицательных значений
            if self.tyre_life[key] < 0.0:
                self.tyre_life[key] = 0.0

            # Формируем результат для этого колеса
            output[key] = {
                'life_percent': round(self.tyre_life[key], 1),
                'temperature': round(temp, 1),
                'color': self._get_color_by_temp(temp)
            }

        return output

    def reset_tyres(self):
        """Метод для сброса состояния шин (например, после пит-стопа или рестарта гонки)."""
        for key in self.tyre_life:
            self.tyre_life[key] = 100.0
