import json
from pathlib import Path
from typing import Dict, List
from utils.log import get_logger

logger = get_logger(__name__)

def load_equipment_list(file_path: str = None) -> Dict[str, List[Dict]]:
    """Cargar lista de equipos desde archivo JSON"""
    
    if file_path is None:
        file_path = Path(__file__).parent / "equipment_list.json"
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            equipment_data = json.load(f)
        
        logger.info(f"Lista de equipos cargada: {file_path}")
        return equipment_data
        
    except FileNotFoundError:
        logger.warning(f"Archivo no encontrado: {file_path}")
        return create_sample_equipment_list(file_path)
    except Exception as e:
        logger.error(f"Error cargando lista de equipos: {e}")
        raise

def create_sample_equipment_list(file_path: str) -> Dict[str, List[Dict]]:
    """Crear archivo de ejemplo con lista de equipos"""
    sample_data = {
        "LOW RAN": [
            {"ip": "192.168.1.10", "name": "NOK-LOW-001", "location": "Site A"},
            {"ip": "192.168.1.11", "name": "NOK-LOW-002", "location": "Site B"},
            {"ip": "192.168.1.12", "name": "NOK-LOW-003", "location": "Site C"},
            {"ip": "192.168.1.13", "name": "NOK-LOW-004", "location": "Site D"},
            {"ip": "192.168.1.14", "name": "NOK-LOW-005", "location": "Site E"}
        ],
        "MIDDLE RAN": [
            {"ip": "192.168.2.10", "name": "NOK-MID-001", "location": "Region A"},
            {"ip": "192.168.2.11", "name": "NOK-MID-002", "location": "Region B"},
            {"ip": "192.168.2.12", "name": "NOK-MID-003", "location": "Region C"},
            {"ip": "192.168.2.13", "name": "NOK-MID-004", "location": "Region D"},
            {"ip": "192.168.2.14", "name": "NOK-MID-005", "location": "Region E"}
        ],
        "HIGH RAN": [
            {"ip": "192.168.3.10", "name": "NOK-HIGH-001", "location": "Core A"},
            {"ip": "192.168.3.11", "name": "NOK-HIGH-002", "location": "Core B"},
            {"ip": "192.168.3.12", "name": "NOK-HIGH-003", "location": "Core C"},
            {"ip": "192.168.3.13", "name": "NOK-HIGH-004", "location": "Core D"},
            {"ip": "192.168.3.14", "name": "NOK-HIGH-005", "location": "Core E"}
        ]
    }
    
    try:
        # Crear directorio si no existe
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Guardar archivo de ejemplo
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(sample_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Archivo de ejemplo creado: {file_path}")
        return sample_data
        
    except Exception as e:
        logger.error(f"Error creando archivo de ejemplo: {e}")
        raise

# Configuración de constantes
CONFIG = {
    'SSH_TIMEOUT': 30,
    'MAX_CONCURRENT_CONNECTIONS': 50,
    'COMMAND_DELAY': 1,  # Segundos entre comandos
    'CONNECTION_RETRY_ATTEMPTS': 3,
    'LOG_LEVEL': 'INFO'
}
