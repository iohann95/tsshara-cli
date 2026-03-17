#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TSShara CLI - Ferramenta de linha de comando para gerenciar dispositivos nobreak TS Shara
via comunicação serial direta.

Funcionalidades:
- nbid: Gera ID único do hardware (baseado em serial do disco + hostname)
- read: Lê configuração diretamente do nobreak via serial
- write: Escreve configuração diretamente no nobreak via serial
- test: Envia comandos de teste ao nobreak (10s, low, timed, beep, shutdown, status, firmware)
- monitor: Monitora continuamente o nobreak e envia notificações por e-mail
- init-config: Gera arquivo config.ini padrão
- HTTP API: Endpoint REST para apps externos (Android)
"""

import sys
import os
import argparse
import json
import time
import smtplib
import configparser
import logging
import logging.handlers
import threading
import re
import subprocess
import socket
import hashlib
import platform
import base64
import signal
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer


# ============================================================================
# DEPENDÊNCIAS OPCIONAIS
# ============================================================================

COLORAMA_AVAILABLE = False
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init()
    COLORAMA_AVAILABLE = True
except ImportError:
    class Fore:
        RED = GREEN = YELLOW = CYAN = WHITE = MAGENTA = BLUE = RESET = ''
    class Style:
        BRIGHT = DIM = RESET_ALL = NORMAL = ''

SERIAL_AVAILABLE = False
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# CONSTANTES
# ============================================================================

VERSION = "3.0.0"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.ini')

# Logger global (configurado em setup_logging)
logger = logging.getLogger('tsshara')

# Última linha de status registrada no log (para dedup com log_only_changes)
_last_logged_status_line: Optional[str] = None
_log_only_changes: bool = True

# Controle global de cores
USE_COLORS = True


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

DEFAULT_CONFIG_CONTENT = """\
; ============================================================================
; TSShara CLI - Arquivo de Configuração
; ============================================================================
; Edite este arquivo para configurar a ferramenta de monitoramento de nobreak.
; Argumentos de linha de comando sobrepõem estas configurações quando fornecidos.
;
; Gerar este arquivo com valores padrão:  python tsshara-cli.py init-config
; ============================================================================

[serial]
; Porta serial: "auto" para detecção automática, ou especifique ex: COM3, /dev/ttyUSB0
port = auto

; Taxa de comunicação serial (padrão para TS Shara: 2400)
baudrate = 2400

; Timeout de leitura em segundos
timeout = 5.0

; Número de tentativas em caso de falha de comunicação
retries = 5

; Atraso em segundos entre tentativas quando a porta está ocupada
busy_retry_delay = 2

; Número máximo de tentativas quando a porta está ocupada
busy_max_retries = 6


[monitor]
; Intervalo de polling em segundos
poll_interval = 5

; Limite de tensão da bateria para alerta de bateria baixa (volts)
battery_threshold = 11.0

; Intervalo de teste automático. Formatos:
;   Intervalo simples: 30s, 5m, 2h, 1d, 1w
;   Agendamento personalizado:  wd2h8m15  (terça-feira 08:15)
;                                h14m30    (diariamente 14:30)
;                                wd1-5h9m0 (seg-sex 09:00)
;                                md15h12m0 (dia 15 do mês 12:00)
; Deixe vazio para desativar testes automáticos.
test_interval =


[email]
; Ativar notificações por e-mail (true/false)
enabled = false

; Servidor SMTP
smtp_host = smtp.gmail.com

; Porta do servidor SMTP (587 para STARTTLS, 465 para SSL)
smtp_port = 587

; Usuário de autenticação SMTP
smtp_user =

; Senha de autenticação SMTP
smtp_pass =

; Endereço de e-mail do remetente (From)
from_addr =

; Endereços de e-mail dos destinatários (separados por vírgula)
to_addrs =

; Usar STARTTLS (true) ou SSL direto (false)
use_tls = true


[api]
; Ativar endpoint HTTP REST API para apps externos (ex: Android)
enabled = false

; Endereço de escuta da API
;   0.0.0.0   = todas as interfaces (acessível pela rede/internet)
;   127.0.0.1 = apenas localhost
host = 0.0.0.0

; Porta de escuta da API
port = 8080

; Autenticação Basic Auth (opcional)
; Edite diretamente neste arquivo para configurar.
auth_username =
auth_password =


[logging]
; Ativar logging em arquivo (true/false)
enabled = true

; Nível de log: DEBUG, INFO, WARNING, ERROR, CRITICAL
level = INFO

; Diretório dos arquivos de log (relativo ao diretório do script, ou caminho absoluto)
dir = log

; Ativar rotação diária de logs (true/false)
; Quando ativado, à meia-noite o log é rotacionado para tsshara-cli-AAAA-MM-DD.log
; Quando desativado, tudo é gravado em um único arquivo tsshara-cli.log
rotation = true

; Número de arquivos de log diários a manter (ignorado se rotation = false)
backup_count = 7

; Exibir mensagens de log no console (true/false)
; Defina como true apenas se quiser saída no formato de log com timestamp no console
; (ex: ao rodar como serviço sem captura de stdout pelo NSSM).
; Padrão false evita saída duplicada com as mensagens coloridas do console.
console = false

; Registrar no log apenas quando o status mudar (true/false)
; Quando ativado, linhas de status idênticas consecutivas são suprimidas no arquivo de log.
; Eventos como alertas, erros e mudanças de estado sempre são registrados.
log_only_changes = true


[general]
; Ativar saída colorida no terminal (requer colorama)
color = true
"""


class AppConfig:
    """Gerencia configuração da aplicação a partir do config.ini"""

    def __init__(self, config_path: Optional[str] = None):
        self.path = config_path or DEFAULT_CONFIG_PATH
        self._parser = configparser.ConfigParser()
        self._load_defaults()
        if os.path.exists(self.path):
            self._parser.read(self.path, encoding='utf-8')

    def _load_defaults(self):
        """Define valores padrão internos para todas as seções"""
        self._parser['serial'] = {
            'port': 'auto', 'baudrate': '2400', 'timeout': '5.0',
            'retries': '5', 'busy_retry_delay': '2', 'busy_max_retries': '6',
        }
        self._parser['monitor'] = {
            'poll_interval': '5', 'battery_threshold': '11.0', 'test_interval': '',
        }
        self._parser['email'] = {
            'enabled': 'false', 'smtp_host': '', 'smtp_port': '587',
            'smtp_user': '', 'smtp_pass': '', 'from_addr': '', 'to_addrs': '',
            'use_tls': 'true',
        }
        self._parser['api'] = {
            'enabled': 'false', 'host': '0.0.0.0', 'port': '8080',
            'auth_username': '', 'auth_password': '',
        }
        self._parser['logging'] = {
            'enabled': 'true', 'level': 'INFO', 'dir': 'log',
            'rotation': 'true', 'backup_count': '7', 'console': 'false',
        }
        self._parser['general'] = {
            'color': 'true',
        }

    def get(self, section: str, key: str, fallback: str = '') -> str:
        return self._parser.get(section, key, fallback=fallback)

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        return self._parser.getint(section, key, fallback=fallback)

    def getfloat(self, section: str, key: str, fallback: float = 0.0) -> float:
        return self._parser.getfloat(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        return self._parser.getboolean(section, key, fallback=fallback)


# ============================================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """Formatador de log com cores baseadas em colorama por nível"""

    LEVEL_COLORS = {
        logging.DEBUG: (lambda: Style.DIM),
        logging.INFO: (lambda: Fore.CYAN),
        logging.WARNING: (lambda: Fore.YELLOW),
        logging.ERROR: (lambda: Fore.RED),
        logging.CRITICAL: (lambda: Fore.RED + Style.BRIGHT),
    }

    def format(self, record):
        if USE_COLORS and COLORAMA_AVAILABLE:
            color_fn = self.LEVEL_COLORS.get(record.levelno)
            color = color_fn() if color_fn else ''
            reset = Style.RESET_ALL
            record.msg = f"{color}{record.msg}{reset}"
        return super().format(record)


def setup_logging(config: AppConfig):
    """Configura logging baseado nas configurações do arquivo config.ini"""
    global _log_only_changes
    _log_only_changes = config.getboolean('logging', 'log_only_changes', fallback=True)

    log_enabled = config.getboolean('logging', 'enabled', fallback=True)

    if not log_enabled:
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.CRITICAL)
        return

    log_level_str = config.get('logging', 'level', fallback='INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logger.setLevel(log_level)

    file_fmt = logging.Formatter(
        '%(asctime)s [%(levelname)-8s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Handler de arquivo com rotação diária (ou simples)
    log_dir = config.get('logging', 'dir', fallback='log')
    if log_dir:
        if not os.path.isabs(log_dir):
            log_dir = os.path.join(SCRIPT_DIR, log_dir)
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, 'tsshara-cli.log')
        rotation = config.getboolean('logging', 'rotation', fallback=True)
        backup_count = config.getint('logging', 'backup_count', fallback=7)
        try:
            if rotation:
                fh = logging.handlers.TimedRotatingFileHandler(
                    log_file, when='midnight', backupCount=backup_count, encoding='utf-8'
                )
                fh.suffix = "%Y-%m-%d"

                # Renomeia arquivos rotacionados para formato com data:
                # tsshara-cli.log.2025-01-01 → tsshara-cli-2025-01-01.log
                def log_namer(default_name):
                    dir_name = os.path.dirname(default_name)
                    base_name = os.path.basename(default_name)
                    parts = base_name.rsplit('.', 1)
                    if len(parts) == 2 and re.match(r'\d{4}-\d{2}-\d{2}', parts[1]):
                        name_parts = parts[0].rsplit('.', 1)
                        ext = name_parts[1] if len(name_parts) > 1 else 'log'
                        return os.path.join(dir_name, f"{name_parts[0]}-{parts[1]}.{ext}")
                    return default_name

                fh.namer = log_namer
            else:
                fh = logging.FileHandler(log_file, encoding='utf-8')

            fh.setFormatter(file_fmt)
            fh.setLevel(log_level)
            logger.addHandler(fh)
        except Exception as e:
            print(f"AVISO: Não foi possível criar arquivo de log: {e}")

    # Handler de console (opcional, para visibilidade em modo serviço)
    if config.getboolean('logging', 'console', fallback=True):
        console_fmt = ColoredFormatter(
            '%(asctime)s [%(levelname)-8s] %(message)s',
            datefmt='%H:%M:%S'
        )
        ch = logging.StreamHandler()
        ch.setFormatter(console_fmt)
        ch.setLevel(log_level)
        logger.addHandler(ch)


# ============================================================================
# HELPERS DE IMPRESSÃO COLORIDA
# ============================================================================

def _safe_print(msg: str):
    """Print que não falha com caracteres não-suportados pelo encoding do console (ex: cp1252)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', errors='replace').decode('ascii'))


def print_info(msg: str):
    """Imprime mensagem informativa em ciano e registra no log"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        _safe_print(f"{Fore.CYAN}{msg}{Style.RESET_ALL}")
    else:
        _safe_print(msg)
    logger.info(msg)


def print_success(msg: str):
    """Imprime mensagem de sucesso em verde e registra no log"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        _safe_print(f"{Fore.GREEN}{msg}{Style.RESET_ALL}")
    else:
        _safe_print(msg)
    logger.info(msg)


def print_warning(msg: str):
    """Imprime mensagem de aviso em amarelo e registra no log"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        _safe_print(f"{Fore.YELLOW}{msg}{Style.RESET_ALL}")
    else:
        _safe_print(msg)
    logger.warning(msg)


def print_error(msg: str):
    """Imprime mensagem de erro em vermelho e registra no log"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        _safe_print(f"{Fore.RED}{msg}{Style.RESET_ALL}")
    else:
        _safe_print(msg)
    logger.error(msg)


def print_status_line(status: 'NobreakStatus'):
    """Imprime linha de status formatada do monitor com cores por campo"""
    if status.test_in_progress:
        status_text = 'TESTE'
        status_color = Fore.YELLOW
    elif status.utility_fail:
        status_text = 'FALHA ENERGIA'
        status_color = Fore.RED
    elif status.battery_low:
        status_text = 'BATERIA BAIXA'
        status_color = Fore.RED
    else:
        status_text = 'OK'
        status_color = Fore.GREEN

    ts = time.strftime('%H:%M:%S')

    if USE_COLORS and COLORAMA_AVAILABLE:
        print(
            f"{Fore.WHITE}[{ts}] "
            f"{Fore.CYAN}Entrada:{Fore.WHITE} {status.input_voltage:.1f}V "
            f"{Fore.CYAN}| Saída:{Fore.WHITE} {status.output_voltage:.1f}V "
            f"{Fore.CYAN}| Bateria:{Fore.WHITE} {status.battery:.1f}V "
            f"{Fore.CYAN}| Temp:{Fore.WHITE} {status.temperature:.1f}°C "
            f"{Fore.CYAN}| Status: {status_color}{status_text}{Style.RESET_ALL}"
        )
    else:
        print(
            f"[{ts}] "
            f"Entrada: {status.input_voltage:.1f}V | "
            f"Saída: {status.output_voltage:.1f}V | "
            f"Bateria: {status.battery:.1f}V | "
            f"Temp: {status.temperature:.1f}°C | "
            f"Status: {status_text}"
        )

    # Registra no arquivo de log (texto puro, sem cores)
    log_line = (
        f"Entrada: {status.input_voltage:.1f}V | "
        f"Saída: {status.output_voltage:.1f}V | "
        f"Bateria: {status.battery:.1f}V | "
        f"Temp: {status.temperature:.1f}°C | "
        f"Status: {status_text}"
    )
    global _last_logged_status_line
    if not _log_only_changes or log_line != _last_logged_status_line:
        logger.info(log_line)
        _last_logged_status_line = log_line


