import paramiko
import pandas as pd
import getpass
import time
import re
import sys
from datetime import datetime
from openpyxl.utils import get_column_letter

# Credenciales y lectura de Excel
HOST_JUMP = '172.31.238.6'
PORT_JUMP = 22
username = input("Usuario para el jumphost: ")
password = getpass.getpass("Contraseña: ")
df = pd.read_excel('equipos_prueba.xlsx')  # columnas: Ip, Nombre, Jerarquia Red

# Listas para almacenar resultados
equipos_con_fallas = [] # Lista para equipos con fallas de tarjeta
resultados_comandos = [] # Lista para resultados de comandos de mantenimiento
errores_conexion = []  # Lista para errores de conexión
equipos_comando_no_reconocido = []  # Lista para equipos Huawei
errores_mantenimiento = []  # Lista para errores en comandos de mantenimiento

def analizar_show_card(salida_comando, ip, nombre):
    """
    Analiza la salida del comando 'show card' para detectar tarjetas en falla,
    incluyendo el caso especial de '(not equipped)' en la línea siguiente al slot.
    """
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
    """
    Verifica si la sincronización fue exitosa buscando el mensaje específico
    """
    patron_sincronizacion = r"Boot/Config Sync Status\s*:\s*All boot environment synchronized"
    return bool(re.search(patron_sincronizacion, salida_comando, re.IGNORECASE))

def esperar_comando_completado(channel, comando, timeout=300):
    """
    Espera hasta que un comando se complete detectando patrones específicos
    Para comandos de mantenimiento, busca "Completed." seguido del prompt
    Para otros comandos, busca solo el prompt
    """
    buff = ""
    start = time.time()
    es_comando_mantenimiento = any(cmd in comando.lower() for cmd in ['admin save', 'admin redundancy'])
    
    print(f"        Esperando finalización de: {comando}")
    
    while True:
        if channel.recv_ready():
            try:
                resp = channel.recv(4096).decode('utf-8', errors='ignore')
                buff += resp
                
                # Para comandos de mantenimiento, buscar "Completed." seguido del prompt
                if es_comando_mantenimiento:
                    # Buscar el patrón "Completed." seguido eventualmente por el prompt
                    if "Completed." in buff:
                        # Después de "Completed.", buscar el prompt
                        lineas_despues_completed = buff.split("Completed.")[-1]
                        if re.search(r'[A-Z]:[^#]*#\s*$', lineas_despues_completed):
                            print(f"        ✅ Comando completado (detectado 'Completed.' + prompt)")
                            return buff
                        # Si no hay prompt aún, continuar esperando un poco más
                        # pero con timeout reducido ya que "Completed." ya apareció
                        if time.time() - start > (timeout * 0.8):  # 80% del timeout original
                            print(f"        ⚠️ Timeout después de 'Completed.' - asumiendo completado")
                            return buff
                else:
                    # Para comandos regulares (como show card), buscar solo el prompt
                    lineas = buff.split('\n')
                    for linea in reversed(lineas[-3:]):  # Revisar las últimas 3 líneas
                        if re.search(r'[A-Z]:[^#]*#\s*$', linea.strip()):
                            print(f"        ✅ Comando completado (detectado prompt)")
                            return buff
                            
            except UnicodeDecodeError:
                # Si hay problemas de codificación, continuar
                continue
        
        # Timeout general
        if time.time() - start > timeout:
            print(f"        ❌ Timeout ({timeout}s) - comando puede no haberse completado")
            return buff
        
        time.sleep(0.2) 

