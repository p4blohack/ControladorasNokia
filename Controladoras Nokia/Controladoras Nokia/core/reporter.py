import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict
from utils.log import get_logger

logger = get_logger(__name__)

class ReportGenerator:
    """Generador de reportes de sincronización"""
    
    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(exist_ok=True)
    
    async def generate_report(self, failed_results: List[Dict], group: str) -> str:
        """Generar reporte Excel con equipos que fallaron"""
        try:
            # Preparar datos para el reporte
            report_data = []
            
            for result in failed_results:
                report_data.append({
                    'IP': result['ip'],
                    'Nombre del Equipo': result['name'],
                    'Estado': result['status'],
                    'Error': result['error'],
                    'Fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'Grupo': group
                })
            
            # Crear DataFrame
            df = pd.DataFrame(report_data)
            
            # Nombre del archivo
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"sync_report_{group.replace(' ', '_')}_{timestamp}.xlsx"
            filepath = self.reports_dir / filename
            
            # Generar archivo Excel con formato
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Equipos con Errores', index=False)
                
                # Obtener worksheet para formato
                worksheet = writer.sheets['Equipos con Errores']
                
                # Ajustar ancho de columnas
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
                
                # Aplicar formato a encabezados
                from openpyxl.styles import Font, PatternFill
                
                for cell in worksheet[1]:
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill(start_color='366092', end_color='366092', fill_type='solid')
            
            logger.info(f"Reporte generado: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Error generando reporte: {e}")
            raise
    
    def generate_summary_report(self, results_summary: Dict, group: str) -> str:
        """Generar reporte resumen"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            summary_data = {
                'Grupo': [group],
                'Total Equipos': [results_summary['total']],
                'Exitosos': [results_summary['success']],
                'Con Errores': [results_summary['errors']],
                'Procesados': [results_summary['processed']],
                'Fecha': [timestamp]
            }
            
            df_summary = pd.DataFrame(summary_data)
            
            timestamp_file = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"summary_{group.replace(' ', '_')}_{timestamp_file}.xlsx"
            filepath = self.reports_dir / filename
            
            df_summary.to_excel(filepath, sheet_name='Resumen', index=False)
            
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Error generando reporte resumen: {e}")
            raise
