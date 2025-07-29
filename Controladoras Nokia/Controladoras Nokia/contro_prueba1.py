import paramiko
import pandas as pd
import getpass
import time
import re
import sys

# Credenciales y lectura de Excel
HOST_JUMP = '172.31.238.6'
PORT_JUMP = 22
username = input("Usuario para el jumphost: ")
password = getpass.getpass("Contraseña: ")
df = pd.read_excel('equipos_prueba.xlsx')  # columnas: Ip, Nombre, Jerarquia Red

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

# Timeout aumentado a 60 segundos
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

    #Enviar comando para desactivar la paginación
    channel.send("/environment no more\n")
    time.sleep(1)
    # limpiar buffer tras el comando
    while channel.recv_ready():
        channel.recv(2048)

    # 3) Ejecutar show card
    channel.send("show card\n")
    time.sleep(1)
    salida = ""
    while channel.recv_ready():
        salida += channel.recv(2048).decode('utf-8', errors='ignore')

    # Guardar en archivo
    output_file.write(f"==== {nombre} ({ip}) ====\n{salida}\n\n")

    # 4) Detectar si es Huawei
    if "Unrecognized command found at '^'" in salida:
        print("⚠ Huawei")
    else:
        print("✔ show card")

    # 5) Logout y cierre de canal
    channel.send("exit\n")
    time.sleep(1)
    channel.close()

output_file.close()
client.close()
print("\n✅ Terminado. Revisa salidas_show_card.txt")
 