# ============================================================================
# EXCEÇÕES
# ============================================================================

class SerialError(Exception):
    """Erro relacionado à porta serial"""
    pass


class PortBusyError(SerialError):
    """Porta serial está ocupada por outro processo.
    Lançada quando outra instância, serviço ou programa está usando a porta serial.
    """
    pass


class ValidationError(Exception):
    """Erro de validação de entrada"""
    pass


# ============================================================================
# MODELOS DE DADOS
# ============================================================================

@dataclass
class TestResult:
    """Resultado de comando de teste"""
    comando: str
    sucesso: bool
    resposta: Optional[str] = None
    erro: Optional[str] = None
    porta: Optional[str] = None


@dataclass
class NobreakStatus:
    """Status do nobreak"""
    input_voltage: float
    input_fault_voltage: float
    output_voltage: float
    current: float
    frequency: float
    battery: float
    temperature: float
    utility_fail: bool      # Bit 0
    battery_low: bool       # Bit 1
    bypass_mode: bool       # Bit 2
    ups_failed: bool        # Bit 3
    ups_standby: bool       # Bit 4
    test_in_progress: bool  # Bit 5
    shutdown_active: bool   # Bit 6
    beep_on: bool           # Bit 7
    status_raw: str


# ============================================================================
# GERADOR DE ID DE HARDWARE
# ============================================================================

def get_disk_serial() -> str:
    """Obtém serial do disco"""
    try:
        system = platform.system()

        if system == "Windows":
            try:
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     'Get-PhysicalDisk | Select -ExpandProperty SerialNumber'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    match = re.search(r'[A-Za-z0-9_-]+', result.stdout)
                    if match and match.group(0) != "SerialNumber":
                        return match.group(0).strip()
            except Exception:
                pass

            try:
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     'Get-WmiObject Win32_DiskDrive | Select -ExpandProperty SerialNumber'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    match = re.search(r'[A-Za-z0-9_-]+', result.stdout)
                    if match and match.group(0) != "SerialNumber":
                        return match.group(0).strip()
            except Exception:
                pass

            try:
                result = subprocess.run(['vol', 'C:'], capture_output=True, text=True, shell=True)
                match = re.search(r' ([A-Za-z0-9_-]{8,})', result.stdout)
                if match:
                    return match.group(1)
            except Exception:
                pass

        elif system == "Darwin":
            result = subprocess.run(
                ['system_profiler', 'SPHardwareDataType'],
                capture_output=True, text=True
            )
            match = re.search(r'Serial Number.*:\s*(\S+)', result.stdout)
            if match:
                return match.group(1)

        elif system == "Linux":
            result = subprocess.run(
                ['udevadm', 'info', '--query=all', '--name=/dev/sda'],
                capture_output=True, text=True
            )
            match = re.search(r'ID_SERIAL_SHORT=(\S+)', result.stdout)
            if match:
                return match.group(1)

        return "UNKNOWN"
    except Exception:
        return "ERROR"


def generate_hardware_id() -> str:
    """Gera ID de hardware baseado em serial do disco e hostname"""
    disk_serial = get_disk_serial()
    hostname = socket.gethostname()

    raw = f"{disk_serial}-{hostname}"
    hash_obj = hashlib.sha256(raw.encode()).hexdigest()[:12]
    hash_upper = hash_obj.upper()

    return '-'.join([hash_upper[i:i+4] for i in range(0, 12, 4)])


# ============================================================================
# GERENCIADOR DE PORTA SERIAL (com detecção inteligente de porta ocupada)
# ============================================================================

class SerialPortManager:
    """Gerencia comunicação serial com detecção inteligente de porta ocupada.

    Quando outro processo (ex: serviço de monitoramento) está usando a porta serial,
    esta classe detecta a condição PermissionError / "Access is denied"
    e lança PortBusyError ao invés de um SerialError genérico. O método
    abrir_com_retry() aguarda automaticamente e tenta abrir novamente.
    """

    def __init__(self, porta: Optional[str] = None, baudrate: int = 2400):
        if not SERIAL_AVAILABLE:
            raise SerialError("pyserial não está instalado. Execute: pip install pyserial")
        self.porta = porta
        self.baudrate = baudrate
        self.serial_port = None

    def abrir(self, timeout: float = 5.0) -> bool:
        """Abre porta serial. Lança PortBusyError se a porta estiver ocupada por outro processo."""
        try:
            self.serial_port = serial.Serial(
                port=self.porta,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout
            )
            return True
        except serial.SerialException as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ['permission', 'access', 'denied', 'busy', 'in use']):
                raise PortBusyError(
                    f"Porta {self.porta} está ocupada por outro processo. "
                    f"Pode haver outra instância ou serviço usando esta porta."
                )
            raise SerialError(f"Erro ao abrir porta {self.porta}: {e}")
        except PermissionError:
            raise PortBusyError(
                f"Porta {self.porta} está ocupada por outro processo."
            )
        except OSError as e:
            if e.errno in (13, 16):  # EACCES, EBUSY
                raise PortBusyError(
                    f"Porta {self.porta} está ocupada por outro processo."
                )
            raise SerialError(f"Erro ao abrir porta {self.porta}: {e}")

    def abrir_com_retry(self, timeout: float = 5.0,
                        busy_max_retries: int = 6,
                        busy_retry_delay: float = 2.0) -> bool:
        """Abre porta com retry automático quando ocupada.

        Quando a porta está ocupada (usada pelo serviço de monitoramento ou outra instância),
        aguarda busy_retry_delay segundos e tenta novamente, até busy_max_retries vezes.
        Como o monitor e o CLI usam a porta por períodos muito curtos (< 2s)
        e a liberam entre operações, o retry geralmente funciona rapidamente.
        """
        for attempt in range(busy_max_retries):
            try:
                return self.abrir(timeout)
            except PortBusyError:
                if attempt < busy_max_retries - 1:
                    logger.debug(
                        f"Porta {self.porta} ocupada, tentativa {attempt + 1}/{busy_max_retries}, "
                        f"aguardando {busy_retry_delay}s..."
                    )
                    time.sleep(busy_retry_delay)
                else:
                    raise PortBusyError(
                        f"Porta {self.porta} permanece ocupada após {busy_max_retries} tentativas "
                        f"({busy_max_retries * busy_retry_delay:.0f}s). "
                        f"Verifique se outro processo (serviço ou outra instância) está usando a porta."
                    )

    def escrever(self, dados: str) -> bool:
        """Envia dados pela porta serial"""
        try:
            self.serial_port.write(dados.encode('ascii'))
            return True
        except Exception as e:
            raise SerialError(f"Erro ao escrever na porta: {e}")

    def ler(self, timeout: float = 1.0) -> str:
        """Lê resposta da porta serial.

        Uses a short inter-byte timeout so we return as soon as the UPS
        finishes sending instead of waiting for `read(200)` to fill or timeout.
        At 2400 baud one byte takes ~4.2ms — we wait up to 50ms of silence
        to confirm the UPS is done transmitting.
        """
        try:
            self.serial_port.timeout = timeout
            # Read first byte (blocks up to `timeout`)
            first = self.serial_port.read(1)
            if not first:
                return ''
            # Switch to short inter-byte timeout to drain remaining bytes quickly
            self.serial_port.timeout = 0.05  # 50ms silence = end of message
            rest = self.serial_port.read(200)
            return (first + rest).decode('ascii', errors='ignore')
        except Exception as e:
            raise SerialError(f"Erro ao ler da porta: {e}")

    def fechar(self):
        """Fecha porta serial"""
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass

    @staticmethod
    def listar_portas() -> List[str]:
        """Lista portas COM disponíveis"""
        if not SERIAL_AVAILABLE:
            return []
        portas = serial.tools.list_ports.comports()
        return [p.device for p in portas]

    @staticmethod
    def detectar_porta() -> str:
        """Auto-detecta porta do nobreak.

        Escaneia todas as portas seriais disponíveis, enviando Q1 para cada uma.
        Se uma porta estiver ocupada (usada por outro processo), ela é registrada.
        Se nenhuma porta responsiva for encontrada mas houver portas ocupadas,
        a primeira porta ocupada é retornada — provavelmente é a porta do nobreak
        sendo usada pelo serviço de monitoramento.
        O chamador deve usar abrir_com_retry() para aguardar a liberação da porta.
        """
        if not SERIAL_AVAILABLE:
            raise SerialError("pyserial não está instalado. Execute: pip install pyserial")

        portas = SerialPortManager.listar_portas()
        portas_ocupadas = []

        for porta in portas:
            try:
                manager = SerialPortManager(porta)
                manager.abrir(timeout=5.0)
                manager.escrever("Q1\r")
                time.sleep(0.5)
                resposta = manager.ler(timeout=5.0)
                manager.fechar()

                if resposta:
                    logger.debug(f"Nobreak detectado na porta {porta}")
                    return porta
            except PortBusyError:
                logger.debug(f"Porta {porta} ocupada durante detecção — marcando como candidata")
                portas_ocupadas.append(porta)
            except Exception:
                continue

        # Se encontramos portas ocupadas mas nenhuma responsiva, a porta ocupada
        # provavelmente é o nobreak sendo usado pelo serviço de monitoramento ou outra instância.
        if portas_ocupadas:
            logger.info(
                f"Porta {portas_ocupadas[0]} está ocupada — assumindo como porta do nobreak. "
                f"Retry automático será usado para aguardar liberação."
            )
            return portas_ocupadas[0]

        raise SerialError("Nenhum dispositivo nobreak encontrado nas portas disponíveis")


# ============================================================================
# PROTOCOLO DE COMANDOS
# ============================================================================

class CommandProtocol:
    """Formata comandos para o protocolo do nobreak"""

    @staticmethod
    def formatar_teste_10s() -> str:
        return "T\r"

    @staticmethod
    def formatar_teste_low() -> str:
        return "TL\r"

    @staticmethod
    def formatar_teste_temporizado(minutos: int) -> str:
        if minutos < 0 or minutos > 99:
            raise ValidationError("Minutos devem estar entre 0 e 99")
        return f"T{minutos:02d}\r"

    @staticmethod
    def formatar_toggle_beep() -> str:
        return "Q\r"

    @staticmethod
    def formatar_shutdown(minutos: int) -> str:
        if minutos < 0 or minutos > 99:
            raise ValidationError("Minutos devem estar entre 0 e 99")
        return f"S{minutos:02d}\r"

    @staticmethod
    def formatar_consulta_status() -> str:
        return "Q1\r"

    @staticmethod
    def formatar_consulta_firmware() -> str:
        return "F\r"


# ============================================================================
# PARSER DE INTERVALO DE TEMPO & AGENDAMENTO PERSONALIZADO
# ============================================================================

def parse_time_interval(interval_str: str) -> int:
    """
    Parse intervalo de tempo para segundos.
    Formatos: Xs, Xm, Xh, Xd, Xw (ou Xsec, Xmin, Xhour, Xday, Xweek)
    """
    interval_str = interval_str.strip().lower()
    match = re.match(r'^(\d+)(s|sec|m|min|h|hour|d|day|w|week)$', interval_str)

    if not match:
        raise ValidationError(
            f"Formato de intervalo inválido: '{interval_str}'. "
            f"Use formatos como: 30s, 5m, 2h, 1d, 1w"
        )

    valor = int(match.group(1))
    unidade = match.group(2)

    multipliers = {
        's': 1, 'sec': 1,
        'm': 60, 'min': 60,
        'h': 3600, 'hour': 3600,
        'd': 86400, 'day': 86400,
        'w': 604800, 'week': 604800,
    }

    return valor * multipliers[unidade]


