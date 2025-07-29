import paramiko
import pandas as pd
import getpass
import time
import re
import sys
import multiprocessing
from datetime import datetime
from openpyxl.utils import get_column_letter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import socket

# Configuración del jumphost
HOST_JUMP = '172.31.238.6'
PORT_JUMP = 22
username = input("Usuario para el jumphost: ")
password = getpass.getpass("Contraseña: ")

# Ajustar según pruebas de rendimiento
MAX_WORKERS = min(50, multiprocessing.cpu_count() * 5)

# Lectura de equipos
df = pd.read_excel('equipos_prueba.xlsx')  # columnas: Ip, Nombre, Jerarquia Red

# Listas para resultados globales
equipos_con_fallas = []
resultados_comandos = []
errores_conexion = []
equipos_comando_no_reconocido = []
errores_mantenimiento = []

# Lock para escritura segura en listas
list_lock = Lock()

# Clase personalizada para el ProxyCommand
class JumpHostProxy(paramiko.proxy.ProxyCommand):
    def __init__(self, jump_host, jump_port, username, password, target_host, target_port):
        self.jump_host = jump_host
        self.jump_port = jump_port
        self.username = username
        self.password = password
        self.target_host = target_host
        self.target_port = target_port
        self.ssh = None
        self.channel = None
        
    def connect(self):
        # Conectar al jumphost
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(
            self.jump_host,
            port=self.jump_port,
            username=self.username,
            password=self.password,
            timeout=10
        )
        
        # Establecer canal SSH hacia el equipo destino
        self.channel = self.ssh.get_transport().open_channel(
            'direct-tcpip',
            (self.target_host, self.target_port),
            ('', 0)
        )
        
    def send(self, content):
        if self.channel is None:
            self.connect()
        return self.channel.send(content)
        
    def recv(self, size):
        if self.channel is None:
            self.connect()
        return self.channel.recv(size)
        
    def close(self):
        if self.channel:
            self.channel.close()
        if self.ssh:
            self.ssh.close()

def analizar_show_card(salida_comando, ip, nombre):
    """ (Mantener función original igual) """
    fallas = []
    lineas = salida_comando.split('\n')
    
    for idx, linea in enumerate(lineas):
        linea_strip = linea.strip()

        # Buscar líneas que empiecen con slot A o B
        if re.match(r'^[AB]\s+', linea_strip):
            partes = re.split(r'\s+', linea_strip)
            slot = partes[0]  # 'A' o 'B'
            
            # 1) Comprobar caso especial “(not equipped)” en las líneas siguientes
            j = idx + 1
            while j < len(lineas) and lineas[j].startswith(' '):
                if "(not equipped)" in lineas[j].lower():
                    fallas.append({
                        'Ip': ip,
                        'Nombre': nombre,
                        'Tarjeta_Falla': slot,
                        'Estado': "Not equipped - Tarjeta no instalada físicamente"
                    })
                    break  # dejamos de procesar este slot
                j += 1
            else:
                # Sólo si no rompemos por not-equipped, pasamos a las comprobaciones normales

                # 2) Buscar estados Admin/Operational en la misma línea
                admin_state = None
                operational_state = None
                for parte in partes:
                    if parte.lower() in ('up', 'down'):
                        if admin_state is None:
                            admin_state = parte.lower()
                        elif operational_state is None:
                            operational_state = parte.lower()
                            break

                if admin_state == 'down' or operational_state == 'down':
                    fallas.append({
                        'Ip': ip,
                        'Nombre': nombre,
                        'Tarjeta_Falla': slot,
                        'Estado': f"Admin: {admin_state}, Operational: {operational_state}"
                    })

                # 3) Patrón combinado “up/active”, “down/standby”
                patron_estado = re.search(r'(up|down)/(standby|active)', linea_strip, re.IGNORECASE)
                if patron_estado and patron_estado.group(1).lower() == 'down':
                    fallas.append({
                        'Ip': ip,
                        'Nombre': nombre,
                        'Tarjeta_Falla': slot,
                        'Estado': f"Operational: {patron_estado.group(0)}"
                    })

    return fallas

