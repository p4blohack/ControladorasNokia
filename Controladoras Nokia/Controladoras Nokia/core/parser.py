import re
from typing import Dict, List
from utils.log import get_logger

logger = get_logger(__name__)

class CommandParser:
    """Parser para comandos y salidas de equipos Nokia"""
    
    def is_nokia_equipment(self, show_card_output: str) -> bool:
        """Verificar si el equipo es Nokia basado en salida de show card"""
        # Patrones típicos de salida Nokia
        nokia_patterns = [
            r'Card Summary',
            r'sfm2-200g',
            r'iom-20g-b',
            r'iom3-xp',
            r'Provisioned Type',
            r'up/active',
            r'up/standby'
        ]
        
        # Si encuentra patrones Nokia, es Nokia
        for pattern in nokia_patterns:
            if re.search(pattern, show_card_output, re.IGNORECASE):
                return True
        
        # Patrones que indican que NO es Nokia
        non_nokia_patterns = [
            r'invalid command',
            r'Unknown command',
            r'command not found',
            r'syntax error'
        ]
        
        for pattern in non_nokia_patterns:
            if re.search(pattern, show_card_output, re.IGNORECASE):
                return False
        
        # Si no se encuentra ningún patrón definitivo, asumir que no es Nokia
        return False
    
    def check_controllers_status(self, show_card_output: str) -> bool:
        """Verificar que las controladoras A y B estén up"""
        try:
            lines = show_card_output.split('\n')
            
            controller_a_up = False
            controller_b_up = False
            
            for line in lines:
                line = line.strip()
                
                # Buscar líneas que contengan información de las controladoras
                if re.search(r'^A\s+', line):
                    # Controladora A
                    if 'up' in line and ('up/active' in line or 'up/standby' in line):
                        controller_a_up = True
                
                elif re.search(r'^B\s+', line):
                    # Controladora B  
                    if 'up' in line and ('up/active' in line or 'up/standby' in line):
                        controller_b_up = True
            
            result = controller_a_up and controller_b_up
            logger.debug(f"Controladoras A:{controller_a_up}, B:{controller_b_up} - OK:{result}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error verificando estado de controladoras: {e}")
            return False
    
    def check_sync_status(self, sync_output: str) -> bool:
        """Verificar estado de sincronización"""
        try:
            # Buscar línea específica que indica sincronización exitosa
            target_line = "Boot/Config Sync Status      : All boot environment synchronized"
            
            # También buscar variaciones de la línea
            sync_patterns = [
                r'Boot/Config Sync Status\s*:\s*All boot environment synchronized',
                r'All boot environment synchronized',
                r'Boot.*Config.*Sync.*Status.*All.*synchronized'
            ]
            
            for pattern in sync_patterns:
                if re.search(pattern, sync_output, re.IGNORECASE):
                    logger.debug("Sincronización exitosa detectada")
                    return True
            
            logger.debug("Sincronización NO exitosa")
            return False
            
        except Exception as e:
            logger.error(f"Error verificando sincronización: {e}")
            return False
    
    def extract_error_info(self, output: str) -> Dict[str, str]:
        """Extraer información de error de la salida"""
        error_info = {
            'type': 'unknown',
            'description': 'Error no especificado'
        }
        
        error_patterns = {
            'ssh_failed': [r'Connection refused', r'No route to host', r'Connection timed out'],
            'auth_failed': [r'Authentication failed', r'Permission denied', r'Access denied'],
            'invalid_command': [r'invalid command', r'Unknown command', r'command not found'],
            'timeout': [r'timeout', r'timed out'],
            'controller_down': [r'down/standby', r'not equipped', r'failed']
        }
        
        for error_type, patterns in error_patterns.items():
            for pattern in patterns:
                if re.search(pattern, output, re.IGNORECASE):
                    error_info['type'] = error_type
                    error_info['description'] = f"Error tipo {error_type}"
                    break
        
        return error_info