class CustomSchedule:
    """
    Agendador usando sintaxe Custom.

    Prefixos suportados:
    - md: Month day (dia do mês) 1-31
    - wd: Week day (dia da semana) 1-7 (1=segunda, 7=domingo)
    - h: Hours (horas) 0-23
    - m: Minutes (minutos) 0-59
    - s: Seconds (segundos) 0-59

    Exemplos:
    - "wd2h8m15" = Toda terça-feira às 08:15
    - "h14m30"   = Todos os dias às 14:30
    - "wd1-5h9m0" = Segunda a sexta às 09:00
    - "md15h12m0" = Todo dia 15 do mês às 12:00
    """

    def __init__(self, schedule_str: str):
        self.schedule_str = schedule_str.strip()
        self.month_days = None
        self.week_days = None
        self.hours = None
        self.minutes = None
        self.seconds = None

        md_match = re.search(r'md(\d+(?:-\d+)?)', self.schedule_str)
        if md_match:
            self.month_days = self._parse_range(md_match.group(1), 1, 31, "dia do mês")

        wd_match = re.search(r'wd(\d+(?:-\d+)?)', self.schedule_str)
        if wd_match:
            self.week_days = self._parse_range(wd_match.group(1), 1, 7, "dia da semana")

        h_match = re.search(r'h(\d+(?:-\d+)?)', self.schedule_str)
        if h_match:
            self.hours = self._parse_range(h_match.group(1), 0, 23, "hora")

        m_match = re.search(r'm(\d+(?:-\d+)?)', self.schedule_str)
        if m_match:
            self.minutes = self._parse_range(m_match.group(1), 0, 59, "minuto")

        s_match = re.search(r's(\d+(?:-\d+)?)', self.schedule_str)
        if s_match:
            self.seconds = self._parse_range(s_match.group(1), 0, 59, "segundo")

        if all(x is None for x in [self.month_days, self.week_days, self.hours, self.minutes, self.seconds]):
            raise ValidationError(
                f"Formato Custom inválido: '{schedule_str}'. "
                f"Use prefixos: md, wd, h, m, s. Exemplo: wd2h8m15"
            )

    def _parse_range(self, range_str: str, min_val: int, max_val: int, field_name: str) -> List[int]:
        if '-' in range_str:
            parts = range_str.split('-')
            if len(parts) != 2:
                raise ValidationError(f"Range inválido para {field_name}: {range_str}")
            start, end = int(parts[0]), int(parts[1])
            if start < min_val or start > max_val:
                raise ValidationError(f"{field_name} inválido: {start} (deve ser {min_val}-{max_val})")
            if end < min_val or end > max_val:
                raise ValidationError(f"{field_name} inválido: {end} (deve ser {min_val}-{max_val})")
            if start > end:
                raise ValidationError(f"Range inválido: {start}-{end}")
            return list(range(start, end + 1))
        else:
            val = int(range_str)
            if val < min_val or val > max_val:
                raise ValidationError(f"{field_name} inválido: {val} (deve ser {min_val}-{max_val})")
            return [val]

    def should_run(self, last_run: Optional[datetime]) -> bool:
        agora = datetime.now()

        if last_run is None:
            return False

        # Já executou neste minuto?
        if (last_run.year == agora.year and last_run.month == agora.month and
            last_run.day == agora.day and last_run.hour == agora.hour and
            last_run.minute == agora.minute):
            return False

        if self.month_days is not None and agora.day not in self.month_days:
            return False
        if self.week_days is not None:
            if (agora.weekday() + 1) not in self.week_days:
                return False
        if self.hours is not None and agora.hour not in self.hours:
            return False
        if self.minutes is not None and agora.minute not in self.minutes:
            return False
        if self.seconds is not None and agora.second not in self.seconds:
            return False

        return True

    def get_description(self) -> str:
        dias_semana = {
            1: "segunda", 2: "terça", 3: "quarta",
            4: "quinta", 5: "sexta", 6: "sábado", 7: "domingo"
        }
        parts = []

        if self.month_days:
            if len(self.month_days) == 1:
                parts.append(f"dia {self.month_days[0]} do mês")
            else:
                parts.append(f"dias {self.month_days[0]}-{self.month_days[-1]} do mês")

        if self.week_days:
            if len(self.week_days) == 1:
                parts.append(dias_semana.get(self.week_days[0], str(self.week_days[0])))
            elif self.week_days == list(range(1, 6)):
                parts.append("segunda a sexta")
            else:
                parts.append(", ".join(dias_semana.get(d, str(d)) for d in self.week_days))

        time_parts = []
        time_parts.append(f"{self.hours[0]:02d}" if self.hours else "**")
        time_parts.append(f"{self.minutes[0]:02d}" if self.minutes else "**")
        if self.seconds:
            time_parts.append(f"{self.seconds[0]:02d}")

        time_str = ":".join(time_parts)

        if parts:
            return f"{', '.join(parts)} às {time_str}"
        return f"às {time_str}"


# ============================================================================
# ENVIO DE E-MAIL
# ============================================================================

class EmailSender:
    """Gerencia envio de e-mails de notificação"""

    def __init__(self, smtp_host: str, smtp_port: int, smtp_user: str,
                 smtp_pass: str, from_addr: str, to_addrs: List[str], use_tls: bool = True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.use_tls = use_tls

    def enviar(self, assunto: str, mensagem: str) -> bool:
        """Envia e-mail de notificação"""
        try:
            msg = MIMEMultipart()
            msg['From'] = self.from_addr
            msg['To'] = ', '.join(self.to_addrs)
            msg['Subject'] = assunto
            msg.attach(MIMEText(mensagem, 'plain'))

            if self.use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)

            server.login(self.smtp_user, self.smtp_pass)
            server.send_message(msg)
            server.quit()

            logger.info(f"E-mail enviado: {assunto}")
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar e-mail: {e}")
            return False


# ============================================================================
# FORMATADOR DE SAÍDA
# ============================================================================

class OutputFormatter:
    """Formata saída em JSON ou texto"""

    def __init__(self, formato: str = "text", verbose: bool = False, quiet: bool = False):
        self.formato = formato
        self.verbose = verbose
        self.quiet = quiet

    def formatar_config(self, config: Dict[str, Any]) -> str:
        if self.formato == "json":
            return json.dumps({
                "sucesso": True, "operacao": "read", "dados": config
            }, indent=2, ensure_ascii=False)
        else:
            linhas = []
            header = "Configuração Nobreak:"
            if USE_COLORS and COLORAMA_AVAILABLE:
                linhas.append(f"{Fore.CYAN}{Style.BRIGHT}{header}{Style.RESET_ALL}")
            else:
                linhas.append(header)

            beep_text = 'Ativado' if config.get('beep', 0) == 1 else 'Desativado'
            linhas.append(f"  Beep: {beep_text}")

            if self.verbose:
                linhas.append(f"  Status Raw: {config.get('status_raw', 'N/A')}")
                linhas.append(f"  Tensão Entrada: {config.get('input_voltage', 0):.1f}V")
                linhas.append(f"  Tensão Saída: {config.get('output_voltage', 0):.1f}V")
                linhas.append(f"  Bateria: {config.get('battery', 0):.1f}V")
                linhas.append(f"  Temperatura: {config.get('temperature', 0):.1f}°C")

            return "\n".join(linhas)

    def formatar_resultado_teste(self, resultado: TestResult) -> str:
        if self.formato == "json":
            return json.dumps({
                "sucesso": resultado.sucesso, "comando": resultado.comando,
                "resposta": resultado.resposta, "erro": resultado.erro,
                "porta": resultado.porta
            }, indent=2, ensure_ascii=False)
        else:
            if resultado.sucesso:
                linhas = []
                if USE_COLORS and COLORAMA_AVAILABLE:
                    linhas.append(f"{Fore.GREEN}[OK] Comando enviado: {resultado.comando}{Style.RESET_ALL}")
                else:
                    linhas.append(f"[OK] Comando enviado: {resultado.comando}")
                if self.verbose:
                    linhas.append(f"  Porta: {resultado.porta}")
                linhas.append(f"  Resposta: {resultado.resposta}")
                return "\n".join(linhas)
            else:
                if USE_COLORS and COLORAMA_AVAILABLE:
                    return f"{Fore.RED}[ERRO] {resultado.erro}{Style.RESET_ALL}"
                return f"[ERRO] {resultado.erro}"

    def formatar_erro(self, erro: Dict[str, Any]) -> str:
        if self.formato == "json":
            return json.dumps({"sucesso": False, "erro": erro}, indent=2, ensure_ascii=False)
        else:
            cat = erro.get('categoria', 'Desconhecido')
            msg = erro.get('mensagem', '')
            result = f"Erro: {cat}: {msg}"
            if self.verbose and 'detalhes' in erro:
                result += f"\nDetalhes: {erro['detalhes']}"
            if 'sugestao' in erro:
                result += f"\nSugestão: {erro['sugestao']}"

            if USE_COLORS and COLORAMA_AVAILABLE:
                return f"{Fore.RED}{result}{Style.RESET_ALL}"
            return result


# ============================================================================
# CLIENTE API (para agendar comandos via monitor)
# ============================================================================

class ApiClient:
    """HTTP client to route commands through the running API server.

    When the monitor is running (as a service), standalone CLI commands (test, read, write)
    should route through the API to use the monitor's _serial_lock, avoiding COM port
    contention. This is the same approach used by the Android app.
    """

    def __init__(self, config: AppConfig):
        host = config.get('api', 'host', fallback='0.0.0.0')
        self.host = '127.0.0.1' if host in ('0.0.0.0', '') else host
        self.port = config.getint('api', 'port', fallback=8080)
        self.username = config.get('api', 'auth_username', fallback='')
        self.password = config.get('api', 'auth_password', fallback='')
        self.base_url = f"http://{self.host}:{self.port}"

    def _build_request(self, method: str, path: str, data: Optional[dict] = None):
        """Build urllib.request.Request with auth headers."""
        import urllib.request
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode('utf-8') if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header('Content-Type', 'application/json; charset=utf-8')
        if self.username:
            cred = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            req.add_header('Authorization', f'Basic {cred}')
        return req

    def request(self, method: str, path: str, data: Optional[dict] = None,
                timeout: float = 30.0) -> Optional[dict]:
        """Make HTTP request. Returns parsed JSON dict or None on any failure."""
        import urllib.request
        import urllib.error
        try:
            req = self._build_request(method, path, data)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return None

    def is_running(self) -> bool:
        """Check if the API server is reachable."""
        result = self.request('GET', '/api/health', timeout=3.0)
        return result is not None and result.get('success', False)

    def run_test(self, test_type: str, minutes: Optional[int] = None) -> Optional[dict]:
        data = {'minutes': minutes} if minutes is not None else None
        return self.request('POST', f'/api/test/{test_type}', data=data)

    def read_ups(self) -> Optional[dict]:
        """Read fresh UPS data through the monitor's serial lock."""
        return self.request('GET', '/api/read')

    def write_ups(self, **kwargs) -> Optional[dict]:
        """Write UPS config through the monitor's serial lock."""
        return self.request('POST', '/api/write', data=kwargs)


def _try_api_route(config: AppConfig, operation: str, quiet: bool = False, **kwargs) -> Optional[dict]:
    """Try to route a command through the running API server.

    Returns API response dict if successful, None if API is not available.
    This allows CLI commands to be 'scheduled' through the monitor's serial lock,
    avoiding COM port contention — the same approach used by the Android app.
    """
    if not config.getboolean('api', 'enabled', fallback=False):
        return None

    api = ApiClient(config)
    try:
        if not api.is_running():
            return None
    except Exception:
        return None

    if not quiet:
        print_info("Monitor ativo — agendando comando via API...")
    logger.info(f"API detectada — agendando '{operation}' via API")

    if operation == 'read':
        return api.read_ups()
    elif operation == 'write':
        return api.write_ups(**kwargs)
    elif operation.startswith('test/'):
        test_type = operation[5:]
        return api.run_test(test_type, minutes=kwargs.get('minutes'))

    return None


# ============================================================================
# HANDLER DE TESTES
# ============================================================================

class TestHandler:
    """Gerencia envio de comandos de teste com retry de porta ocupada"""

    def __init__(self, porta: Optional[str] = None, config: Optional[AppConfig] = None):
        self.porta = porta
        self.config = config

    def _get_busy_params(self):
        if self.config:
            return (
                self.config.getint('serial', 'busy_max_retries', fallback=6),
                self.config.getfloat('serial', 'busy_retry_delay', fallback=2.0),
            )
        return (6, 2.0)

    def executar_comando(self, comando: str) -> TestResult:
        """Executa comando no dispositivo com detecção de porta ocupada"""
        try:
            if not self.porta:
                self.porta = SerialPortManager.detectar_porta()

            max_retries, retry_delay = self._get_busy_params()
            manager = SerialPortManager(self.porta)
            manager.abrir_com_retry(
                busy_max_retries=max_retries,
                busy_retry_delay=retry_delay
            )
            manager.escrever(comando)
            time.sleep(0.5)
            resposta = manager.ler()
            manager.fechar()

            return TestResult(
                comando=comando.strip(), sucesso=True,
                resposta=resposta if resposta else "Sem resposta",
                porta=self.porta
            )
        except PortBusyError as e:
            return TestResult(
                comando=comando.strip(), sucesso=False,
                erro=f"Porta ocupada: {e}",
                porta=self.porta
            )
        except Exception as e:
            return TestResult(
                comando=comando.strip(), sucesso=False,
                erro=str(e), porta=self.porta
            )

    def teste_10s(self) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_teste_10s())

    def teste_low(self) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_teste_low())

    def teste_temporizado(self, minutos: int) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_teste_temporizado(minutos))

    def toggle_beep(self) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_toggle_beep())

    def shutdown(self, minutos: int) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_shutdown(minutos))

    def consultar_status(self) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_consulta_status())

    def consultar_firmware(self) -> TestResult:
        return self.executar_comando(CommandProtocol.formatar_consulta_firmware())


# ============================================================================
# SERVIDOR HTTP API (para Android / apps externos)
# ============================================================================