def verificar_sincronizacion_exitosa(salida_comando):
    """ (Mantener función original igual) """
    patron_sincronizacion = r"Boot/Config Sync Status\s*:\s*All boot environment synchronized"
    return bool(re.search(patron_sincronizacion, salida_comando, re.IGNORECASE))

def esperar_comando_completado(channel, comando, timeout=300):
    """ (Mantener función original igual) """
    buff = ""
    start = time.time()
    es_comando_mantenimiento = any(cmd in comando.lower() for cmd in ['admin save', 'admin redundancy'])
    
    print(f"        Esperando finalización de: {comando}")
    
    while True:
        if channel.recv_ready():
            try:
                resp = channel.recv(4096).decode('utf-8', errors='ignore')
                buff += resp
                
                if es_comando_mantenimiento:
                    if "Completed." in buff:
                        lineas_despues_completed = buff.split("Completed.")[-1]
                        if re.search(r'[A-Z]:[^#]*#\s*$', lineas_despues_completed):
                            print(f"        ✅ Comando completado (detectado 'Completed.' + prompt)")
                            return buff
                        if time.time() - start > (timeout * 0.8):
                            print(f"        ⚠️ Timeout después de 'Completed.' - asumiendo completado")
                            return buff
                else:
                    lineas = buff.split('\n')
                    for linea in reversed(lineas[-3:]):
                        if re.search(r'[A-Z]:[^#]*#\s*$', linea.strip()):
                            print(f"        ✅ Comando completado (detectado prompt)")
                            return buff
                            
            except UnicodeDecodeError:
                continue
        
        if time.time() - start > timeout:
            print(f"        ❌ Timeout ({timeout}s) - comando puede no haberse completado")
            return buff
        
        time.sleep(0.2)