def ejecutar_comandos_mantenimiento(channel, ip, nombre, jerarquia, output_file_comandos):
    """
    Ejecuta la serie de comandos de mantenimiento en equipos sin fallas
    Se detiene al primer error encontrado
    """
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
    
    # Cabecera en archivo de comandos
    output_file_comandos.write(f"\n{'='*80}\n")
    output_file_comandos.write(f"COMANDOS DE MANTENIMIENTO - {nombre} ({ip})\n")
    output_file_comandos.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    output_file_comandos.write(f"{'='*80}\n\n")
    
    for i, comando in enumerate(comandos, 1):
        print(f"    ⏳ Ejecutando ({i}/{len(comandos)}): {comando}")
        
        # Limpiar buffer antes del comando
        while channel.recv_ready():
            try:
                channel.recv(4096)
            except:
                break
        
        try:
            # Enviar comando
            channel.send(f"{comando}\n")
            time.sleep(0.5)
            
            # Timeout específico por comando
            timeout_por_comando = {
                "admin save": 120,
                "admin save index detail": 120,
                "admin redundancy synchronize config": 180,
                "admin redundancy synchronize boot-env": 1200,
                "show redundancy synchronization": 60
            }
            
            timeout = timeout_por_comando.get(comando, 180)
            salida = esperar_comando_completado(channel, comando, timeout)
            
            # Guardar salida
            output_file_comandos.write(f"--- COMANDO: {comando} ---\n")
            output_file_comandos.write(salida)
            output_file_comandos.write(f"\n{'-'*40}\n\n")
            output_file_comandos.flush()
            
            # Verificar ejecución exitosa
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
                
                # Registrar error específico para reporte
                errores_mantenimiento.append({
                    'Nombre': nombre,
                    'Ip': ip,
                    'Jerarquia': jerarquia,
                    'Comando': comando,
                    'Error': error_msg 
                })
                
                # Detener ejecución al primer error
                resultado_comandos['Estado_Ejecucion'] = 'Detenido por error'
                return resultado_comandos
                
        except Exception as e:
            error_msg = f"Excepción en '{comando}': {str(e)}"
            resultado_comandos['Errores'].append(error_msg)
            print(f"    ❌ {comando} - Excepción: {e}")
            
            # Registrar y detener
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
    
    # Determinar estado final
    if len(resultado_comandos['Errores']) > 0:
        resultado_comandos['Estado_Ejecucion'] = f"Con errores ({len(resultado_comandos['Errores'])} errores)"
    elif not resultado_comandos['Sincronizacion_Exitosa']:
        resultado_comandos['Estado_Ejecucion'] = 'Comandos OK - Sincronización no verificada'
    
    return resultado_comandos

def conectar_a_equipo(client, ip, password, timeout=60):
    """
    Conecta a un equipo a través de SSH usando Paramiko.
    Retorna el canal, estado de conexión y razón de fallo si no se conecta.
    """
    channel = client.invoke_shell()
    time.sleep(0.5)
    
    # Vaciar buffer inicial
    while channel.recv_ready():
        channel.recv(2048)

    channel.send(f"ssh {ip}\n")
    buff = ""
    start = time.time()
    connected = False
    razon_fallo = ""

    # Patrón de prompt final: puede terminar en '#' (Nokia) o '>' (Huawei)
    prompt_pattern = re.compile(r".+(?:#|>)\s*$")

    while True:
        # Timeout
        if time.time() - start > timeout:
            razon_fallo = "Timeout"
            break

        if channel.recv_ready():
            try:
                resp = channel.recv(2048).decode('utf-8', errors='ignore')
                buff += resp

                # Confirmar llave del host
                if "Are you sure you want to continue connecting" in buff:
                    channel.send("yes\n")
                    buff = ""
                    continue

                # Detectar petición de contraseña (Nokia y Huawei)
                if re.search(r"(?:[Pp]assword:|Enter password:)", buff):
                    channel.send(password + "\n")
                    buff = ""
                    continue

                # Denegación de permisos
                if "Permission denied" in buff:
                    razon_fallo = "Autenticación fallida"
                    break

                # Prompt de shell (ya conectado)
                if prompt_pattern.search(buff):
                    connected = True
                    break

            except UnicodeDecodeError:
                razon_fallo = "Error de decodificación"
                break

    if not connected and not razon_fallo:
        razon_fallo = "Error desconocido"

    return channel, connected, razon_fallo

def cerrar_conexion_equipo(channel):
    """
    Cierra apropiadamente la conexión a un equipo
    """
    try:
        channel.send("logout\n")
        time.sleep(1)
        channel.close()
    except:
        try:
            channel.close()
        except:
            pass