class NobreakAPIHandler(BaseHTTPRequestHandler):
    """Handler HTTP REST API para apps externos (ex: Android).

    Endpoints:
        GET  /api/status        - Status atual do nobreak (tensões, bateria, flags)
        GET  /api/health        - Saúde do serviço (uptime, estado do monitoramento, falhas)
        GET  /api/info          - Info do dispositivo (hardware ID, hostname, portas)
        GET  /api/read          - Lê configuração fresca do nobreak via serial lock
        POST /api/test/<tipo>   - Executa teste (10s, low, timed, beep, shutdown, status, firmware)
        POST /api/write         - Escreve configuração no nobreak via serial lock

    Todas as respostas são JSON com headers CORS para acesso cross-origin.
    """

    # Referência de classe para a instância do monitor (definida antes do servidor iniciar)
    monitor_ref: Optional['NobreakMonitor'] = None
    # Credenciais de autenticação (definidas em start_api_server)
    auth_username: str = ''
    auth_password: str = ''
    # Cached info data (computed once — hardware ID, hostname, ports never change at runtime)
    _cached_info: Optional[dict] = None

    def address_string(self):
        """Override to skip reverse DNS lookup on every request — major latency fix."""
        return self.client_address[0]

    def _check_auth(self) -> bool:
        """Verifica Basic Auth se configurado. Retorna True se autorizado."""
        if not self.__class__.auth_username:
            return True  # Auth não configurada, libera acesso

        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Basic '):
            self._send_unauthorized()
            return False

        try:
            decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = decoded.split(':', 1)
            if username == self.__class__.auth_username and password == self.__class__.auth_password:
                return True
        except Exception:
            pass

        self._send_unauthorized()
        return False

    def _send_unauthorized(self):
        """Envia resposta 401 Unauthorized com header WWW-Authenticate"""
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="TSShara API"')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._send_cors_headers()
        self.end_headers()
        body = json.dumps({'success': False, 'error': 'Autenticação necessária'}, ensure_ascii=False)
        self.wfile.write(body.encode('utf-8'))

    def do_GET(self):
        if not self._check_auth():
            return
        path = self.path.split('?')[0].rstrip('/')
        routes = {
            '/api/status': self._handle_status,
            '/api/health': self._handle_health,
            '/api/info': self._handle_info,
            '/api/read': self._handle_read,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json({
                'error': 'Endpoint não encontrado',
                'endpoints': list(routes.keys()) + ['/api/test/<type>', '/api/write']
            }, 404)

    def do_POST(self):
        if not self._check_auth():
            return
        path = self.path.split('?')[0].rstrip('/')
        if path.startswith('/api/test/'):
            test_type = path[len('/api/test/'):]
            self._handle_test(test_type)
        elif path == '/api/write':
            self._handle_write()
        else:
            self._send_json({'error': 'Endpoint não encontrado'}, 404)

    def do_OPTIONS(self):
        """Trata requisições preflight CORS"""
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def _handle_status(self):
        monitor = self.__class__.monitor_ref
        if monitor and monitor.last_status:
            s = monitor.last_status
            if s.test_in_progress:
                status_text = 'TEST'
            elif s.utility_fail:
                status_text = 'POWER_FAIL'
            elif s.battery_low:
                status_text = 'BATTERY_LOW'
            else:
                status_text = 'OK'

            # Use last_successful_read as timestamp (when data was actually read
            # from the UPS) rather than now() — the API serves cached data.
            read_ts = (
                monitor.last_successful_read.isoformat()
                if monitor.last_successful_read
                else datetime.now().isoformat()
            )

            data = {
                'success': True,
                'timestamp': read_ts,
                'data': {
                    'input_voltage': s.input_voltage,
                    'input_fault_voltage': s.input_fault_voltage,
                    'output_voltage': s.output_voltage,
                    'current': s.current,
                    'frequency': s.frequency,
                    'battery': s.battery,
                    'temperature': s.temperature,
                    'utility_fail': s.utility_fail,
                    'battery_low': s.battery_low,
                    'bypass_mode': s.bypass_mode,
                    'ups_failed': s.ups_failed,
                    'ups_standby': s.ups_standby,
                    'test_in_progress': s.test_in_progress,
                    'shutdown_active': s.shutdown_active,
                    'beep_on': s.beep_on,
                    'status_raw': s.status_raw,
                    'status_text': status_text,
                }
            }
        else:
            data = {
                'success': False,
                'error': 'Nenhum dado de status disponível ainda',
                'timestamp': datetime.now().isoformat(),
            }
        self._send_json(data)

    def _handle_health(self):
        monitor = self.__class__.monitor_ref
        data = {
            'success': True,
            'service': 'tsshara-cli',
            'version': VERSION,
            'monitoring': monitor.running if monitor else False,
            'timestamp': datetime.now().isoformat(),
            'port': monitor.porta if monitor else None,
        }
        if monitor:
            data['uptime_seconds'] = int(time.time() - monitor.start_time) if monitor.start_time else 0
            data['last_successful_read'] = (
                monitor.last_successful_read.isoformat()
                if monitor.last_successful_read else None
            )
            data['consecutive_failures'] = monitor.consecutive_failures
            data['poll_interval'] = monitor.poll_interval
        self._send_json(data)

    def _handle_info(self):
        # Cache info data — hardware_id, hostname, and ports don't change at runtime.
        # generate_hardware_id() spawns PowerShell subprocesses and is very slow.
        if not self.__class__._cached_info:
            self.__class__._cached_info = {
                'success': True,
                'hardware_id': generate_hardware_id(),
                'hostname': socket.gethostname(),
                'platform': platform.system(),
                'version': VERSION,
                'serial_available': SERIAL_AVAILABLE,
                'colorama_available': COLORAMA_AVAILABLE,
                'ports': SerialPortManager.listar_portas(),
            }
        self._send_json(self.__class__._cached_info)

    def _handle_read(self):
        """Read fresh UPS configuration through monitor's serial lock."""
        monitor = self.__class__.monitor_ref
        if not monitor:
            self._send_json({'success': False, 'error': 'Monitor não está ativo'}, 503)
            return

        resposta = monitor._executar_serial("Q1\r")
        if not resposta:
            self._send_json({
                'success': False,
                'error': 'Não foi possível ler dados do nobreak',
                'timestamp': datetime.now().isoformat(),
            })
            return

        status = monitor.parse_status(resposta)
        if not status:
            self._send_json({
                'success': False,
                'error': 'Resposta inválida do nobreak',
                'raw_response': resposta,
                'timestamp': datetime.now().isoformat(),
            })
            return

        self._send_json({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'data': {
                'beep': 1 if status.beep_on else 0,
                'status_raw': status.status_raw,
                'input_voltage': status.input_voltage,
                'output_voltage': status.output_voltage,
                'battery': status.battery,
                'temperature': status.temperature,
                'input_fault_voltage': status.input_fault_voltage,
                'current': status.current,
                'frequency': status.frequency,
            },
            'port': monitor.porta,
        })

    def _handle_write(self):
        """Write UPS configuration through monitor's serial lock."""
        monitor = self.__class__.monitor_ref
        if not monitor:
            self._send_json({'success': False, 'error': 'Monitor não está ativo'}, 503)
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            params = json.loads(body.decode('utf-8'))
        except Exception:
            self._send_json({'success': False, 'error': 'JSON inválido'}, 400)
            return

        commands_sent = []

        if 'beep' in params:
            desired_beep = int(params['beep'])
            # Read current state
            resposta = monitor._executar_serial("Q1\r")
            if not resposta:
                self._send_json({
                    'success': False, 'error': 'Não foi possível ler estado atual',
                    'timestamp': datetime.now().isoformat(),
                })
                return

            status = monitor.parse_status(resposta)
            if not status:
                self._send_json({
                    'success': False, 'error': 'Resposta inválida ao ler estado',
                    'timestamp': datetime.now().isoformat(),
                })
                return

            current_beep = 1 if status.beep_on else 0
            if current_beep != desired_beep:
                result = monitor._executar_serial("Q\r")
                if result is not None:
                    commands_sent.append('Toggle beep (Q)')
                else:
                    self._send_json({
                        'success': False, 'error': 'Falha ao enviar toggle beep',
                        'timestamp': datetime.now().isoformat(),
                    })
                    return
            else:
                commands_sent.append(f'Beep já no estado desejado ({desired_beep})')

        if not commands_sent:
            self._send_json({
                'success': False, 'error': 'Nenhum parâmetro válido',
                'timestamp': datetime.now().isoformat(),
            }, 400)
            return

        self._send_json({
            'success': True,
            'commands': commands_sent,
            'timestamp': datetime.now().isoformat(),
        })

    def _handle_test(self, test_type: str):
        """Executa um comando de teste no nobreak via API.

        Tipos suportados: 10s, low, timed, beep, shutdown, status, firmware

        - 'status' and 'firmware' are served from cached data (no COM port access).
        - '10s', 'low', 'timed', 'beep', 'shutdown' send commands through the
          monitor's serial lock to avoid COM port contention with the monitoring loop.
        """
        monitor = self.__class__.monitor_ref

        valid_types = ['10s', 'low', 'beep', 'status', 'firmware', 'timed', 'shutdown']
        if test_type not in valid_types:
            self._send_json({
                'success': False,
                'error': f'Tipo de teste inválido: {test_type}',
                'valid_types': valid_types,
            }, 400)
            return

        try:
            # ── Queries: return cached data without touching COM port ──
            if test_type == 'status':
                # Same data as /api/status — return cached monitor status
                if monitor and monitor.last_status:
                    s = monitor.last_status
                    self._send_json({
                        'success': True,
                        'test_type': 'status',
                        'command': 'Q1',
                        'response': s.status_raw,
                        'port': monitor.porta,
                        'timestamp': (
                            monitor.last_successful_read.isoformat()
                            if monitor.last_successful_read
                            else datetime.now().isoformat()
                        ),
                    })
                else:
                    self._send_json({
                        'success': False,
                        'test_type': 'status',
                        'error': 'Nenhum dado de status disponível ainda',
                        'timestamp': datetime.now().isoformat(),
                    })
                return

            if test_type == 'firmware':
                # Firmware rarely changes — use cached value
                firmware = monitor.get_firmware() if monitor else None
                if firmware:
                    self._send_json({
                        'success': True,
                        'test_type': 'firmware',
                        'command': 'I',
                        'response': firmware,
                        'port': monitor.porta if monitor else None,
                        'timestamp': datetime.now().isoformat(),
                    })
                else:
                    self._send_json({
                        'success': False,
                        'test_type': 'firmware',
                        'error': 'Não foi possível obter informação de firmware',
                        'timestamp': datetime.now().isoformat(),
                    })
                return

            # ── Commands: route through monitor's serial lock ─────────
            if not monitor:
                self._send_json({
                    'success': False,
                    'error': 'Monitor não está ativo',
                    'test_type': test_type,
                    'timestamp': datetime.now().isoformat(),
                }, 503)
                return

            command_map = {
                '10s': CommandProtocol.formatar_teste_10s,
                'low': CommandProtocol.formatar_teste_low,
                'beep': CommandProtocol.formatar_toggle_beep,
            }

            # Handle timed/shutdown (require minutes parameter from request body)
            if test_type in ('timed', 'shutdown'):
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length) if content_length > 0 else b'{}'
                try:
                    params = json.loads(body.decode('utf-8'))
                except Exception:
                    params = {}
                minutes = int(params.get('minutes', 1))
                if test_type == 'timed':
                    comando = CommandProtocol.formatar_teste_temporizado(minutes)
                else:
                    comando = CommandProtocol.formatar_shutdown(minutes)
                result = monitor.executar_comando_api(comando)
                data = {
                    'success': result.sucesso,
                    'test_type': test_type,
                    'command': result.comando,
                    'response': result.resposta,
                    'error': result.erro,
                    'port': result.porta,
                    'minutes': minutes,
                    'timestamp': datetime.now().isoformat(),
                }
                self._send_json(data, 200)
                return

            comando = command_map[test_type]()
            result = monitor.executar_comando_api(comando)

            data = {
                'success': result.sucesso,
                'test_type': test_type,
                'command': result.comando,
                'response': result.resposta,
                'error': result.erro,
                'port': result.porta,
                'timestamp': datetime.now().isoformat(),
            }
            self._send_json(data, 200)
        except Exception as e:
            logger.error(f"Erro ao executar teste '{test_type}' via API: {e}")
            self._send_json({
                'success': False,
                'error': str(e),
                'test_type': test_type,
                'timestamp': datetime.now().isoformat(),
            }, 500)

    def _send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Connection', 'keep-alive')
        self._send_cors_headers()
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def log_message(self, format, *args):
        """Redireciona logs de acesso HTTP para o nosso logger ao invés de stderr"""
        logger.debug(f"API: {format % args}")


