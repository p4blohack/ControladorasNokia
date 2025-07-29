import asyncio
import json
from pathlib import Path
from typing import Dict, List, Callable, Optional
from core.ssh_manager import SSHManager
from core.parser import CommandParser
from core.reporter import ReportGenerator
from data.storage import DiscardedIPsStorage
from utils.log import get_logger

logger = get_logger(__name__)

class NetworkController:
    """Controlador principal para sincronización de equipos"""
    
    def __init__(self, max_concurrent=50):
        self.ssh_manager = SSHManager(max_concurrent)
        self.parser = CommandParser()
        self.reporter = ReportGenerator()
        self.storage = DiscardedIPsStorage()
        self.equipment_list = {}
        
    def load_equipment_list(self, file_path: str):
        """Cargar lista de equipos desde archivo JSON"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self.equipment_list = json.load(f)
            logger.info(f"Lista de equipos cargada: {len(self.equipment_list)} grupos")
        except Exception as e:
            logger.error(f"Error cargando lista de equipos: {e}")
            raise
    
    async def sync_equipment_group(
        self, 
        group: str, 
        credentials: Dict,
        progress_callback: Optional[Callable] = None,
        log_callback: Optional[Callable] = None
    ) -> Dict:
        """Sincronizar grupo de equipos"""
        
        def log(message):
            if log_callback:
                log_callback(message)
            logger.info(message)
        
        # Obtener lista de equipos
        if group == "TODOS":
            equipment = []
            for group_name in ["LOW RAN", "MIDDLE RAN", "HIGH RAN"]:
                equipment.extend(self.equipment_list.get(group_name, []))
        else:
            equipment = self.equipment_list.get(group, [])
        
        if not equipment:
            raise ValueError(f"No se encontraron equipos para el grupo: {group}")
        
        log(f"Procesando {len(equipment)} equipos del grupo {group}")
        
        # Filtrar IPs descartadas
        discarded_ips = self.storage.get_discarded_ips()
        filtered_equipment = [eq for eq in equipment if eq['ip'] not in discarded_ips]
        
        log(f"Equipos después de filtrar descartados: {len(filtered_equipment)}")
        
        # Variables de resultado
        results = []
        processed = 0
        total = len(filtered_equipment)
        
        # Procesar equipos en lotes
        semaphore = asyncio.Semaphore(50)  # Máximo 50 conexiones concurrentes
        
        async def process_equipment(equipment_info):
            nonlocal processed
            async with semaphore:
                try:
                    result = await self.process_single_equipment(
                        equipment_info, credentials
                    )
                    results.append(result)
                    
                    processed += 1
                    if progress_callback:
                        progress_callback(processed, total)
                    
                    # Log progreso
                    if result['status'] == 'success':
                        log(f"✅ {equipment_info['name']} ({equipment_info['ip']}) - OK")
                    else:
                        log(f"❌ {equipment_info['name']} ({equipment_info['ip']}) - {result['error']}")
                        
                except Exception as e:
                    logger.error(f"Error procesando {equipment_info['ip']}: {e}")
                    results.append({
                        'ip': equipment_info['ip'],
                        'name': equipment_info['name'],
                        'status': 'error',
                        'error': f"Error inesperado: {str(e)}"
                    })
        
        # Ejecutar procesamiento paralelo
        tasks = [process_equipment(eq) for eq in filtered_equipment]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Generar reporte solo con errores
        failed_results = [r for r in results if r['status'] != 'success']
        
        if failed_results:
            report_path = await self.reporter.generate_report(failed_results, group)
            log(f"Reporte generado: {report_path}")
        else:
            log("No se encontraron errores - No se generó reporte")
        
        return {
            'total': total,
            'processed': processed,
            'errors': len(failed_results),
            'success': total - len(failed_results)
        }
    
    async def process_single_equipment(self, equipment_info: Dict, credentials: Dict) -> Dict:
        """Procesar un solo equipo"""
        ip = equipment_info['ip']
        name = equipment_info['name']
        
        try:
            # Conectar al servidor Hendrix
            connection = await self.ssh_manager.connect_to_hendrix(credentials)
            
            # Conectar al equipo desde Hendrix
            equipment_conn = await self.ssh_manager.connect_to_equipment(
                connection, ip, credentials['password']
            )
            
            # Verificar si es equipo Nokia
            card_output = await self.ssh_manager.execute_command(
                equipment_conn, "show card"
            )
            
            if not self.parser.is_nokia_equipment(card_output):
                # Es Huawei - descartar
                self.storage.add_discarded_ip(ip)
                await self.ssh_manager.disconnect(equipment_conn)
                await self.ssh_manager.disconnect(connection)
                return {
                    'ip': ip, 'name': name, 'status': 'discarded',
                    'error': 'Equipo Huawei - descartado'
                }
            
            # Verificar estado de controladoras
            controllers_ok = self.parser.check_controllers_status(card_output)
            
            if not controllers_ok:
                await self.ssh_manager.disconnect(equipment_conn)
                await self.ssh_manager.disconnect(connection)
                return {
                    'ip': ip, 'name': name, 'status': 'error',
                    'error': 'Controladoras A o B caídas'
                }
            
            # Ejecutar comandos de sincronización
            sync_commands = [
                "admin save",
                "admin save index detail",
                "admin redundancy synchronize config",
                "admin redundancy synchronize boot-env"
            ]
            
            for cmd in sync_commands:
                await self.ssh_manager.execute_command(equipment_conn, cmd)
                await asyncio.sleep(1)  # Pausa entre comandos
            
            # Verificar sincronización
            sync_output = await self.ssh_manager.execute_command(
                equipment_conn, "show redundancy synchronization"
            )
            
            sync_ok = self.parser.check_sync_status(sync_output)
            
            await self.ssh_manager.disconnect(equipment_conn)
            await self.ssh_manager.disconnect(connection)
            
            if sync_ok:
                return {'ip': ip, 'name': name, 'status': 'success'}
            else:
                return {
                    'ip': ip, 'name': name, 'status': 'error',
                    'error': 'Fallo en sincronización'
                }
                
        except Exception as e:
            return {
                'ip': ip, 'name': name, 'status': 'error',
                'error': str(e)
            }