def ejecutar_comandos_mantenimiento(channel, ip, nombre, jerarquia, output_file_comandos):
    """ (Mantener función original igual) """
    comandos = [
        "admin save",
        "admin save index detail", 
        "admin redundancy synchronize config",
        "admin redundancy synchronize boot-env",
        "show redundancy synchronization"
    ]
    
    resultado_comandos = {
        'Ip': ip,
        'Nombre': nombre,
        'Jerarquia': jerarquia,     
        'Estado_Ejecucion': 'Exitoso',
        'Comandos_Ejecutados': [],
        'Errores': [],
        'Sincronizacion_Exitosa': False,
        'Detalle_Sincronizacion': 'No verificado'
    }
    
    print(f"    🔧 Ejecutando comandos de mantenimiento...")
    
    output_file_comandos.write(f"\n{'='*80}\n")
    output_file_comandos.write(f"COMANDOS DE MANTENIMIENTO - {nombre} ({ip})\n")
    output_file_comandos.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    output_file_comandos.write(f"{'='*80}\n\n")
    
    for i, comando in enumerate(comandos, 1):
        print(f"    ⏳ Ejecutando ({i}/{len(comandos)}): {comando}")
        
        while channel.recv_ready():
            try:
                channel.recv(4096)
            except:
                break
        
        try:
            channel.send(f"{comando}\n")
            time.sleep(0.5)
            
            timeout_por_comando = {
                "admin save": 120,
                "admin save index detail": 120,
                "admin redundancy synchronize config": 180,
                "admin redundancy synchronize boot-env": 1200,
                "show redundancy synchronization": 60
            }
            
            timeout = timeout_por_comando.get(comando, 180)
            salida = esperar_comando_completado(channel, comando, timeout)
            
            output_file_comandos.write(f"--- COMANDO: {comando} ---\n")
            output_file_comandos.write(salida)
            output_file_comandos.write(f"\n{'-'*40}\n\n")
            output_file_comandos.flush()
            
            comando_exitoso = False
            
            if any(cmd in comando.lower() for cmd in ['admin save', 'admin redundancy']):
                if "Completed." in salida:
                    comando_exitoso = True
                elif "Error" in salida or "Failed" in salida:
                    comando_exitoso = False
                else:
                    if re.search(r'[A-Z]:[^#]*#\s*$', salida):
                        comando_exitoso = True
            else:
                if re.search(r'[A-Z]:[^#]*#\s*$', salida) and len(salida.strip()) > 50:
                    comando_exitoso = True
                    if comando == "show redundancy synchronization":
                        if verificar_sincronizacion_exitosa(salida):
                            resultado_comandos['Sincronizacion_Exitosa'] = True
                            resultado_comandos['Detalle_Sincronizacion'] = 'OK'
                        else:
                            resultado_comandos['Sincronizacion_Exitosa'] = False
                            resultado_comandos['Detalle_Sincronizacion'] = 'Fallo verificación'
            
            if comando_exitoso:
                resultado_comandos['Comandos_Ejecutados'].append(comando)
                print(f"    ✅ {comando} - Completado")
            else:
                error_msg = f"Comando '{comando}' falló"
                resultado_comandos['Errores'].append(error_msg)
                print(f"    ❌ {comando} - Error")
                
                errores_mantenimiento.append({
                    'Nombre': nombre,
                    'Ip': ip,
                    'Jerarquia': jerarquia,
                    'Comando': comando,
                    'Error': error_msg 
                })
                
                resultado_comandos['Estado_Ejecucion'] = 'Detenido por error'
                return resultado_comandos
                
        except Exception as e:
            error_msg = f"Excepción en '{comando}': {str(e)}"
            resultado_comandos['Errores'].append(error_msg)
            print(f"    ❌ {comando} - Excepción: {e}")
            
            errores_mantenimiento.append({
                'Nombre': nombre,
                'Ip': ip,
                'Jerarquia': jerarquia,
                'Comando': comando,
                'Error': error_msg
            })
            resultado_comandos['Estado_Ejecucion'] = 'Detenido por error'
            return resultado_comandos
            
        time.sleep(1)
    
    if len(resultado_comandos['Errores']) > 0:
        resultado_comandos['Estado_Ejecucion'] = f"Con errores ({len(resultado_comandos['Errores'])} errores)"
    elif not resultado_comandos['Sincronizacion_Exitosa']:
        resultado_comandos['Estado_Ejecucion'] = 'Comandos OK - Sincronización no verificada'
    
    return resultado_comandos

def conectar_directo_ssh(ip, username, password, timeout=60):
    """
    Establece conexión SSH directa usando ProxyCommand a través del jumphost
    Retorna (client, channel) si es exitoso, (None, None) si falla
    """
    try:
        # Configurar ProxyCommand personalizado
        proxy = JumpHostProxy(
            jump_host=HOST_JUMP,
            jump_port=PORT_JUMP,
            username=username,
            password=password,
            target_host=ip,
            target_port=22
        )
        
        # Crear cliente SSH
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Configurar transporte con el proxy
        transport = paramiko.Transport(proxy)
        transport.start_client()
        
        # Autenticar
        transport.auth_password(username=username, password=password)
        
        # Abrir canal
        channel = transport.open_session()
        channel.get_pty()
        channel.invoke_shell()
        
        return client, channel
        
    except Exception as e:
        print(f"Error en conexión SSH a {ip}: {str(e)}")
        return None, None