def start_api_server(host: str, port: int, monitor: 'NobreakMonitor',
                     auth_username: str = '', auth_password: str = '') -> HTTPServer:
    """Inicia o servidor HTTP API em uma thread daemon em background."""
    NobreakAPIHandler.monitor_ref = monitor
    NobreakAPIHandler.auth_username = auth_username
    NobreakAPIHandler.auth_password = auth_password
    NobreakAPIHandler._cached_info = None  # Reset cache on restart

    server = ThreadingHTTPServer((host, port), NobreakAPIHandler)
    server.daemon_threads = True
    # Disable reverse DNS lookups on incoming connections — this is a major
    # latency source (can add seconds per request if DNS is slow or unavailable).
    server.address_family = socket.AF_INET
    thread = threading.Thread(target=server.serve_forever, daemon=True, name='api-server')
    thread.start()

    print_info(f"API HTTP iniciada em http://{host}:{port}")
    print_info(f"  Endpoints: /api/status  /api/health  /api/info")
    if auth_username:
        print_info(f"  Autenticação: Basic Auth (usuário: {auth_username})")
    return server


# ============================================================================
# MONITOR DE NOBREAK
# ============================================================================

class NobreakMonitor:
    """Monitora status do nobreak com detecção inteligente de porta ocupada.

    O monitor abre a porta serial brevemente (< 2 segundos) a cada ciclo de polling,
    lê o status e fecha a porta imediatamente. Isso permite que comandos CLI
    sejam executados entre os ciclos de polling aguardando a porta ser liberada.

    Quando a porta está ocupada (ex: um comando CLI está rodando), o monitor
    pula o ciclo atual e tenta novamente no próximo intervalo.
    """

    def __init__(self, porta: Optional[str], poll_interval: int,
                 timeout: float, retries: int, battery_threshold: float,
                 email_sender: Optional[EmailSender] = None,
                 test_interval_seconds: Optional[int] = None,
                 test_schedule: Optional[CustomSchedule] = None,
                 busy_max_retries: int = 3,
                 busy_retry_delay: float = 2.0):
        self.porta = porta
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.retries = retries
        self.battery_threshold = battery_threshold
        self.email_sender = email_sender
        self.test_interval_seconds = test_interval_seconds
        self.test_schedule = test_schedule
        self.busy_max_retries = busy_max_retries
        self.busy_retry_delay = busy_retry_delay

        self.last_status: Optional[NobreakStatus] = None
        self.last_test_time: Optional[datetime] = None
        self.last_successful_read: Optional[datetime] = None
        self.consecutive_failures: int = 0
        self.start_time: float = 0
        self.running = True

        # Lock to serialize COM port access between monitoring loop and API commands
        self._serial_lock = threading.Lock()
        # Cached firmware string (fetched once and reused)
        self._firmware: Optional[str] = None

    def parse_status(self, resposta: str) -> Optional[NobreakStatus]:
        """Parse da resposta Q1 para NobreakStatus"""
        try:
            clean = resposta.replace('#', '').replace('(', '').strip()
            parts = clean.split()

            if len(parts) < 8:
                return None

            status_bits = parts[7]
            if len(status_bits) < 8:
                return None

            return NobreakStatus(
                input_voltage=float(parts[0]),
                input_fault_voltage=float(parts[1]),
                output_voltage=float(parts[2]),
                current=float(parts[3]),
                frequency=float(parts[4]),
                battery=float(parts[5]),
                temperature=float(parts[6]),
                utility_fail=status_bits[0] == '1',
                battery_low=status_bits[1] == '1',
                bypass_mode=status_bits[2] == '1',
                ups_failed=status_bits[3] == '1',
                ups_standby=status_bits[4] == '1',
                test_in_progress=status_bits[5] == '1',
                shutdown_active=status_bits[6] == '1',
                beep_on=status_bits[7] == '1',
                status_raw=status_bits
            )
        except Exception as e:
            logger.error(f"Erro ao fazer parse do status: {e}")
            return None

    def _executar_serial(self, comando: str) -> Optional[str]:
        """Executa comando serial com retry de porta ocupada.
        Retorna a string de resposta, ou None em caso de falha.
        Thread-safe: uses _serial_lock to serialize COM port access.
        """
        with self._serial_lock:
            try:
                manager = SerialPortManager(self.porta)
                manager.abrir_com_retry(
                    timeout=self.timeout,
                    busy_max_retries=self.busy_max_retries,
                    busy_retry_delay=self.busy_retry_delay
                )
                manager.escrever(comando)
                time.sleep(0.1)  # brief pause for UPS to start responding
                resposta = manager.ler(timeout=2.0)  # 2s is plenty at 2400 baud
                manager.fechar()
                return resposta
            except PortBusyError:
                logger.warning("Porta ocupada — pulando este ciclo (outro processo pode estar usando a porta)")
                return None
            except SerialError as e:
                logger.error(f"Erro serial: {e}")
                return None

    def executar_comando_api(self, comando: str) -> TestResult:
        """Executes a command through the monitor's serial lock.
        Used by the HTTP API to avoid COM port contention.
        Waits for the monitor's current serial operation to finish first.
        """
        if not self.porta:
            try:
                self.porta = SerialPortManager.detectar_porta()
            except Exception as e:
                return TestResult(comando=comando.strip(), sucesso=False,
                                  erro=str(e), porta=self.porta)

        resposta = self._executar_serial(comando)
        if resposta is not None:
            return TestResult(comando=comando.strip(), sucesso=True,
                              resposta=resposta if resposta else "Sem resposta",
                              porta=self.porta)
        else:
            return TestResult(comando=comando.strip(), sucesso=False,
                              erro="Porta ocupada ou erro de comunicação",
                              porta=self.porta)

    def get_firmware(self) -> Optional[str]:
        """Returns cached firmware string, fetching once from UPS if needed."""
        if self._firmware:
            return self._firmware
        resp = self._executar_serial(CommandProtocol.formatar_consulta_firmware())
        if resp:
            self._firmware = resp.strip()
        return self._firmware

    def ler_status(self) -> Optional[NobreakStatus]:
        """Lê status atual do nobreak com retries"""
        for tentativa in range(self.retries):
            try:
                if not self.porta:
                    self.porta = SerialPortManager.detectar_porta()

                resposta = self._executar_serial("Q1\r")
                if resposta:
                    status = self.parse_status(resposta)
                    if status:
                        return status

                # Resposta vazia ou não pôde ser interpretada
                if tentativa < self.retries - 1:
                    logger.debug(f"Tentativa {tentativa + 1} sem resposta válida, retentando...")
                    time.sleep(1)
            except Exception as e:
                if tentativa < self.retries - 1:
                    logger.warning(f"Tentativa {tentativa + 1} falhou: {e}")
                    time.sleep(1)
                else:
                    logger.error(f"Erro ao ler status após {self.retries} tentativas: {e}")

        return None

    def enviar_notificacao(self, assunto: str, mensagem: str):
        if self.email_sender:
            logger.info(f"Enviando e-mail: {assunto}")
            self.email_sender.enviar(assunto, mensagem)

    def executar_teste_10s(self) -> bool:
        """Executa teste de 10 segundos no nobreak"""
        try:
            if not self.porta:
                self.porta = SerialPortManager.detectar_porta()

            resposta = self._executar_serial("T\r")
            if resposta is not None:
                print_success("[OK] Teste de 10 segundos iniciado")
                self.enviar_notificacao(
                    "Nobreak - Teste Automático Iniciado",
                    f"Teste de 10 segundos iniciado automaticamente.\n\n"
                    f"Horário: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                return True
            else:
                print_warning("Não foi possível iniciar teste (porta pode estar ocupada)")
                return False
        except Exception as e:
            logger.error(f"Erro ao executar teste de 10 segundos: {e}")
            return False

    def verificar_teste_agendado(self):
        """Verifica se é hora de executar o teste agendado"""
        # Agendamento por intervalo
        if self.test_interval_seconds:
            agora = datetime.now()
            if self.last_test_time is None:
                self.executar_teste_10s()
                self.last_test_time = agora
                return
            if agora - self.last_test_time >= timedelta(seconds=self.test_interval_seconds):
                self.executar_teste_10s()
                self.last_test_time = agora
                return

        # Agendamento estilo cron
        if self.test_schedule:
            if self.last_test_time is None:
                self.last_test_time = datetime.now()
            if self.test_schedule.should_run(self.last_test_time):
                self.executar_teste_10s()
                self.last_test_time = datetime.now()

    def verificar_mudancas(self, status: NobreakStatus):
        """Verifica mudanças de estado e envia notificações"""
        if not self.last_status:
            self.last_status = status
            return

        # Teste em progresso
        if status.test_in_progress:
            if not self.last_status.test_in_progress:
                print_warning("[!] Teste em progresso detectado")
                self.enviar_notificacao(
                    "Nobreak - Teste Iniciado",
                    f"Teste de bateria iniciado.\n\n"
                    f"Bateria: {status.battery:.1f}V\n"
                    f"Temperatura: {status.temperature:.1f}°C"
                )
        else:
            # Falha de energia
            if status.utility_fail and not self.last_status.utility_fail:
                print_error("[!] ALERTA: Falha de energia detectada!")
                self.enviar_notificacao(
                    "Nobreak - FALHA DE ENERGIA",
                    f"Falha de energia detectada!\n\n"
                    f"Tensão de entrada: {status.input_voltage:.1f}V\n"
                    f"Tensão de saída: {status.output_voltage:.1f}V\n"
                    f"Bateria: {status.battery:.1f}V\n"
                    f"Temperatura: {status.temperature:.1f}°C"
                )

            # Restauração de energia
            if not status.utility_fail and self.last_status.utility_fail:
                print_success("[OK] Energia restaurada")
                self.enviar_notificacao(
                    "Nobreak - Energia Restaurada",
                    f"Energia foi restaurada.\n\n"
                    f"Tensão de entrada: {status.input_voltage:.1f}V\n"
                    f"Tensão de saída: {status.output_voltage:.1f}V\n"
                    f"Bateria: {status.battery:.1f}V"
                )

        # Bateria baixa
        if status.battery < self.battery_threshold and self.last_status.battery >= self.battery_threshold:
            print_error(f"[!] ALERTA: Bateria baixa ({status.battery:.1f}V)")
            self.enviar_notificacao(
                "Nobreak - BATERIA BAIXA",
                f"Bateria abaixo do limite!\n\n"
                f"Bateria: {status.battery:.1f}V\n"
                f"Limite: {self.battery_threshold:.1f}V\n"
                f"Temperatura: {status.temperature:.1f}°C"
            )

        self.last_status = status

    def _print_banner(self):
        """Imprime banner de inicialização do monitor com cores"""
        if USE_COLORS and COLORAMA_AVAILABLE:
            print(f"\n{Fore.CYAN}{Style.BRIGHT}{'=' * 60}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{Style.BRIGHT}  TSShara CLI - Monitor de Nobreak v{VERSION}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{Style.BRIGHT}{'=' * 60}{Style.RESET_ALL}\n")
        else:
            print(f"\n{'=' * 60}")
            print(f"  TSShara CLI - Monitor de Nobreak v{VERSION}")
            print(f"{'=' * 60}\n")

        print_info(f"Intervalo de polling: {self.poll_interval}s")
        print_info(f"Timeout: {self.timeout}s | Retries: {self.retries}")
        print_info(f"Limite de bateria: {self.battery_threshold}V")
        print_info(f"Porta: {self.porta or 'Auto-detecção'}")

        if self.test_interval_seconds:
            if self.test_interval_seconds < 60:
                intervalo_str = f"{self.test_interval_seconds}s"
            elif self.test_interval_seconds < 3600:
                intervalo_str = f"{self.test_interval_seconds // 60}m"
            elif self.test_interval_seconds < 86400:
                intervalo_str = f"{self.test_interval_seconds // 3600}h"
            elif self.test_interval_seconds < 604800:
                intervalo_str = f"{self.test_interval_seconds // 86400}d"
            else:
                intervalo_str = f"{self.test_interval_seconds // 604800}w"
            print_info(f"Teste automático: a cada {intervalo_str}")

        if self.test_schedule:
            print_info(f"Teste agendado: {self.test_schedule.get_description()}")

        if self.email_sender:
            print_info(f"E-mails: {', '.join(self.email_sender.to_addrs)}")

        print()

    def iniciar(self):
        """Inicia monitoramento contínuo"""
        self.start_time = time.time()
        self._print_banner()
        print_info("Pressione Ctrl+C para parar\n")

        # Registra handlers de sinal para shutdown graceful (NSSM envia SIGTERM)
        def _signal_handler(signum, frame):
            logger.info(f"Sinal {signum} recebido, encerrando...")
            self.running = False

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, _signal_handler)

        try:
            while self.running:
                self.verificar_teste_agendado()

                status = self.ler_status()

                if status:
                    print_status_line(status)
                    self.verificar_mudancas(status)
                    self.last_successful_read = datetime.now()
                    self.consecutive_failures = 0
                else:
                    self.consecutive_failures += 1
                    if self.consecutive_failures <= 3:
                        print_warning(
                            f"[{time.strftime('%H:%M:%S')}] "
                            f"Falha ao ler status (tentativa {self.consecutive_failures})"
                        )
                    elif self.consecutive_failures % 10 == 0:
                        # Não encher: só registra a cada 10 falhas após as 3 primeiras
                        print_error(
                            f"[{time.strftime('%H:%M:%S')}] "
                            f"Falhas consecutivas: {self.consecutive_failures}"
                        )

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print_info("\n\nMonitoramento interrompido pelo usuário")
            self.running = False