def procesar_equipo_completo(client, ip, nombre, password, output_file, output_file_comandos, jerarquia):
    """
    Procesa un equipo completamente: show card + comandos de mantenimiento si no hay fallas.
    Reconoce cuando es Huawei por el mensaje de 'Unrecognized command'.
    Retorna (tiene_fallas, fallas_detectadas, resultado_mantenimiento).
    """
    print(f"\n→ {nombre} ({ip}) [{jerarquia}]:", end=' ')

    # 1. Conectar al equipo
    channel, connected, razon_fallo = conectar_a_equipo(client, ip, password)

    if not connected:
        print(f"✖ Error de conexión: {razon_fallo}")
        cerrar_conexion_equipo(channel)
        errores_conexion.append({
            'Nombre': nombre,
            'Ip': ip,
            'Jerarquia': jerarquia,
            'Razon': razon_fallo
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
        output_file.write(f"==== {nombre} ({ip}) ====\n{salida}\n\n")
        output_file.flush()

        # 4. Analizar salida de show card
        fallas_detectadas = analizar_show_card(salida, ip, nombre)
        tiene_fallas = bool(fallas_detectadas)

        # 5. Detectar Huawei por mensaje de comando no reconocido
        if "Unrecognized command found at '^'" in salida:
            print("⚠ Huawei - comando no reconocido")
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
        return True, [], None

    finally:
        cerrar_conexion_equipo(channel)

# Conexión al jumphost
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect(HOST_JUMP, port=PORT_JUMP, username=username, password=password, timeout=10)
    print(f"✔ Conectado al jumphost {HOST_JUMP}")
except Exception as e:
    print(f"✖ No pude conectar al jumphost: {e}")
    sys.exit(1)

# Abrir archivos de salida
output_file = open('salidas_show_card.txt', 'w', encoding='utf-8')
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file_comandos = open(f'salidas_comandos_mantenimiento_{timestamp}.txt', 'w', encoding='utf-8')

# Procesamiento por jerarquías
jerarquias_orden = ['LOW RAN', 'MIDDLE RAN', 'HIGH RAN']
df['Jerarquia Red'] = df['Jerarquia Red'].str.upper()

for ciclo, jerarquia in enumerate(jerarquias_orden, 1):
    print(f"\n{'='*70}")
    print(f"CICLO {ciclo} INICIADO: {jerarquia}")
    print(f"{'='*70}")
    
    # Filtrar equipos por jerarquía actual
    df_ciclo = df[df['Jerarquia Red'] == jerarquia]
    
    for idx, row in df_ciclo.iterrows():
        ip = row['Ip']
        nombre = row['Nombre']
        jerarquia_red = row['Jerarquia Red']
        
        # Procesar equipo
        tiene_fallas, fallas_detectadas, resultado_mantenimiento = procesar_equipo_completo(
            client, ip, nombre, password, output_file, output_file_comandos, jerarquia_red
        )
        
        # Almacenar resultados
        if tiene_fallas and fallas_detectadas:
            for falla in fallas_detectadas:
                falla['Jerarquia'] = jerarquia_red  # Agregar jerarquía
            equipos_con_fallas.extend(fallas_detectadas)
        
        if resultado_mantenimiento:
            resultados_comandos.append(resultado_mantenimiento)
    
    print(f"\n{'='*70}")
    print(f"CICLO {ciclo} TERMINADO: {jerarquia}")
    print(f"{'='*70}")

# Cerrar archivos y conexión
output_file.close()
output_file_comandos.close()
client.close()

# Generar reporte unificado
print("\n" + "="*60)
print("GENERACIÓN DE REPORTE UNIFICADO")
print("="*60)

nombre_reporte_final = f'reporte_completo_{timestamp}.xlsx'

# Función para ajustar el ancho de columnas en Excel
def ajustar_ancho_columnas(writer, df, sheet_name, margen=2):
    """
    Ajusta el ancho de columnas de la hoja `sheet_name` en el ExcelWriter
    según la longitud máxima de los contenidos en `df` más un margen.
    """
    worksheet = writer.sheets[sheet_name]
    for idx, col in enumerate(df.columns, 1):
        # calcular longitud máxima (valores + encabezado)
        max_len = max(
            df[col].astype(str).map(len).max(),
            len(col)
        ) + margen
        letra = get_column_letter(idx)
        worksheet.column_dimensions[letra].width = max_len

# Crear Excel con múltiples hojas
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

# Mostrar resumen en consola
if equipos_con_fallas:
    print(f"\n🚨 TARJETAS CON FALLAS DETECTADAS ({len(equipos_con_fallas)}):")
    print("="*60)
    for falla in equipos_con_fallas:
        print(f"• {falla['Nombre']} ({falla['Ip']}) - Tarjeta {falla['Tarjeta_Falla']}: {falla['Estado']}")
else:
    print("✅ No se detectaron tarjetas con fallas")

if resultados_comandos:
    print(f"\n🔧 COMANDOS DE MANTENIMIENTO EJECUTADOS ({len(resultados_comandos)}):")
    print("="*60)
    for resultado in resultados_comandos:
        estado_emoji = "✅" if resultado['Estado_Ejecucion'] == 'Exitoso' else "⚠️"
        sync_emoji = "🔄✅" if resultado['Sincronizacion_Exitosa'] else "🔄❌"
        print(f"{estado_emoji} {sync_emoji} {resultado['Nombre']} ({resultado['Ip']}) - {resultado['Estado_Ejecucion']}")
        if not resultado['Sincronizacion_Exitosa']:
            print(f"    🚨 SINCRONIZACIÓN: {resultado['Detalle_Sincronizacion']}")
        if resultado['Errores']:
            for error in resultado['Errores']:
                print(f"    ❌ {error}")
else:
    print("ℹ️  No se ejecutaron comandos de mantenimiento")

print(f"\n✅ Proceso completado. Archivos generados:")
print(f"📄 Salidas show card: salidas_show_card.txt")
print(f"📄 Salidas comandos: salidas_comandos_mantenimiento_{timestamp}.txt")
print(f"📊 Reporte completo: {nombre_reporte_final}")
print(f"    └── Hoja 1: Resumen General")
if equipos_con_fallas:
    print(f"    └── Hoja 2: Tarjetas en Falla")
if resultados_comandos:
    print(f"    └── Hoja 3: Comandos Mantenimiento")