def procesar_equipo_completo(ip, nombre, password, output_file, output_file_comandos, jerarquia):
    """
    Procesa un equipo completamente con conexión directa SSH
    """
    print(f"\n→ {nombre} ({ip}) [{jerarquia}]:", end=' ')

    # 1. Conectar al equipo usando ProxyCommand
    client, channel = conectar_directo_ssh(ip, username, password)
    
    if not client or not channel:
        print("✖ Error de conexión SSH")
        with list_lock:
            errores_conexion.append({
                'Nombre': nombre,
                'Ip': ip,
                'Jerarquia': jerarquia,
                'Razon': "Error al establecer conexión SSH directa"
            })
        return True, [], None

    print("✔ Conectado")
    
    try:
        # 2. Configurar entorno
        channel.send("/environment no more\n")
        time.sleep(1)
        while channel.recv_ready():
            channel.recv(2048)

        # 3. Ejecutar show card
        channel.send("show card\n")
        salida = esperar_comando_completado(channel, "show card", timeout=30)

        # Guardar en archivo
        with list_lock:
            output_file.write(f"==== {nombre} ({ip}) ====\n{salida}\n\n")
            output_file.flush()

        # 4. Analizar salida de show card
        fallas_detectadas = analizar_show_card(salida, ip, nombre)
        tiene_fallas = bool(fallas_detectadas)

        # 5. Detectar Huawei por mensaje de comando no reconocido
        if "Unrecognized command found at '^'" in salida:
            print("⚠ Huawei - comando no reconocido")
            with list_lock:
                equipos_comando_no_reconocido.append({
                    'Nombre': nombre,
                    'Ip': ip,
                    'Jerarquia': jerarquia,
                    'Razon': "Comando 'show card' no reconocido"
                })
            return True, [], None
        
        print("✔ show card ejecutado")

        if tiene_fallas:
            print(f"⚠ {len(fallas_detectadas)} tarjeta(s) con falla detectada(s)")
            return True, fallas_detectadas, None
        else:
            print("✅ Sin fallas detectadas")
            # 6. Ejecutar comandos de mantenimiento
            print("🔧 Iniciando mantenimiento...")
            resultado_mantenimiento = ejecutar_comandos_mantenimiento(
                channel, ip, nombre, jerarquia, output_file_comandos
            )
            print(f"✅ Mantenimiento completado - {resultado_mantenimiento['Estado_Ejecucion']}")
            return False, [], resultado_mantenimiento

    except Exception as e:
        print(f"❌ Error durante procesamiento: {e}")
        with list_lock:
            errores_conexion.append({
                'Nombre': nombre,
                'Ip': ip,
                'Jerarquia': jerarquia,
                'Razon': str(e)
            })
        return True, [], None

    finally:
        try:
            channel.close()
            client.close()
        except:
            pass

def tarea_equipo(equipo, password, output_file, output_file_comandos):
    """
    Función que ejecuta el procesamiento de un equipo en un hilo
    """
    ip = equipo['Ip']
    nombre = equipo['Nombre']
    jerarquia = equipo['Jerarquia']
    
    try:
        tiene_fallas, fallas, resultado_mant = procesar_equipo_completo(
            ip, nombre, password, output_file, output_file_comandos, jerarquia
        )
        
        # Guardar resultados globales con lock
        with list_lock:
            if tiene_fallas and fallas:
                for falla in fallas:
                    falla['Jerarquia'] = jerarquia
                equipos_con_fallas.extend(fallas)
            if resultado_mant:
                resultados_comandos.append(resultado_mant)
                
    except Exception as e:
        with list_lock:
            errores_conexion.append({
                'Nombre': nombre,
                'Ip': ip,
                'Jerarquia': jerarquia,
                'Razon': str(e)
            })

# ---------- Programa principal ----------
# Preparar archivos de salida
output_file = open('salidas_show_card.txt', 'w', encoding='utf-8')
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
output_file_comandos = open(f'salidas_comandos_mantenimiento_{timestamp}.txt', 'w', encoding='utf-8')

# Normalizar jerarquías del DataFrame
df['Jerarquia Red'] = df['Jerarquia Red'].str.upper()

# Crear lista de tareas
equipos = [{
    'Ip': row['Ip'],
    'Nombre': row['Nombre'],
    'Jerarquia': row['Jerarquia Red']
} for _, row in df.iterrows()]

