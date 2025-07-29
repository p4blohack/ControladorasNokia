import sys
import asyncio
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from ui.main_window import MainWindow
from utils.log import setup_logging

def main():
    """Función principal de la aplicación"""
    # Configurar logging
    setup_logging()
    
    # Crear aplicación Qt
    app = QApplication(sys.argv)
    
    # Crear ventana principal
    window = MainWindow()
    window.show()
    
    # Configurar event loop para asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Timer para integrar asyncio con Qt
    timer = QTimer()
    timer.timeout.connect(lambda: loop.run_until_complete(asyncio.sleep(0.01)))
    timer.start(10)
    
    # Ejecutar aplicación
    try:
        sys.exit(app.exec())
    finally:
        loop.close()

if __name__ == "__main__":
    main()