import paramiko
import pandas as pd
import getpass
import time
import re
import sys
from datetime import datetime

# Credenciales y lectura de Excel
HOST_JUMP = '172.31.238.6'
PORT_JUMP = 22
username = input("Usuario para el jumphost: ")
password = getpass.getpass("Contraseña: ")
df = pd.read_excel('equipos_prueba.xlsx')  # columnas: Ip, Nombre, Jerarquia Red

# Lista para almacenar equipos con fallas
equipos_con_fallas = []

def analizar_show_card(salida_comando, ip, nombre):
    """
    Analiza la salida del comando 'show card' para detectar tarjetas en falla
    """
    fallas = []
    lineas = salida_comando.split('\n')
    
    for linea in lineas:
        linea = linea.strip()
        
        # Buscar líneas que contengan slots A o B
        if re.match(r'^[AB]\s+', linea):
            partes = re.split(r'\s+', linea)
            
            try:
                slot = partes[0]  # A o B
                
                # Buscar las columnas Admin State y Operational State
                # El formato puede variar, buscamos patrones comunes
                
                # Patron 1: ... up down ...
                if len(partes) >= 4:
                    admin_state = None
                    operational_state = None
                    
                    # Buscar estados en la línea
                    for i, parte in enumerate(partes):
                        if parte.lower() in ['up', 'down']:
                            if admin_state is None:
                                admin_state = parte.lower()
                            elif operational_state is None:
                                operational_state = parte.lower()
                                break
                    
                    # Verificar si hay falla
                    if operational_state == 'down' or admin_state == 'down':
                        estado_falla = f"Admin: {admin_state}, Operational: {operational_state}"
                        fallas.append({
                            'Ip': ip,
                            'Nombre': nombre,
                            'Tarjeta_Falla': slot,
                            'Estado': estado_falla
                        })
                        
                # Patron 2: Buscar patrones como "down/standby", "up/active"
                patron_estado = re.search(r'(up|down)/(standby|active)', linea)
                if patron_estado and 'down' in patron_estado.group(1):
                    fallas.append({
                        'Ip': ip,
                        'Nombre': nombre,
                        'Tarjeta_Falla': slot,
                        'Estado': f"Operational: {patron_estado.group(0)}"
                    })
                    
                # Patron 3: Detectar "not equipped"
                if "(not equipped)" in linea.lower():
                    fallas.append({
                        'Ip': ip,
                        'Nombre': nombre,
                        'Tarjeta_Falla': slot,
                        'Estado': "Not equipped - Tarjeta no instalada físicamente"
                    })
                    
            except (IndexError, ValueError) as e:
                # Si hay error parseando la línea, continuar
                continue
    
    return fallas

# Conexión al jumphost
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect(HOST_JUMP, port=PORT_JUMP, username=username, password=password, timeout=10)
    print(f"✔ Conectado al jumphost {HOST_JUMP}")
except Exception as e:
    print(f"✖ No pude conectar al jumphost: {e}")
    sys.exit(1)

output_file = open('salidas_show_card.txt', 'w', encoding='utf-8')

TIMEOUT = 60

for idx, row in df.iterrows():
    ip = row['Ip']
    nombre = row['Nombre']
    print(f"\n→ {nombre} ({ip}):", end=' ')

    # 1) Abrir un canal fresco por cada equipo
    channel = client.invoke_shell()
    time.sleep(0.5)
    while channel.recv_ready():
        channel.recv(2048)

    # 2) Lanzar el ssh al equipo
    channel.send(f"ssh {ip}\n")
    buff = ""
    start = time.time()
    connected = False

    while True:
        if channel.recv_ready():
            resp = channel.recv(2048).decode('utf-8', errors='ignore')
            buff += resp

            # 2.1) Aceptar autenticidad del host
            if "Are you sure you want to continue connecting" in buff:
                channel.send("yes\n")
                buff = ""
                continue

            # 2.2) Enviar contraseña
            if re.search(r"[Pp]assword:", buff):
                channel.send(password + "\n")
                buff = ""
                continue

            # 2.3) Detectar prompt del equipo (termina en '#')
            if re.search(r".+#\s*$", buff):
                connected = True
                break

        # 2.4) Timeout extendido
        if time.time() - start > TIMEOUT:
            print("✖ Timeout")
            break

    # Si no pudo conectar, cerramos el canal y continuamos
    if not connected:
        channel.close()
        continue

    print("✔ Conectado")

    # Enviar comando para desactivar la paginación
    channel.send("/environment no more\n")
    time.sleep(1)
    # limpiar buffer tras el comando
    while channel.recv_ready():
        channel.recv(2048)

    # 3) Ejecutar show card
    channel.send("show card\n")
    time.sleep(2) 
    salida = ""
    # Esperar un poco más para asegurar que toda la salida se reciba
    time.sleep(1)
    while channel.recv_ready():
        salida += channel.recv(2048).decode('utf-8', errors='ignore')

    # Guardar en archivo
    output_file.write(f"==== {nombre} ({ip}) ====\n{salida}\n\n")

    # 4) Analizar la salida para detectar fallas
    fallas_detectadas = analizar_show_card(salida, ip, nombre)
    if fallas_detectadas:
        equipos_con_fallas.extend(fallas_detectadas)
        print(f"⚠ {len(fallas_detectadas)} tarjeta(s) con falla detectada(s)")
    
    # 5) Detectar si es Huawei
    if "Unrecognized command found at '^'" in salida:
        print("⚠ Huawei - comando no reconocido")
    else:
        print("✔ show card ejecutado")

    # 6) Logout y cierre de canal
    channel.send("logout\n")
    time.sleep(1)
    channel.close()

output_file.close()
client.close()

# Generar reporte Excel si hay fallas
if equipos_con_fallas:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_reporte = f'reporte_fallas_tarjetas_{timestamp}.xlsx'
    
    df_fallas = pd.DataFrame(equipos_con_fallas)
    
    # Crear el archivo Excel con formato
    with pd.ExcelWriter(nombre_reporte, engine='openpyxl') as writer:
        df_fallas.to_excel(writer, sheet_name='Tarjetas en Falla', index=False)
        
        # Ajustar ancho de columnas
        worksheet = writer.sheets['Tarjetas en Falla']
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    print(f"\n📊 Reporte de fallas generado: {nombre_reporte}")
    print(f"📋 Total de tarjetas con falla: {len(equipos_con_fallas)}")
    
    # Mostrar resumen en consola
    print("\n🚨 RESUMEN DE FALLAS DETECTADAS:")
    print("="*60)
    for falla in equipos_con_fallas:
        print(f"• {falla['Nombre']} ({falla['Ip']}) - Tarjeta {falla['Tarjeta_Falla']}: {falla['Estado']}")
else:
    print("\n✅ No se detectaron tarjetas con fallas")

print("\n✅ Terminado. Revisa salidas_show_card.txt")
if equipos_con_fallas:
    print(f"📊 Y el reporte de fallas: reporte_fallas_tarjetas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")