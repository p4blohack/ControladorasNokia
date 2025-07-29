import json
from pathlib import Path
from typing import Set, List
from utils.log import get_logger

logger = get_logger(__name__)

class DiscardedIPsStorage:
    """Gestor de almacenamiento para IPs descartadas (equipos Huawei)"""
    
    def __init__(self, storage_file: str = "data/discarded_ips.json"):
        self.storage_file = Path(storage_file)
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        self._discarded_ips = self._load_discarded_ips()
    
    def _load_discarded_ips(self) -> Set[str]:
        """Cargar IPs descartadas desde archivo"""
        try:
            if self.storage_file.exists():
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get('discarded_ips', []))
            return set()
        except Exception as e:
            logger.warning(f"Error cargando IPs descartadas: {e}")
            return set()
    
    def _save_discarded_ips(self):
        """Guardar IPs descartadas en archivo"""
        try:
            data = {
                'discarded_ips': list(self._discarded_ips),
                'last_updated': str(Path(__file__).stat().st_mtime)
            }
            
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logger.error(f"Error guardando IPs descartadas: {e}")
    
    def add_discarded_ip(self, ip: str):
        """Agregar IP a la lista de descartadas"""
        if ip not in self._discarded_ips:
            self._discarded_ips.add(ip)
            self._save_discarded_ips()
            logger.info(f"IP {ip} agregada a lista de descartadas")
    
    def remove_discarded_ip(self, ip: str):
        """Remover IP de la lista de descartadas"""
        if ip in self._discarded_ips:
            self._discarded_ips.remove(ip)
            self._save_discarded_ips()
            logger.info(f"IP {ip} removida de lista de descartadas")
    
    def get_discarded_ips(self) -> Set[str]:
        """Obtener conjunto de IPs descartadas"""
        return self._discarded_ips.copy()
    
    def is_discarded(self, ip: str) -> bool:
        """Verificar si una IP está descartada"""
        return ip in self._discarded_ips
    
    def clear_discarded_ips(self):
        """Limpiar todas las IPs descartadas"""
        self._discarded_ips.clear()
        self._save_discarded_ips()
        logger.info("Lista de IPs descartadas limpiada")
    
    def get_discarded_count(self) -> int:
        """Obtener número de IPs descartadas"""
        return len(self._discarded_ips)