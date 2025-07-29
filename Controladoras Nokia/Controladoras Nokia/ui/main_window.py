import asyncio
import json
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QProgressBar, QTextEdit, QLabel,
    QLineEdit, QComboBox, QGroupBox, QFormLayout,
    QMessageBox, QFileDialog
)
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont
from core.controller import NetworkController
from data.config import load_equipment_list
from utils.log import get_logger

logger = get_logger(__name__)

class SyncWorker(QThread):
    """Worker thread para ejecutar sincronización sin bloquear UI"""
    progress_updated = Signal(int, int)  # actual, total
    log_updated = Signal(str)
    finished = Signal(bool, str)  # success, message
    
    def __init__(self, controller, group, credentials):
        super().__init__()
        self.controller = controller
        self.group = group
        self.credentials = credentials
        
    def run(self):
        """Ejecutar proceso de sincronización"""
        try:
            # Configurar event loop para este thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Ejecutar sincronización
            result = loop.run_until_complete(
                self.controller.sync_equipment_group(
                    self.group, 
                    self.credentials,
                    progress_callback=self.progress_updated.emit,
                    log_callback=self.log_updated.emit
                )
            )
            
            self.finished.emit(True, f"Sincronización completada. {result['total']} equipos procesados.")
            
        except Exception as e:
            logger.error(f"Error en sincronización: {e}")
            self.finished.emit(False, f"Error: {str(e)}")
        finally:
            loop.close()

class MainWindow(QMainWindow):
    """Ventana principal de la aplicación"""
    
    def __init__(self):
        super().__init__()
        self.controller = NetworkController()
        self.worker = None
        self.setup_ui()
        
    def setup_ui(self):
        """Configurar interfaz de usuario"""
        self.setWindowTitle("Nokia Controller Sync - v1.0")
        self.setMinimumSize(800, 600)
        
        # Widget central
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Título
        title = QLabel("Sincronización de Controladoras Nokia")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # Grupo de credenciales
        cred_group = QGroupBox("Credenciales SSH")
        cred_layout = QFormLayout(cred_group)
        
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.server_input = QLineEdit("hendrix_server_ip")
        
        cred_layout.addRow("Usuario:", self.username_input)
        cred_layout.addRow("Contraseña:", self.password_input)
        cred_layout.addRow("Servidor Hendrix:", self.server_input)
        
        layout.addWidget(cred_group)
        
        # Grupo de selección
        select_group = QGroupBox("Selección de Equipos")
        select_layout = QHBoxLayout(select_group)
        
        self.group_combo = QComboBox()
        self.group_combo.addItems(["LOW RAN", "MIDDLE RAN", "HIGH RAN", "TODOS"])
        
        self.load_file_btn = QPushButton("Cargar Lista de Equipos")
        self.load_file_btn.clicked.connect(self.load_equipment_file)
        
        select_layout.addWidget(QLabel("Grupo:"))
        select_layout.addWidget(self.group_combo)
        select_layout.addWidget(self.load_file_btn)
        
        layout.addWidget(select_group)
        
        # Botones de control
        control_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("Iniciar Sincronización")
        self.start_btn.clicked.connect(self.start_sync)
        self.start_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        
        self.stop_btn = QPushButton("Detener")
        self.stop_btn.clicked.connect(self.stop_sync)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton { background-color: #f44336; color: white; }")
        
        self.report_btn = QPushButton("Ver Reporte")
        self.report_btn.clicked.connect(self.open_report)
        
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.report_btn)
        
        layout.addLayout(control_layout)
        
        # Barra de progreso
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("Listo para iniciar")
        
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        
        # Log de actividad
        log_group = QGroupBox("Log de Actividad")
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)
        
    def load_equipment_file(self):
        """Cargar archivo de lista de equipos"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Cargar Lista de Equipos", "", "JSON files (*.json)"
        )
        
        if file_path:
            try:
                self.controller.load_equipment_list(file_path)
                self.log_message(f"Lista de equipos cargada: {file_path}")
                QMessageBox.information(self, "Éxito", "Lista de equipos cargada correctamente")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error al cargar archivo: {str(e)}")
    
    def start_sync(self):
        """Iniciar proceso de sincronización"""
        # Validar credenciales
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        server = self.server_input.text().strip()
        
        if not all([username, password, server]):
            QMessageBox.warning(self, "Error", "Por favor complete todas las credenciales")
            return
            
        # Preparar credenciales
        credentials = {
            'username': username,
            'password': password,
            'server': server
        }
        
        # Obtener grupo seleccionado
        group = self.group_combo.currentText()
        
        # Deshabilitar botones
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        # Limpiar log
        self.log_text.clear()
        
        # Iniciar worker
        self.worker = SyncWorker(self.controller, group, credentials)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.log_updated.connect(self.log_message)
        self.worker.finished.connect(self.sync_finished)
        self.worker.start()
        
        self.log_message(f"Iniciando sincronización del grupo: {group}")
    
    def stop_sync(self):
        """Detener proceso de sincronización"""
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
            
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log_message("Proceso detenido por el usuario")
    
    def update_progress(self, current, total):
        """Actualizar barra de progreso"""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Procesando: {current}/{total} equipos")
    
    def log_message(self, message):
        """Agregar mensaje al log"""
        self.log_text.append(f"[{asyncio.get_event_loop().time():.2f}] {message}")
        self.log_text.ensureCursorVisible()
    
    def sync_finished(self, success, message):
        """Proceso de sincronización terminado"""
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        if success:
            self.log_message(f"✅ {message}")
            QMessageBox.information(self, "Completado", message)
        else:
            self.log_message(f"❌ {message}")
            QMessageBox.critical(self, "Error", message)
    
    def open_report(self):
        """Abrir reporte generado"""
        report_path = Path("reports/sync_report.xlsx")
        if report_path.exists():
            import os
            os.startfile(str(report_path))
        else:
            QMessageBox.information(self, "Info", "No hay reportes disponibles")