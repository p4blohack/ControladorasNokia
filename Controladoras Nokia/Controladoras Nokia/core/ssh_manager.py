import asyncio
import asyncssh
from typing import Optional
from utils.log import get_logger

logger = get_logger(__name__)

class SSHManager:
    """Gestor de conexiones SSH asíncronas"""
    
    def __init__(self, max_concurrent: int = 50):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
    async def connect_to_hendrix(self, credentials: dict) -> asyncssh.SSHClientConnection:
        """Conectar al servidor Hendrix"""
        try:
            connection = await asyncssh.connect(
                credentials['server'],
                username=credentials['username'],
                password=credentials['password'],
                known_hosts=None,  # Ignorar verificación de host
                server_host_key_algs=['ssh-rsa', 'ssh-dss'],
                encryption_algs=['aes128-ctr', 'aes192-ctr', 'aes256-ctr'],
                connect_timeout=30
            )
            logger.debug(f"Conectado al servidor Hendrix: {credentials['server']}")
            return connection
            
        except Exception as e:
            logger.error(f"Error conectando a Hendrix: {e}")
            raise ConnectionError(f"No se pudo conectar al servidor Hendrix: {str(e)}")
    
    async def connect_to_equipment(
        self, 
        hendrix_conn: asyncssh.SSHClientConnection, 
        equipment_ip: str, 
        password: str
    ) -> asyncssh.SSHClientConnection:
        """Conectar a equipo desde servidor Hendrix"""
        try:
            # Comando SSH para conectar al equipo
            ssh_command = f"ssh {equipment_ip}"
            
            # Crear conexión SSH anidada
            process = await hendrix_conn.create_process(ssh_command)
            
            # Esperar prompt de confirmación
            output = await process.stdout.read(1024)
            output_str = output.decode('utf-8', errors='ignore')
            
            # Responder automáticamente "yes" si aparece el prompt
            if "Are you sure you want to continue connecting" in output_str:
                process.stdin.write("yes\n")
                output = await process.stdout.read(1024)
                output_str = output.decode('utf-8', errors='ignore')
            
            # Enviar contraseña si se solicita
            if "Password:" in output_str or "password:" in output_str:
                process.stdin.write(f"{password}\n")
                await asyncio.sleep(2)  # Esperar autenticación
            
            # Verificar conexión exitosa
            test_output = await self.execute_command_process(process, "show card")
            
            if "invalid command" in test_output.lower() or "error" in test_output.lower():
                if "show card" not in test_output.lower():
                    # Posible equipo Huawei
                    await process.terminate()
                    raise Exception("Equipo no compatible (posible Huawei)")
            
            logger.debug(f"Conectado al equipo: {equipment_ip}")
            return process
            
        except Exception as e:
            logger.error(f"Error conectando al equipo {equipment_ip}: {e}")
            raise ConnectionError(f"No se pudo conectar al equipo {equipment_ip}: {str(e)}")
    
    async def execute_command(self, connection, command: str, timeout: int = 30) -> str:
        """Ejecutar comando en conexión SSH"""
        if hasattr(connection, 'run'):
            # Conexión directa
            result = await connection.run(command, timeout=timeout)
            return result.stdout
        else:
            # Proceso SSH anidado
            return await self.execute_command_process(connection, command, timeout)
    
    async def execute_command_process(self, process, command: str, timeout: int = 30) -> str:
        """Ejecutar comando en proceso SSH"""
        try:
            # Enviar comando
            process.stdin.write(f"{command}\n")
            await process.stdin.drain()
            
            # Leer respuesta con timeout
            output = ""
            start_time = asyncio.get_event_loop().time()
            
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(1024), timeout=1.0
                    )
                    if chunk:
                        output += chunk.decode('utf-8', errors='ignore')
                        
                        # Buscar prompt que indica fin del comando
                        if any(prompt in output for prompt in ['#', '>', '$']):
                            break
                    
                    # Timeout check
                    if asyncio.get_event_loop().time() - start_time > timeout:
                        break
                        
                except asyncio.TimeoutError:
                    # No hay más datos, asumir que terminó
                    break
            
            return output
            
        except Exception as e:
            logger.error(f"Error ejecutando comando '{command}': {e}")
            raise
    
    async def disconnect(self, connection):
        """Cerrar conexión SSH"""
        try:
            if hasattr(connection, 'close'):
                connection.close()
            elif hasattr(connection, 'terminate'):
                await connection.terminate()
            logger.debug("Conexión SSH cerrada")
        except Exception as e:
            logger.warning(f"Error cerrando conexión: {e}")