# ============================================================================
# TRATADOR DE ERROS
# ============================================================================

class ErrorHandler:

    @staticmethod
    def tratar_erro_serial(erro: Exception, formatter: OutputFormatter) -> int:
        """Porta serial - código 2"""
        msg_erro = {
            "codigo": 2,
            "categoria": "Erro de Porta Serial",
            "mensagem": str(erro),
            "sugestao": "Verifique se o dispositivo está conectado"
        }
        print(formatter.formatar_erro(msg_erro))
        logger.error(f"Erro serial: {erro}")
        return 2

    @staticmethod
    def tratar_erro_porta_ocupada(erro: Exception, formatter: OutputFormatter) -> int:
        """Porta ocupada - código 2 com sugestão de serviço"""
        msg_erro = {
            "codigo": 2,
            "categoria": "Porta Ocupada",
            "mensagem": str(erro),
            "sugestao": (
                "A porta serial está em uso por outro processo. "
                "Se o serviço de monitoramento está rodando, aguarde ou pare-o "
                "antes de usar comandos manuais."
            )
        }
        print(formatter.formatar_erro(msg_erro))
        logger.warning(f"Porta ocupada: {erro}")
        return 2

    @staticmethod
    def tratar_erro_validacao(erro: Exception, formatter: OutputFormatter) -> int:
        """Validação - código 3"""
        msg_erro = {
            "codigo": 3,
            "categoria": "Erro de Validação",
            "mensagem": str(erro)
        }
        print(formatter.formatar_erro(msg_erro))
        return 3

    @staticmethod
    def tratar_erro_inesperado(erro: Exception, formatter: OutputFormatter) -> int:
        """Erro inesperado - código 99"""
        msg_erro = {
            "codigo": 99,
            "categoria": "Erro Inesperado",
            "mensagem": str(erro)
        }
        print(formatter.formatar_erro(msg_erro))
        logger.exception(f"Erro inesperado: {erro}")
        return 99


# ============================================================================
# HANDLERS DE COMANDOS
# ============================================================================

def handle_nbid(args, formatter: OutputFormatter, config: AppConfig) -> int:
    try:
        nb_id = generate_hardware_id()
        if formatter.formato == "json":
            print(json.dumps({"sucesso": True, "nb_id": nb_id}, indent=2, ensure_ascii=False))
        else:
            print_success(f"ID Nobreak: {nb_id}")
        return 0
    except Exception as e:
        return ErrorHandler.tratar_erro_inesperado(e, formatter)


def handle_config_read(args, formatter: OutputFormatter, config: AppConfig) -> int:
    """Lê configuração diretamente do nobreak via serial"""
    if not SERIAL_AVAILABLE:
        print_error("pyserial não está instalado. Execute: pip install pyserial")
        return 2

    try:
        # Try routing through API if monitor is running (scheduled access)
        quiet = getattr(args, 'quiet', False)
        api_result = _try_api_route(config, 'read', quiet=quiet)
        if api_result and api_result.get('success'):
            data = api_result.get('data', {})
            cfg_data = {
                'beep': data.get('beep', 0),
                'status_raw': data.get('status_raw', ''),
                'input_voltage': data.get('input_voltage', 0),
                'output_voltage': data.get('output_voltage', 0),
                'battery': data.get('battery', 0),
                'temperature': data.get('temperature', 0),
            }
            print(formatter.formatar_config(cfg_data))
            return 0
        elif api_result and not api_result.get('success'):
            print_error(f"Erro via API: {api_result.get('error', 'Desconhecido')}")
            return 2

        # Fallback: direct serial access
        # Resolve porta: CLI > config > auto-detectar
        porta = getattr(args, 'port', None)
        if not porta:
            port_cfg = config.get('serial', 'port', fallback='auto')
            porta = None if port_cfg == 'auto' else port_cfg
        if not porta:
            porta = SerialPortManager.detectar_porta()

        timeout = config.getfloat('serial', 'timeout', fallback=5.0)
        retries = config.getint('serial', 'retries', fallback=5)
        busy_max = config.getint('serial', 'busy_max_retries', fallback=6)
        busy_delay = config.getfloat('serial', 'busy_retry_delay', fallback=2.0)

        for tentativa in range(retries):
            try:
                manager = SerialPortManager(porta)
                manager.abrir_com_retry(
                    timeout=timeout,
                    busy_max_retries=busy_max,
                    busy_retry_delay=busy_delay
                )

                manager.escrever("Q1\r")
                time.sleep(0.5)
                resposta = manager.ler(timeout=timeout)
                manager.fechar()

                if not resposta:
                    if tentativa < retries - 1:
                        if not formatter.quiet:
                            print_warning(f"Tentativa {tentativa + 1} falhou, tentando novamente...")
                        time.sleep(1)
                        continue
                    raise SerialError("Dispositivo não respondeu")

                clean = resposta.replace('#', '').replace('(', '').strip()
                parts = clean.split()

                if len(parts) < 8:
                    if tentativa < retries - 1:
                        if not formatter.quiet:
                            print_warning("Resposta inválida, tentando novamente...")
                        time.sleep(1)
                        continue
                    raise SerialError(f"Resposta inválida: {resposta}")

                status_bits = parts[7]
                beep_status = int(status_bits[7]) if len(status_bits) >= 8 else 0

                cfg_data = {
                    "beep": beep_status, "status_raw": status_bits,
                    "input_voltage": float(parts[0]),
                    "output_voltage": float(parts[2]),
                    "battery": float(parts[5]),
                    "temperature": float(parts[6]),
                }

                print(formatter.formatar_config(cfg_data))
                return 0

            except PortBusyError as e:
                return ErrorHandler.tratar_erro_porta_ocupada(e, formatter)
            except SerialError:
                if tentativa < retries - 1:
                    if not formatter.quiet:
                        print_warning(f"Erro na tentativa {tentativa + 1}, tentando novamente...")
                    time.sleep(1)
                    continue
                raise

    except PortBusyError as e:
        return ErrorHandler.tratar_erro_porta_ocupada(e, formatter)
    except SerialError as e:
        return ErrorHandler.tratar_erro_serial(e, formatter)
    except Exception as e:
        return ErrorHandler.tratar_erro_inesperado(e, formatter)


def handle_config_write(args, formatter: OutputFormatter, config: AppConfig) -> int:
    """Escreve configuração diretamente no nobreak via serial"""
    if not SERIAL_AVAILABLE:
        print_error("pyserial não está instalado. Execute: pip install pyserial")
        return 2

    try:
        # Try routing through API if monitor is running (scheduled access)
        if args.beep is not None:
            quiet = getattr(args, 'quiet', False)
            api_result = _try_api_route(config, 'write', quiet=quiet, beep=args.beep)
            if api_result and api_result.get('success'):
                cmds = api_result.get('commands', [])
                if cmds and not quiet:
                    print_success(f"Configuração atualizada: {', '.join(cmds)}")
                return 0
            elif api_result and not api_result.get('success'):
                print_error(f"Erro via API: {api_result.get('error', 'Desconhecido')}")
                return 2

        # Fallback: direct serial access
        comandos_enviados = []

        if args.beep is not None:
            if args.beep not in (0, 1):
                raise ValidationError("beep deve ser 0 (ativado) ou 1 (desativado)")

            timeout = config.getfloat('serial', 'timeout', fallback=5.0)
            retries = config.getint('serial', 'retries', fallback=5)
            busy_max = config.getint('serial', 'busy_max_retries', fallback=6)
            busy_delay = config.getfloat('serial', 'busy_retry_delay', fallback=2.0)

            porta = getattr(args, 'port', None)
            if not porta:
                port_cfg = config.get('serial', 'port', fallback='auto')
                porta = None if port_cfg == 'auto' else port_cfg
            if not porta:
                porta = SerialPortManager.detectar_porta()

            for tentativa in range(retries):
                try:
                    manager = SerialPortManager(porta)
                    manager.abrir_com_retry(
                        timeout=timeout,
                        busy_max_retries=busy_max,
                        busy_retry_delay=busy_delay
                    )

                    manager.escrever("Q1\r")
                    time.sleep(0.5)
                    resposta = manager.ler(timeout=timeout)

                    if not resposta:
                        manager.fechar()
                        if tentativa < retries - 1:
                            if not formatter.quiet:
                                print_warning(f"Tentativa {tentativa + 1} falhou, tentando novamente...")
                            time.sleep(1)
                            continue
                        raise SerialError("Dispositivo não respondeu")

                    clean = resposta.replace('#', '').replace('(', '').strip()
                    parts = clean.split()

                    if len(parts) < 8:
                        manager.fechar()
                        if tentativa < retries - 1:
                            if not formatter.quiet:
                                print_warning("Resposta inválida, tentando novamente...")
                            time.sleep(1)
                            continue
                        raise SerialError(f"Resposta inválida: {resposta}")

                    status_bits = parts[7]
                    beep_atual = int(status_bits[7]) if len(status_bits) >= 8 else 0

                    if beep_atual != args.beep:
                        manager.escrever("Q\r")
                        time.sleep(0.5)
                        manager.ler(timeout=timeout)
                        comandos_enviados.append("Toggle beep (Q)")
                    else:
                        if not formatter.quiet:
                            print_info(f"Beep já está no estado desejado ({args.beep})")

                    manager.fechar()
                    break

                except PortBusyError as e:
                    return ErrorHandler.tratar_erro_porta_ocupada(e, formatter)
                except SerialError:
                    if tentativa < retries - 1:
                        if not formatter.quiet:
                            print_warning(f"Erro na tentativa {tentativa + 1}, tentando novamente...")
                        time.sleep(1)
                        continue
                    raise

        if not comandos_enviados and args.beep is None:
            raise ValidationError("Nenhum parâmetro válido especificado. Use 'tsshara-cli write --help'")

        if comandos_enviados and not formatter.quiet:
            print_success(f"Configuração atualizada: {', '.join(comandos_enviados)}")

        return 0

    except PortBusyError as e:
        return ErrorHandler.tratar_erro_porta_ocupada(e, formatter)
    except ValidationError as e:
        return ErrorHandler.tratar_erro_validacao(e, formatter)
    except SerialError as e:
        return ErrorHandler.tratar_erro_serial(e, formatter)
    except Exception as e:
        return ErrorHandler.tratar_erro_inesperado(e, formatter)


def handle_test_command(args, formatter: OutputFormatter, config: AppConfig) -> int:
    """Processa comandos de teste"""
    if not SERIAL_AVAILABLE:
        print_error("pyserial não está instalado. Execute: pip install pyserial")
        return 2

    try:
        # Try routing through API if monitor is running (scheduled access)
        quiet = getattr(args, 'quiet', False)
        api_test_types = {'10s', 'low', 'beep', 'status', 'firmware', 'timed', 'shutdown'}
        if args.test_command in api_test_types:
            minutes = getattr(args, 'minutes', None)
            api_result = _try_api_route(
                config, f'test/{args.test_command}', quiet=quiet, minutes=minutes
            )
            if api_result is not None:
                resultado = TestResult(
                    comando=api_result.get('command', args.test_command),
                    sucesso=api_result.get('success', False),
                    resposta=api_result.get('response'),
                    erro=api_result.get('error'),
                    porta=api_result.get('port'),
                )
                print(formatter.formatar_resultado_teste(resultado))
                return 0 if resultado.sucesso else 2

        # Fallback: direct serial access
        porta = getattr(args, 'port', None)
        if not porta:
            port_cfg = config.get('serial', 'port', fallback='auto')
            porta = None if port_cfg == 'auto' else port_cfg

        handler = TestHandler(porta, config)

        if args.test_command == '10s':
            resultado = handler.teste_10s()
        elif args.test_command == 'low':
            resultado = handler.teste_low()
        elif args.test_command == 'timed':
            resultado = handler.teste_temporizado(args.minutes)
        elif args.test_command == 'beep':
            resultado = handler.toggle_beep()
        elif args.test_command == 'shutdown':
            resultado = handler.shutdown(args.minutes)
        elif args.test_command == 'status':
            resultado = handler.consultar_status()
        elif args.test_command == 'firmware':
            resultado = handler.consultar_firmware()
        else:
            raise ValidationError(f"Comando de teste desconhecido: {args.test_command}")

        print(formatter.formatar_resultado_teste(resultado))
        return 0 if resultado.sucesso else 2

    except ValidationError as e:
        return ErrorHandler.tratar_erro_validacao(e, formatter)
    except PortBusyError as e:
        return ErrorHandler.tratar_erro_porta_ocupada(e, formatter)
    except SerialError as e:
        return ErrorHandler.tratar_erro_serial(e, formatter)
    except Exception as e:
        return ErrorHandler.tratar_erro_inesperado(e, formatter)


