import struct
from Crypto.Cipher import Salsa20

# Порты для передачи данных (оставляем оригинальные значения)
SendPort = 33739
ReceivePort = 33740

def salsa20_dec(dat):
    """
    Оригинальная функция расшифровки Salsa20 из репозитория Bornhall.
    Снимает шифрование с 296-байтового пакета GT7.
    """
    if len(dat) != 296:
        return bytearray(b'')
        
    KEY = b'Simulator Interface Packet GT7 ver 0.0'
    
    # Извлекаем вектор инициализации (IV) по адресу 0x40
    oiv = dat[0x40:0x44]
    iv1 = int.from_bytes(oiv, byteorder='little')
    
    # Оригинальная маска через DEADBEAF
    iv2 = iv1 ^ 0xDEADBEAF
    
    IV = bytearray()
    IV.extend(iv2.to_bytes(4, 'little'))
    IV.extend(iv1.to_bytes(4, 'little'))
    
    # Расшифровываем Си-модулем pycryptodome
    cipher = Salsa20.new(key=KEY[0:32], nonce=bytes(IV))
    ddata = cipher.decrypt(dat)
    
    # Проверяем Magic Number заголовка
    magic = int.from_bytes(ddata[0:4], byteorder='little')
    if magic != 0x47375330:
        return bytearray(b'')
        
    return ddata
