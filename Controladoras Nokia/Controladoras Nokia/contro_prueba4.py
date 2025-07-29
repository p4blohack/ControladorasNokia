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
# Lista para almacenar resultados de comandos ejecutados
resultados_comandos = []

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
            print(f"        ❌ Timeout ({timeout}s) - comando puede no haber completado")
            return buff
        
        time.sleep(0.2) 

def ejecutar_comandos_mantenimiento(channel, ip, nombre, output_file_comandos):
    """
    Ejecuta la serie de comandos de mantenimiento en equipos sin fallas
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
        'Estado_Ejecucion': 'Exitoso',
        'Comandos_Ejecutados': [],
        'Errores': [],
        'Sincronizacion_Exitosa': False,
        'Detalle_Sincronizacion': 'No verificado'
    }
    
    print(f"    🔧 Ejecutando comandos de mantenimiento...")
    
    # Escribir cabecera en archivo de comandos
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
            time.sleep(0.5)  # Pequeña pausa para que el comando se registre
            
            # Esperar a que se complete con timeout específico por comando
            timeout_por_comando = {
                "admin save": 120,
                "admin save index detail": 120,
                "admin redundancy synchronize config": 180,
                "admin redundancy synchronize boot-env": 600,  # Este puede tomar más tiempo
                "show redundancy synchronization": 60
            }
            
            timeout = timeout_por_comando.get(comando, 180)
            salida = esperar_comando_completado(channel, comando, timeout)
            
            # Guardar salida en archivo de comandos
            output_file_comandos.write(f"--- COMANDO: {comando} ---\n")
            output_file_comandos.write(salida)
            output_file_comandos.write(f"\n{'-'*40}\n\n")
            output_file_comandos.flush()
            
            # Verificar si el comando se ejecutó correctamente
            comando_exitoso = False
            
            if any(cmd in comando.lower() for cmd in ['admin save', 'admin redundancy']):
                # Para comandos de administración, verificar "Completed."
                if "Completed." in salida:
                    comando_exitoso = True
                elif "Error" in salida or "Failed" in salida:
                    comando_exitoso = False
                else:
                    # Si no hay "Completed." pero tampoco error explícito, asumir éxito si hay prompt
                    if re.search(r'[A-Z]:[^#]*#\s*$', salida):
                        comando_exitoso = True
            else:
                # Para show commands, verificar que hay salida y prompt
                if re.search(r'[A-Z]:[^#]*#\s*$', salida) and len(salida.strip()) > 50:
                    comando_exitoso = True
                    
                    # Verificar sincronización si es el último comando
                    if comando == "show redundancy synchronization":
                        if verificar_sincronizacion_exitosa(salida):
                            resultado_comandos['Sincronizacion_Exitosa'] = True
                            resultado_comandos['Detalle_Sincronizacion'] = 'All boot environment synchronized - OK'
                            print(f"    ✅ Sincronización verificada exitosamente")
                        else:
                            resultado_comandos['Sincronizacion_Exitosa'] = False
                            resultado_comandos['Detalle_Sincronizacion'] = 'No se encontró mensaje de sincronización exitosa'
                            resultado_comandos['Errores'].append('Sincronización no completada correctamente')
                            print(f"    ⚠️ Sincronización NO verificada - falta mensaje de confirmación")
            
            if comando_exitoso:
                resultado_comandos['Comandos_Ejecutados'].append(comando)
                print(f"    ✅ {comando} - Completado exitosamente")
            else:
                error_msg = f"Comando '{comando}' no completó correctamente o tuvo errores"
                resultado_comandos['Errores'].append(error_msg)
                print(f"    ❌ {comando} - Error o no completado")
                
        except Exception as e:
            error_msg = f"Excepción ejecutando '{comando}': {str(e)}"
            resultado_comandos['Errores'].append(error_msg)
            print(f"    ❌ {comando} - Excepción: {e}")
            
            # También guardar el error en el archivo
            output_file_comandos.write(f"--- ERROR EN COMANDO: {comando} ---\n")
            output_file_comandos.write(f"Error: {str(e)}\n")
            output_file_comandos.write(f"{'-'*40}\n\n")
            output_file_comandos.flush()
            
        # Pausa entre comandos
        time.sleep(1)
    
    # Determinar estado final
    if len(resultado_comandos['Errores']) > 0:
        resultado_comandos['Estado_Ejecucion'] = f"Con errores ({len(resultado_comandos['Errores'])} errores)"
    elif not resultado_comandos['Sincronizacion_Exitosa']:
        resultado_comandos['Estado_Ejecucion'] = 'Comandos OK - Sincronización no verificada'
    
    return resultado_comandos

def conectar_a_equipo(client, ip, password, timeout=60):
    """
    Establece conexión SSH a un equipo específico a través del jumphost
    Retorna (channel, connected) donde connected es True si la conexión fue exitosa
    """
    # Abrir un canal fresco
    channel = client.invoke_shell()
    time.sleep(0.5)
    
    # Limpiar buffer inicial
    while channel.recv_ready():
        channel.recv(2048)

    # Lanzar el ssh al equipo
    channel.send(f"ssh {ip}\n")
    buff = ""
    start = time.time()
    connected = False

    while True:
        if channel.recv_ready():
            resp = channel.recv(2048).decode('utf-8', errors='ignore')
            buff += resp

            # Aceptar autenticidad del host
            if "Are you sure you want to continue connecting" in buff:
                channel.send("yes\n")
                buff = ""
                continue

            # Enviar contraseña
            if re.search(r"[Pp]assword:", buff):
                channel.send(password + "\n")
                buff = ""
                continue

            # Detectar prompt del equipo (termina en '#')
            if re.search(r".+#\s*$", buff):
                connected = True
                break

        # Timeout
        if time.time() - start > timeout:
            break

    return channel, connected

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

def procesar_equipo_completo(client, ip, nombre, password, output_file, output_file_comandos):
    """
    Procesa un equipo completamente: show card + comandos de mantenimiento si no hay fallas
    Retorna (tiene_fallas, fallas_detectadas, resultado_mantenimiento)
    """
    print(f"\n→ {nombre} ({ip}):", end=' ')
    
    # 1. Conectar al equipo
    channel, connected = conectar_a_equipo(client, ip, password)
    
    if not connected:
        print("✖ Timeout de conexión")
        cerrar_conexion_equipo(channel)
        return True, [], None  # Tratamos timeout como "tiene fallas" para no hacer mantenimiento
    
    print("✔ Conectado")
    
    try:
        # 2. Configurar entorno (desactivar paginación)
        channel.send("/environment no more\n")
        time.sleep(1)
        # Limpiar buffer tras el comando
        while channel.recv_ready():
            channel.recv(2048)

        # 3. Ejecutar show card
        channel.send("show card\n")
        salida = esperar_comando_completado(channel, "show card", timeout=30)

        # Guardar en archivo
        output_file.write(f"==== {nombre} ({ip}) ====\n{salida}\n\n")
        output_file.flush()  # Forzar escritura inmediata

        # 4. Analizar la salida para detectar fallas
        fallas_detectadas = analizar_show_card(salida, ip, nombre)
        tiene_fallas = len(fallas_detectadas) > 0
        
        # 5. Detectar si es Huawei
        if "Unrecognized command found at '^'" in salida:
            print("⚠ Huawei - comando no reconocido")
            tiene_fallas = True  # No hacer mantenimiento en equipos Huawei
        else:
            print("✔ show card ejecutado")

        if tiene_fallas:
            print(f"⚠ {len(fallas_detectadas)} tarjeta(s) con falla detectada(s)")
            return True, fallas_detectadas, None
        else:
            print("✅ Sin fallas detectadas")
            
            # 6. Ejecutar comandos de mantenimiento inmediatamente
            print(f"🔧 Iniciando mantenimiento...")
            resultado_mantenimiento = ejecutar_comandos_mantenimiento(channel, ip, nombre, output_file_comandos)
            print(f"✅ Mantenimiento completado - {resultado_mantenimiento['Estado_Ejecucion']}")
            
            return False, [], resultado_mantenimiento
            
    except Exception as e:
        print(f"❌ Error durante procesamiento: {e}")
        return True, [], None
    
    finally:
        # 7. Cerrar conexión inmediatamente
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

print("\n" + "="*70)
print("PROCESAMIENTO COMPLETO POR EQUIPO (show card + mantenimiento)")
print("="*70)

# Procesar cada equipo completamente antes de pasar al siguiente
for idx, row in df.iterrows():
    ip = row['Ip']
    nombre = row['Nombre']
    
    # Procesar equipo completo
    tiene_fallas, fallas_detectadas, resultado_mantenimiento = procesar_equipo_completo(
        client, ip, nombre, password, output_file, output_file_comandos
    )
    
    # Almacenar resultados
    if tiene_fallas and fallas_detectadas:
        equipos_con_fallas.extend(fallas_detectadas)
    
    if resultado_mantenimiento:
        resultados_comandos.append(resultado_mantenimiento)
    
    # Pausa pequeña entre equipos para no saturar el jumphost
    time.sleep(0.5)

# Cerrar archivos y conexión
output_file.close()
output_file_comandos.close()
client.close()

# Generar reporte unificado en Excel
print("\n" + "="*60)
print("GENERACIÓN DE REPORTE UNIFICADO")
print("="*60)

nombre_reporte_final = f'reporte_completo_{timestamp}.xlsx'

# Crear el archivo Excel con múltiples hojas
with pd.ExcelWriter(nombre_reporte_final, engine='openpyxl') as writer:
    
    # Hoja 1: Resumen General
    resumen_data = []
    total_equipos = len(df)
    equipos_con_fallas_count = len(set(falla['Ip'] for falla in equipos_con_fallas))
    equipos_mantenimiento = len(resultados_comandos)
    equipos_sin_procesar = total_equipos - equipos_con_fallas_count - equipos_mantenimiento
    
    resumen_data.append(['Total de equipos procesados', total_equipos])
    resumen_data.append(['Equipos con fallas en tarjetas', equipos_con_fallas_count])
    resumen_data.append(['Equipos con mantenimiento ejecutado', equipos_mantenimiento])
    resumen_data.append(['Equipos sin procesar (timeouts/errores)', equipos_sin_procesar])
    resumen_data.append(['Total de tarjetas con falla', len(equipos_con_fallas)])
    
    # Resumen de sincronización
    sync_exitosas = len([r for r in resultados_comandos if r['Sincronizacion_Exitosa']])
    sync_fallidas = len([r for r in resultados_comandos if not r['Sincronizacion_Exitosa']])
    
    resumen_data.append(['Sincronizaciones exitosas', sync_exitosas])
    resumen_data.append(['Sincronizaciones fallidas', sync_fallidas])
    resumen_data.append(['Fecha de ejecución', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    
    df_resumen = pd.DataFrame(resumen_data, columns=['Métrica', 'Valor'])
    df_resumen.to_excel(writer, sheet_name='Resumen General', index=False)
    
    # Ajustar ancho de columnas para resumen
    worksheet_resumen = writer.sheets['Resumen General']
    worksheet_resumen.column_dimensions['A'].width = 35
    worksheet_resumen.column_dimensions['B'].width = 15
    
    # Hoja 2: Tarjetas en Falla (si hay fallas)
    if equipos_con_fallas:
        df_fallas = pd.DataFrame(equipos_con_fallas)
        df_fallas.to_excel(writer, sheet_name='Tarjetas en Falla', index=False)
        
        # Ajustar ancho de columnas
        worksheet_fallas = writer.sheets['Tarjetas en Falla']
        for column in worksheet_fallas.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet_fallas.column_dimensions[column_letter].width = adjusted_width
    
    # Hoja 3: Comandos de Mantenimiento (si se ejecutaron)
    if resultados_comandos:
        # Preparar datos para Excel
        datos_excel = []
        for resultado in resultados_comandos:
            datos_excel.append({
                'Nombre': resultado['Nombre'],
                'IP': resultado['Ip'],
                'Estado_Ejecucion': resultado['Estado_Ejecucion'],
                'Comandos_Exitosos': len(resultado['Comandos_Ejecutados']),
                'Total_Errores': len(resultado['Errores']),
                'Sincronizacion_Exitosa': 'SÍ' if resultado['Sincronizacion_Exitosa'] else 'NO',
                'Detalle_Sincronizacion': resultado['Detalle_Sincronizacion'],
                'Comandos_Ejecutados': ' | '.join(resultado['Comandos_Ejecutados']),
                'Errores_Detectados': ' | '.join(resultado['Errores']) if resultado['Errores'] else 'Ninguno'
            })
        
        df_mantenimiento = pd.DataFrame(datos_excel)
        df_mantenimiento.to_excel(writer, sheet_name='Comandos Mantenimiento', index=False)
        
        # Ajustar ancho de columnas
        worksheet_mantenimiento = writer.sheets['Comandos Mantenimiento']
        for column in worksheet_mantenimiento.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 60)
            worksheet_mantenimiento.column_dimensions[column_letter].width = adjusted_width

print(f"📊 Reporte completo generalizado: {nombre_reporte_final}")

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