def handle_monitor(args, formatter: OutputFormatter, config: AppConfig) -> int:
    """Processa comando monitor com suporte a config.ini e API"""
    if not SERIAL_AVAILABLE:
        print_error("pyserial não está instalado. Execute: pip install pyserial")
        return 2

    try:
        # --- Ponto de partida: resolve porta: CLI > config > auto ---
        porta = getattr(args, 'port', None)
        if not porta:
            port_cfg = config.get('serial', 'port', fallback='auto')
            porta = None if port_cfg == 'auto' else port_cfg

        # --- Resolve configurações do monitor a partir do config.ini ---
        poll_interval = config.getint('monitor', 'poll_interval', fallback=5)
        timeout = config.getfloat('serial', 'timeout', fallback=5.0)
        retries = config.getint('serial', 'retries', fallback=5)
        battery_threshold = config.getfloat('monitor', 'battery_threshold', fallback=11.0)

        busy_max = config.getint('serial', 'busy_max_retries', fallback=6)
        busy_delay = config.getfloat('serial', 'busy_retry_delay', fallback=2.0)

        # --- Resolve e-mail a partir do config.ini ---
        email_sender = None
        email_enabled = config.getboolean('email', 'enabled', fallback=False)

        email_to = config.get('email', 'to_addrs', fallback='')
        smtp_host = config.get('email', 'smtp_host', fallback='')
        smtp_port = config.getint('email', 'smtp_port', fallback=587)
        smtp_user = config.get('email', 'smtp_user', fallback='')
        smtp_pass = config.get('email', 'smtp_pass', fallback='')
        email_from = config.get('email', 'from_addr', fallback='')
        use_tls = config.getboolean('email', 'use_tls', fallback=True)

        if email_enabled and email_to:
            to_addrs = [e.strip() for e in email_to.replace(';', ',').split(',') if e.strip()]
            if to_addrs:
                if not all([smtp_host, smtp_user, smtp_pass, email_from]):
                    raise ValidationError(
                        "Para enviar e-mails, configure smtp_host, smtp_user, smtp_pass e from_addr "
                        "no config.ini."
                    )
                email_sender = EmailSender(
                    smtp_host=smtp_host, smtp_port=smtp_port,
                    smtp_user=smtp_user, smtp_pass=smtp_pass,
                    from_addr=email_from, to_addrs=to_addrs,
                    use_tls=use_tls
                )

        # --- Resolve intervalo de teste a partir do config.ini ---
        test_interval_seconds = None
        test_schedule = None

        test_interval_str = config.get('monitor', 'test_interval', fallback='')
        if test_interval_str:
            try:
                test_interval_seconds = parse_time_interval(test_interval_str)
            except ValidationError:
                test_schedule = CustomSchedule(test_interval_str)

        # --- Cria e inicia o monitor ---
        monitor = NobreakMonitor(
            porta=porta,
            poll_interval=poll_interval,
            timeout=timeout,
            retries=retries,
            battery_threshold=battery_threshold,
            email_sender=email_sender,
            test_interval_seconds=test_interval_seconds,
            test_schedule=test_schedule,
            busy_max_retries=min(busy_max, 3),  # Menos retries por ciclo no modo monitor
            busy_retry_delay=busy_delay,
        )

        # --- Inicia API se habilitada ---
        api_server = None
        api_enabled = config.getboolean('api', 'enabled', fallback=False)
        if api_enabled:
            api_host = config.get('api', 'host', fallback='0.0.0.0')
            api_port = config.getint('api', 'port', fallback=8080)
            api_auth_user = config.get('api', 'auth_username', fallback='')
            api_auth_pass = config.get('api', 'auth_password', fallback='')
            try:
                api_server = start_api_server(api_host, api_port, monitor, api_auth_user, api_auth_pass)
            except Exception as e:
                print_error(f"Erro ao iniciar API HTTP: {e}")
                logger.error(f"Falha ao iniciar API: {e}")

        try:
            monitor.iniciar()
        finally:
            # Shutdown graceful da API ao sair
            if api_server:
                logger.info("Encerrando API HTTP...")
                try:
                    api_server.shutdown()
                    api_server.server_close()
                    logger.info("API HTTP encerrada.")
                except Exception as e:
                    logger.warning(f"Erro ao encerrar API: {e}")
        return 0

    except ValidationError as e:
        return ErrorHandler.tratar_erro_validacao(e, formatter)
    except PortBusyError as e:
        return ErrorHandler.tratar_erro_porta_ocupada(e, formatter)
    except SerialError as e:
        return ErrorHandler.tratar_erro_serial(e, formatter)
    except Exception as e:
        return ErrorHandler.tratar_erro_inesperado(e, formatter)


def handle_init_config(args, formatter: OutputFormatter, config: AppConfig) -> int:
    """Gera arquivo config.ini padrão"""
    try:
        target_path = config.path
        force = getattr(args, 'force', False)

        if os.path.exists(target_path) and not force:
            print_warning(f"Arquivo já existe: {target_path}")
            print_info("Use --force para sobrescrever.")
            return 1

        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_CONFIG_CONTENT)

        print_success(f"Arquivo de configuração criado: {target_path}")
        print_info("Edite o arquivo para configurar email, serial, API e logging.")
        return 0

    except Exception as e:
        return ErrorHandler.tratar_erro_inesperado(e, formatter)


# ============================================================================
# MODO INTERATIVO (CLI sem argumentos)
# ============================================================================