print(f"\n🚀 Iniciando procesamiento paralelo con {MAX_WORKERS} hilos...")

# Ejecutar en paralelo
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = []
    for equipo in equipos:
        futures.append(
            executor.submit(
                tarea_equipo,
                equipo,
                password,
                output_file,
                output_file_comandos
            )
        )
    
    # Mostrar progreso
    for i, future in enumerate(as_completed(futures), 1):
        print(f"    Progreso: {i}/{len(futures)} equipos procesados", end='\r')

print("\n✅ Procesamiento paralelo completado.")

# Cerrar recursos
output_file.close()
output_file_comandos.close()

# Generar reporte unificado (mantener igual que en el original)
print("\n" + "="*60)
print("GENERACIÓN DE REPORTE UNIFICADO")
print("="*60)

nombre_reporte_final = f'reporte_completo_{timestamp}.xlsx'

def ajustar_ancho_columnas(writer, df, sheet_name, margen=2):
    worksheet = writer.sheets[sheet_name]
    for idx, col in enumerate(df.columns, 1):
        max_len = max(
            df[col].astype(str).map(len).max(),
            len(col)
        ) + margen
        letra = get_column_letter(idx)
        worksheet.column_dimensions[letra].width = max_len

with pd.ExcelWriter(nombre_reporte_final, engine='openpyxl') as writer:
    # Hoja 1: Resumen General
    resumen_data = []
    total_equipos = len(df)
    equipos_procesados = total_equipos - len(errores_conexion)
    
    resumen_data.append(['Total de equipos', total_equipos])
    resumen_data.append(['Equipos procesados', equipos_procesados])
    resumen_data.append(['Equipos con error conexión', len(errores_conexion)])
    resumen_data.append(['Equipos Huawei', len(equipos_comando_no_reconocido)])
    resumen_data.append(['Equipos con fallas tarjeta', len(equipos_con_fallas)])
    resumen_data.append(['Equipos con mantenimiento', len(resultados_comandos)])
    resumen_data.append(['Errores en mantenimiento', len(errores_mantenimiento)])
    
    df_resumen = pd.DataFrame(resumen_data, columns=['Métrica', 'Valor'])
    df_resumen.to_excel(writer, sheet_name='Resumen General', index=False)
    ajustar_ancho_columnas(writer, df_resumen, 'Resumen General')
    
    # Hoja 2: Fallas de tarjetas
    if equipos_con_fallas:
        df_fallas = pd.DataFrame(equipos_con_fallas)
        df_fallas.to_excel(writer, sheet_name='Fallas de tarjetas', index=False)
        ajustar_ancho_columnas(writer, df_fallas, 'Fallas de tarjetas')
    
    # Hoja 3: Errores de conexión
    if errores_conexion:
        df_errores_con = pd.DataFrame(errores_conexion)
        df_errores_con.to_excel(writer, sheet_name='Errores de conexión', index=False)
        ajustar_ancho_columnas(writer, df_errores_con, 'Errores de conexión')
    
    # Hoja 4: Comandos no reconocidos (Huawei)
    if equipos_comando_no_reconocido:
        df_com_no_rec = pd.DataFrame(equipos_comando_no_reconocido)
        df_com_no_rec.to_excel(writer, sheet_name='Comandos no reconocidos', index=False)
        ajustar_ancho_columnas(writer, df_com_no_rec, 'Comandos no reconocidos')
    
    # Hoja 5: Errores en mantenimiento
    if errores_mantenimiento:
        df_err_mant = pd.DataFrame(errores_mantenimiento)
        df_err_mant.to_excel(writer, sheet_name='Errores mantenimiento', index=False)
        ajustar_ancho_columnas(writer, df_err_mant, 'Errores mantenimiento')

print(f"📊 Reporte completo generado: {nombre_reporte_final}")