def _clear_screen():
    """Clear terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')


def _pause():
    """Pause and wait for Enter"""
    print()
    input("Pressione Enter para continuar...")


def _input_prompt(prompt: str, default: str = '') -> str:
    """Get user input with optional default value"""
    if default:
        display = f"{prompt} [{default}]: "
    else:
        display = f"{prompt}: "

    if USE_COLORS and COLORAMA_AVAILABLE:
        value = input(f"  {Fore.WHITE}{display}{Style.RESET_ALL}").strip()
    else:
        value = input(f"  {display}").strip()

    return value if value else default


def _print_menu_header(title: str):
    """Print a colored menu header"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        print(f"\n{Fore.CYAN}{Style.BRIGHT}{'=' * 60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{Style.BRIGHT}  {title}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{Style.BRIGHT}{'=' * 60}{Style.RESET_ALL}\n")
    else:
        print(f"\n{'=' * 60}")
        print(f"  {title}")
        print(f"{'=' * 60}\n")


def _print_menu_separator():
    """Print a colored separator line"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        print(f"{Fore.CYAN}{'─' * 60}{Style.RESET_ALL}")
    else:
        print('─' * 60)


def _print_menu_option(key: str, label: str):
    """Print a menu option"""
    if USE_COLORS and COLORAMA_AVAILABLE:
        print(f"  {Fore.WHITE}{Style.BRIGHT}{key}.{Style.RESET_ALL} {label}")
    else:
        print(f"  {key}. {label}")


def interactive_menu(config: AppConfig):
    """Interactive CLI menu — shown when no arguments are provided"""

    formatter = OutputFormatter("text", verbose=False, quiet=False)

    while True:
        _clear_screen()
        _print_menu_header(f"TSShara CLI v{VERSION} - Menu Interativo")

        # Info
        print_info(f"  Config:  {config.path}")
        print_info(f"  Serial:  {'pyserial disponível' if SERIAL_AVAILABLE else 'NÃO DISPONÍVEL'}")

        porta_cfg = config.get('serial', 'port', fallback='auto')
        print_info(f"  Porta:   {porta_cfg}")

        api_status_str = "desativada"
        if config.getboolean('api', 'enabled', fallback=False):
            try:
                api = ApiClient(config)
                if api.is_running():
                    api_status_str = f"ativa em {api.base_url}"
                else:
                    api_status_str = "configurada mas não ativa"
            except Exception:
                api_status_str = "configurada (erro ao verificar)"
        print_info(f"  API:     {api_status_str}")

        print()
        _print_menu_separator()
        _print_menu_option("1", "Mostrar ID do hardware (nbid)")
        _print_menu_option("2", "Ler configuração do nobreak (read)")
        _print_menu_option("3", "Escrever configuração no nobreak (write)")
        _print_menu_option("4", "Comandos de teste (test)")
        _print_menu_option("5", "Iniciar monitoramento (monitor)")
        _print_menu_option("6", "Gerar config.ini padrão (init-config)")
        _print_menu_option("7", "Editar configurações")
        _print_menu_option("8", "Sair")
        _print_menu_separator()

        print()
        choice = _input_prompt("Escolha uma opção", "8")

        if choice == '1':
            _interactive_nbid(formatter, config)
        elif choice == '2':
            _interactive_read(formatter, config)
        elif choice == '3':
            _interactive_write(formatter, config)
        elif choice == '4':
            _interactive_test_menu(formatter, config)
        elif choice == '5':
            _interactive_monitor(formatter, config)
        elif choice == '6':
            _interactive_init_config(formatter, config)
        elif choice == '7':
            _interactive_config_editor(config)
        elif choice == '8':
            print_info("\nAté logo!")
            break
        else:
            print_warning(f"Opção inválida: {choice}")
            _pause()


def _interactive_nbid(formatter, config):
    """Interactive: show hardware ID"""
    print()
    args = argparse.Namespace(json=False, verbose=False, quiet=False, config=None)
    handle_nbid(args, formatter, config)
    _pause()


def _interactive_read(formatter, config):
    """Interactive: read UPS config"""
    print()
    porta = _input_prompt("Porta serial (Enter para auto)", "")
    args = argparse.Namespace(
        port=porta if porta else None,
        json=False, verbose=True, quiet=False, config=None
    )
    print()
    handle_config_read(args, OutputFormatter("text", verbose=True, quiet=False), config)
    _pause()


def _interactive_write(formatter, config):
    """Interactive: write UPS config"""
    print()
    print_info("Configurações disponíveis para escrita:")
    print("  1. Beep (0=ativado, 1=desativado)")
    print()
    choice = _input_prompt("Escolha", "1")

    if choice == '1':
        beep_val = _input_prompt("Valor do beep (0=ativado, 1=desativado)", "")
        if beep_val in ('0', '1'):
            porta = _input_prompt("Porta serial (Enter para auto)", "")
            args = argparse.Namespace(
                beep=int(beep_val),
                port=porta if porta else None,
                json=False, verbose=False, quiet=False, config=None
            )
            print()
            handle_config_write(args, formatter, config)
        else:
            print_warning("Valor inválido. Use 0 ou 1.")
    _pause()


def _interactive_test_menu(formatter, config):
    """Interactive: test commands submenu"""
    while True:
        _clear_screen()
        _print_menu_header("Comandos de Teste")
        _print_menu_separator()
        _print_menu_option("1", "Teste 10 segundos")
        _print_menu_option("2", "Teste até bateria baixa")
        _print_menu_option("3", "Teste temporizado (X minutos)")
        _print_menu_option("4", "Shutdown (desligar após X minutos)")
        _print_menu_option("5", "Voltar")
        _print_menu_separator()

        print()
        choice = _input_prompt("Escolha", "5")

        test_map = {
            '1': '10s', '2': 'low',
        }

        if choice == '5':
            break
        elif choice in test_map:
            porta = _input_prompt("Porta serial (Enter para auto)", "")
            args = argparse.Namespace(
                test_command=test_map[choice],
                port=porta if porta else None,
                json=False, verbose=False, quiet=False, config=None
            )
            print()
            handle_test_command(args, formatter, config)
            _pause()
        elif choice == '3':
            minutos = _input_prompt("Duração em minutos (0-99)", "1")
            try:
                minutos_int = int(minutos)
                porta = _input_prompt("Porta serial (Enter para auto)", "")
                args = argparse.Namespace(
                    test_command='timed',
                    minutes=minutos_int,
                    port=porta if porta else None,
                    json=False, verbose=False, quiet=False, config=None
                )
                print()
                handle_test_command(args, formatter, config)
            except ValueError:
                print_warning("Valor inválido")
            _pause()
        elif choice == '4':
            minutos = _input_prompt("Desligar após X minutos (0-99)", "1")
            try:
                minutos_int = int(minutos)
                porta = _input_prompt("Porta serial (Enter para auto)", "")
                args = argparse.Namespace(
                    test_command='shutdown',
                    minutes=minutos_int,
                    port=porta if porta else None,
                    json=False, verbose=False, quiet=False, config=None
                )
                print()
                handle_test_command(args, formatter, config)
            except ValueError:
                print_warning("Valor inválido")
            _pause()
        else:
            print_warning(f"Opção inválida: {choice}")
            _pause()


def _interactive_monitor(formatter, config):
    """Interactive: start monitoring"""
    print()
    print_info("Iniciando monitoramento... (Ctrl+C para parar)")
    porta = _input_prompt("Porta serial (Enter para auto)", "")
    args = argparse.Namespace(
        port=porta if porta else None,
        json=False, verbose=False, quiet=False, config=None
    )
    print()
    handle_monitor(args, formatter, config)
    _pause()


def _interactive_init_config(formatter, config):
    """Interactive: generate default config.ini"""
    print()
    force = False
    if os.path.exists(config.path):
        resp = _input_prompt(f"Arquivo já existe: {config.path}. Sobrescrever? (s/N)", "N")
        force = resp.lower() in ('s', 'sim', 'y', 'yes')

    args = argparse.Namespace(force=force, json=False, verbose=False, quiet=False, config=None)
    handle_init_config(args, formatter, config)
    _pause()


# ============================================================================
# EDITOR DE CONFIGURAÇÕES INTERATIVO
# ============================================================================

_CONFIG_SECTIONS = [
    ("1", "Serial", "serial", [
        ("port", "Porta serial (auto ou COMx)"),
        ("baudrate", "Baudrate"),
        ("timeout", "Timeout (s)"),
        ("retries", "Tentativas"),
        ("busy_retry_delay", "Atraso entre retries (s)"),
        ("busy_max_retries", "Máximo de retries"),
    ]),
    ("2", "Monitor", "monitor", [
        ("poll_interval", "Intervalo de polling (s)"),
        ("battery_threshold", "Limite bateria (V)"),
        ("test_interval", "Intervalo de teste (ex: 30s, 5m, wd2h8m15)"),
    ]),
    ("3", "E-mail", "email", [
        ("enabled", "Ativado (true/false)"),
        ("smtp_host", "Servidor SMTP"),
        ("smtp_port", "Porta SMTP"),
        ("smtp_user", "Usuário SMTP"),
        ("smtp_pass", "Senha SMTP"),
        ("from_addr", "E-mail remetente"),
        ("to_addrs", "Destinatários (separados por vírgula)"),
        ("use_tls", "Usar TLS (true/false)"),
    ]),
    ("4", "API", "api", [
        ("enabled", "Ativado (true/false)"),
        ("host", "Endereço de escuta"),
        ("port", "Porta"),
        ("auth_username", "Usuário"),
        ("auth_password", "Senha"),
    ]),
    ("5", "Logging", "logging", [
        ("enabled", "Ativado (true/false)"),
        ("level", "Nível (DEBUG/INFO/WARNING/ERROR)"),
        ("dir", "Diretório"),
        ("rotation", "Rotação diária (true/false)"),
        ("backup_count", "Dias de backup"),
    ]),
    ("6", "Geral", "general", [
        ("color", "Cores (true/false)"),
    ]),
]


def _interactive_config_editor(config: AppConfig):
    """Interactive configuration editor"""
    while True:
        _clear_screen()
        _print_menu_header("Editar Configurações")
        print_info(f"  Arquivo: {config.path}\n")
        _print_menu_separator()

        for key, label, _, _ in _CONFIG_SECTIONS:
            _print_menu_option(key, label)
        _print_menu_option("7", "Voltar")
        _print_menu_separator()

        print()
        choice = _input_prompt("Escolha uma seção", "7")

        if choice == '7':
            break

        section_info = None
        for key, label, section_name, fields in _CONFIG_SECTIONS:
            if key == choice:
                section_info = (label, section_name, fields)
                break

        if not section_info:
            print_warning(f"Opção inválida: {choice}")
            _pause()
            continue

        label, section_name, fields = section_info
        _edit_config_section(config, section_name, label, fields)


def _edit_config_section(config: AppConfig, section: str, label: str, fields: list):
    """Edit a specific config section"""
    _clear_screen()

    if USE_COLORS and COLORAMA_AVAILABLE:
        print(f"\n{Fore.CYAN}{Style.BRIGHT}  Editar: {label}{Style.RESET_ALL}\n")
    else:
        print(f"\n  Editar: {label}\n")

    print_info("  Pressione Enter para manter o valor atual. Digite 'limpar' para limpar.\n")

    changed = False
    for key, description in fields:
        current = config.get(section, key, fallback='')

        # Mask password fields for display
        display_current = current
        if 'pass' in key.lower() and current:
            display_current = '*' * min(len(current), 8)

        if USE_COLORS and COLORAMA_AVAILABLE:
            prompt = f"{Fore.CYAN}{description}{Style.RESET_ALL} [{display_current}]"
        else:
            prompt = f"{description} [{display_current}]"

        new_val = input(f"  {prompt}: ").strip()

        if new_val == 'limpar':
            config._parser.set(section, key, '')
            changed = True
        elif new_val and new_val != current:
            config._parser.set(section, key, new_val)
            changed = True

    if changed:
        print()
        resp = _input_prompt("Salvar alterações? (S/n)", "S")
        if resp.lower() in ('s', 'sim', 'y', 'yes', ''):
            _save_config(config)
            print_success("Configurações salvas!")
        else:
            # Reload config to discard in-memory changes
            config._parser = configparser.ConfigParser()
            config._load_defaults()
            if os.path.exists(config.path):
                config._parser.read(config.path, encoding='utf-8')
            print_info("Alterações descartadas.")
    else:
        print_info("\nNenhuma alteração.")

    _pause()


def _save_config(config: AppConfig):
    """Save current config to file, preserving comments from existing file"""
    try:
        if os.path.exists(config.path):
            with open(config.path, 'r', encoding='utf-8') as f:
                existing_lines = f.readlines()
        else:
            existing_lines = []

        # Build map of section/key -> value from current in-memory config
        values = {}
        for section in config._parser.sections():
            for key, value in config._parser.items(section):
                values[(section, key)] = value

        if existing_lines:
            new_lines = []
            current_section = None
            keys_written = set()

            for line in existing_lines:
                stripped = line.strip()

                # Section header
                section_match = re.match(r'^\[(\w+)\]', stripped)
                if section_match:
                    # Write any new keys for the previous section before moving on
                    if current_section:
                        for (s, k), v in values.items():
                            if s == current_section and (s, k) not in keys_written:
                                new_lines.append(f'{k} = {v}\n')
                                keys_written.add((s, k))

                    current_section = section_match.group(1)
                    new_lines.append(line)
                    continue

                # Key = value line
                key_match = re.match(r'^(\w[\w_]*)\s*=', stripped)
                if key_match and current_section:
                    key = key_match.group(1)
                    if (current_section, key) in values:
                        new_lines.append(f'{key} = {values[(current_section, key)]}\n')
                        keys_written.add((current_section, key))
                    else:
                        new_lines.append(line)
                    continue

                # Comment or blank line — preserve as-is
                new_lines.append(line)

            # Write remaining keys for the last section
            if current_section:
                for (s, k), v in values.items():
                    if s == current_section and (s, k) not in keys_written:
                        new_lines.append(f'{k} = {v}\n')
                        keys_written.add((s, k))

            # Write any sections/keys not in the original file
            for (s, k), v in values.items():
                if (s, k) not in keys_written:
                    if not any(re.match(rf'^\[{s}\]', l.strip()) for l in new_lines):
                        new_lines.append(f'\n[{s}]\n')
                    new_lines.append(f'{k} = {v}\n')
                    keys_written.add((s, k))

            with open(config.path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
        else:
            # No existing file — write fresh with configparser
            with open(config.path, 'w', encoding='utf-8') as f:
                config._parser.write(f)

        logger.info(f"Configuração salva em {config.path}")
    except Exception as e:
        print_error(f"Erro ao salvar configuração: {e}")
        logger.error(f"Erro ao salvar config: {e}")


# ============================================================================
# PRINCIPAL
# ============================================================================

def main():
    """Ponto de entrada principal"""

    # --- Parser global ---
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument('--json', action='store_true', help='Saída em formato JSON')
    global_parser.add_argument('--verbose', action='store_true', help='Saída detalhada')
    global_parser.add_argument('--quiet', action='store_true', help='Suprimir saída não essencial')
    global_parser.add_argument(
        '--config', metavar='PATH', default=None,
        help='Caminho do arquivo de configuração (padrão: config.ini no diretório do script)'
    )

    parser = argparse.ArgumentParser(
        description='TSShara CLI - Gerenciar dispositivos nobreak TS Shara via serial',
        prog='tsshara-cli',
        parents=[global_parser],
        epilog='''
Exemplos:
  %(prog)s nbid
  %(prog)s read --port COM3 --verbose
  %(prog)s write --beep 1
  %(prog)s test status --json
  %(prog)s test 10s --port COM3
  %(prog)s monitor
  %(prog)s monitor --port COM3
  %(prog)s init-config
  %(prog)s init-config --force

Arquivo de Configuração (config.ini):
  Armazena configurações de email, serial, API e logging.
  Gere o arquivo padrão com:  %(prog)s init-config
  Configure email, API, intervalos e demais opções editando o config.ini.

Serviço Windows:
  Use install-windows-service.bat para instalar como serviço (requer NSSM).
  Baixe NSSM de: https://nssm.cc/download

API HTTP:
  Ative com enabled=true na seção [api] do config.ini.
  Endpoints: /api/status  /api/health  /api/info

Porta Ocupada:
  Se o serviço e o CLI tentam usar a porta ao mesmo tempo,
  o programa detecta automaticamente e aguarda a liberação.

Códigos de saída:
  0   Sucesso
  2   Erro de porta serial / porta ocupada
  3   Erro de validação
  99  Erro inesperado
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')

    subparsers = parser.add_subparsers(dest='command', help='Comandos disponíveis')

    # --- nbid ---
    subparsers.add_parser('nbid', help='Mostrar ID do nobreak', parents=[global_parser])

    # --- read ---
    read_parser = subparsers.add_parser('read', help='Ler configuração do nobreak', parents=[global_parser])
    read_parser.add_argument('--port', metavar='PORTA', default=None,
                            help='Porta serial (ex: COM3, auto-detecta se omitido)')

    # --- write ---
    write_parser = subparsers.add_parser('write', help='Escrever configuração no nobreak', parents=[global_parser])
    write_parser.add_argument('--port', metavar='PORTA', default=None,
                             help='Porta serial (ex: COM3, auto-detecta se omitido)')
    write_parser.add_argument('--beep', type=int, choices=[0, 1], metavar='0|1',
                             help='Beep (0=ativado, 1=desativado)')

    # --- test ---
    test_parser = subparsers.add_parser('test', help='Enviar comandos de teste ao nobreak', parents=[global_parser])
    test_subparsers = test_parser.add_subparsers(dest='test_command')

    for name, help_text in [
        ('10s', 'Teste de 10 segundos'),
        ('low', 'Teste até bateria baixa'),
        ('beep', 'Ativar/desativar beep'),
        ('status', 'Consultar status'),
        ('firmware', 'Consultar firmware'),
    ]:
        sub = test_subparsers.add_parser(name, help=help_text, parents=[global_parser])
        sub.add_argument('--port', metavar='PORTA', default=None,
                        help='Porta serial (auto-detecta se omitido)')

    test_timed = test_subparsers.add_parser('timed', help='Testar por X minutos', parents=[global_parser])
    test_timed.add_argument('minutes', type=int, metavar='MINUTOS', help='Duração (0-99)')
    test_timed.add_argument('--port', metavar='PORTA', default=None)

    test_shutdown = test_subparsers.add_parser('shutdown', help='Desligar após X minutos', parents=[global_parser])
    test_shutdown.add_argument('minutes', type=int, metavar='MINUTOS', help='Atraso (0-99)')
    test_shutdown.add_argument('--port', metavar='PORTA', default=None)

    # --- monitor ---
    monitor_parser = subparsers.add_parser(
        'monitor', help='Monitorar nobreak continuamente', parents=[global_parser]
    )
    monitor_parser.add_argument('--port', metavar='PORTA', default=None,
                               help='Porta serial (auto-detecta se omitido)')

    # --- init-config ---
    init_parser = subparsers.add_parser(
        'init-config', help='Gerar arquivo config.ini padrão', parents=[global_parser]
    )
    init_parser.add_argument('--force', action='store_true',
                            help='Sobrescrever arquivo existente')

    # --- Processar argumentos ---
    args = parser.parse_args()

    if not args.command:
        # Interactive mode — no arguments provided
        config_path = args.config if args.config else DEFAULT_CONFIG_PATH
        config = AppConfig(config_path)

        global USE_COLORS
        if not config.getboolean('general', 'color', fallback=True):
            USE_COLORS = False

        setup_logging(config)
        logger.info(f"TSShara CLI v{VERSION} — modo interativo")

        interactive_menu(config)
        sys.exit(0)

    # --- Carregar configuração ---
    config_path = args.config if args.config else DEFAULT_CONFIG_PATH
    config = AppConfig(config_path)

    # --- Configurar cores ---
    if not config.getboolean('general', 'color', fallback=True):
        USE_COLORS = False

    # --- Configurar logging ---
    setup_logging(config)
    logger.info(f"TSShara CLI v{VERSION} — comando: {args.command}")

    # --- Verificar disponibilidade do serial ---
    if not SERIAL_AVAILABLE and args.command in ('read', 'write', 'test', 'monitor'):
        print_warning("AVISO: pyserial não está instalado. Execute: pip install pyserial")

    # --- Formatador de saída ---
    formato = "json" if args.json else "text"
    formatter = OutputFormatter(formato, args.verbose, args.quiet)

    # --- Roteamento para handler ---
    if args.command == 'nbid':
        sys.exit(handle_nbid(args, formatter, config))
    elif args.command == 'read':
        sys.exit(handle_config_read(args, formatter, config))
    elif args.command == 'write':
        sys.exit(handle_config_write(args, formatter, config))
    elif args.command == 'test':
        if args.test_command:
            sys.exit(handle_test_command(args, formatter, config))
        else:
            test_parser.print_help()
            sys.exit(0)
    elif args.command == 'monitor':
        sys.exit(handle_monitor(args, formatter, config))
    elif args.command == 'init-config':
        sys.exit(handle_init_config(args, formatter, config))


if __name__ == '__main__':
    main()
