"""
Лабораторная работа №3 — оконный клиент чата.
Запуск: python client.py
"""
import sys
import socket
import threading
import queue
import time
import re
import errno
import hashlib
import os
import sys
import tempfile
import threading
import time
from collections import deque, Counter
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QListWidget, QListWidgetItem, QTextEdit, QLineEdit,
    QPushButton, QLabel, QGroupBox, QFormLayout, QSpinBox, QMenu,
    QMessageBox, QInputDialog, QComboBox, QStatusBar, QDialog,
    QDialogButtonBox, QDoubleSpinBox
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure



DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5123
PING_INTERVAL_MS = 5000
USERS_REFRESH_MS = 5000
MAX_MESSAGE_LENGTH = 999

PALETTE = [
    "#2878B5", "#D65A31", "#27966F", "#9B59B6",
    "#C18A16", "#D14D72", "#168C9A", "#6B7A32",
    "#7A5AF8", "#A34A28"
]
GENERAL_TEXT_COLOR = "#20242A"
SYSTEM_COLOR = "#7A8491"


# ==================== Встроенная модель статистики ====================
from collections import Counter
from datetime import datetime


class ChatStatistics:
    def __init__(self):
        self.start_time = None
        self.general_total = 0
        self.private_in = 0
        self.private_out = 0
        self.general_by_user = Counter()
        self.total_by_user = Counter()

    def start_session(self):
        self.start_time = datetime.now()

    def record_general(self, user, outgoing=False, count_message=True):
        if count_message:
            self.general_total += 1
            self.general_by_user[user] += 1
            self.total_by_user[user] += 1
        elif outgoing:
            # Эхо собственного сообщения не увеличивает счётчик повторно.
            pass

    def record_private(self, user, incoming=True):
        if incoming:
            self.private_in += 1
        else:
            self.private_out += 1
        self.total_by_user[user] += 1

    def set_users(self, users):
        self.current_users = list(users)

    def summary(self, online_count):
        total = self.general_total + self.private_in + self.private_out
        if self.general_by_user:
            active, amount = self.general_by_user.most_common(1)[0]
            active_text = f"{active} ({amount})"
        else:
            active_text = "пока нет"
        if self.start_time:
            duration = datetime.now() - self.start_time
            duration_text = str(duration).split(".")[0]
        else:
            duration_text = "—"
        return (
            f"Участников онлайн: {online_count}\n"
            f"Общих сообщений за сеанс: {self.general_total}\n"
            f"Личных входящих: {self.private_in}\n"
            f"Личных исходящих: {self.private_out}\n"
            f"Всего личных: {self.private_in + self.private_out}\n"
            f"Всего сообщений за сеанс: {total}\n"
            f"Самый активный участник: {active_text}\n"
            f"Длительность сеанса: {duration_text}"
        )

    def query(self, query, online_users):
        q = query.strip()
        low = q.lower()
        if low in {"help", "помощь", "?"}:
            return ("Запросы статистики:\n"
                    "• введите имя — число сообщений участника за сеанс;\n"
                    "• total — общее число сообщений;\n"
                    "• private — число личных сообщений;\n"
                    "• active — самый активный участник;\n"
                    "• online — число участников онлайн.")
        if low == "total":
            return f"Всего сообщений за сеанс: {self.general_total + self.private_in + self.private_out}"
        if low == "private":
            return f"Личных сообщений за сеанс: {self.private_in + self.private_out}"
        if low == "online":
            return f"Сейчас онлайн: {len(online_users)}"
        if low == "active":
            if not self.general_by_user:
                return "Пока нет общих сообщений."
            name, count = self.general_by_user.most_common(1)[0]
            return f"Самый активный участник: {name} ({count} общих сообщений)."
        if q in self.total_by_user:
            return f"{q}: {self.total_by_user[q]} сообщений за сеанс."
        if q in self.general_by_user:
            return f"{q}: {self.general_by_user[q]} общих сообщений за сеанс."
        return f"Участник «{q}» пока не отправлял сообщений или не найден."


# ==================== Встроенный менеджер Arduino ====================
import errno
import hashlib
import os
import re
import sys
import tempfile
import threading
import time


def clean_text(value):
    if value is None:
        return ""
    for ch in ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\r"):
        value = value.replace(ch, "")
    return value.strip()


TRANSLIT = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh",
    "з":"z","и":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o",
    "п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts",
    "ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"
}

# Arduino Leonardo и распространённые USB-UART мосты, встречающиеся в совместимых платах.
USB_SERIAL_VIDS = {
    0x2341,  # Arduino LLC
    0x2A03,  # Arduino (старые/совместимые идентификаторы)
    0x1A86,  # CH340/CH341
    0x10C4,  # Silicon Labs CP210x
    0x0403,  # FTDI
    0x067B,  # Prolific
}


def to_ascii(text):
    result = []
    for char in text:
        low = char.lower()
        if low in TRANSLIT:
            value = TRANSLIT[low]
            result.append(value.capitalize() if char.isupper() else value)
        else:
            result.append(char)
    return "".join(result)


class PortLock:
    """Межпроцессная блокировка порта; ОС снимает её при завершении процесса."""
    def __init__(self, port_name):
        normalized = os.path.normcase(os.path.realpath(port_name)).lower()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        lock_dir = os.path.join(os.path.expanduser("~"), ".chat-com-locks")
        os.makedirs(lock_dir, exist_ok=True)
        self.path = os.path.join(lock_dir, f"{digest}.lock")
        self.file = None

    def acquire(self):
        self.file = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, os.SEEK_END)
                if self.file.tell() == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                try:
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    self.file.close()
                    self.file = None
                    return False
            else:
                import fcntl
                try:
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError) as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN, None):
                        self.file.close()
                        self.file = None
                        return False
                    raise
            return True
        except Exception:
            if self.file:
                self.file.close()
                self.file = None
            raise

    def release(self):
        if not self.file:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.file.close()
            self.file = None


class ArduinoManager:
    def __init__(self):
        self.serial = None
        self.port_name = None
        self.only_sender_name = True
        self.send_utf8 = True
        self.status_callback = None
        self._lock = threading.RLock()
        self._ready = False
        self._port_lock = None
        self._ports_cache = {}

    @property
    def is_connected(self):
        return self.serial is not None and getattr(self.serial, "is_open", False) and self._ready

    @staticmethod
    def _is_usb_serial_port(port):
        """Отсекает Bluetooth/debug-console и оставляет USB serial устройства."""
        device = (getattr(port, "device", "") or "").lower()
        description = (getattr(port, "description", "") or "").lower()
        manufacturer = (getattr(port, "manufacturer", "") or "").lower()
        product = (getattr(port, "product", "") or "").lower()
        vid = getattr(port, "vid", None)

        if any(word in device for word in ("bluetooth", "debug-console", "debug_console", "wlan")):
            return False

        if vid in USB_SERIAL_VIDS:
            return True

        if sys.platform == "darwin":
            return device.startswith(("/dev/cu.usbmodem", "/dev/cu.usbserial", "/dev/cu.wchusbserial"))
        if sys.platform.startswith("linux"):
            return device.startswith(("/dev/ttyacm", "/dev/ttyusb"))
        if os.name == "nt":
            text = " ".join((description, manufacturer, product))
            return bool(re.search(r"arduino|usb.?serial|ch340|ch341|cp210|ftdi|prolific", text))
        return False

    def list_ports(self):
        try:
            from serial.tools import list_ports
            found = [p for p in list_ports.comports() if self._is_usb_serial_port(p)]
            self._ports_cache = {p.device: p for p in found}
            return sorted(self._ports_cache)
        except Exception:
            self._ports_cache = {}
            return []

    @staticmethod
    def _is_busy_error(exc):
        message = str(exc).lower()
        return (
            isinstance(exc, PermissionError)
            or getattr(exc, "errno", None) in (errno.EBUSY, errno.EACCES, errno.EPERM)
            or any(part in message for part in ("resource busy", "device or resource busy", "permission denied", "access is denied", "access denied", "already open"))
        )

    def connect(self, port_name):
        self.disconnect()
        if not port_name:
            return False, "Не выбран USB-порт Arduino."

        # Повторно проверяем наличие устройства: не доверяем старому списку в ComboBox.
        available = self.list_ports()
        if port_name not in available:
            return False, (
                f"Устройство {port_name} не найдено среди USB-последовательных портов. "
                "Нажмите «Обновить порты» и проверьте USB-подключение."
            )

        lock = PortLock(port_name)
        try:
            if not lock.acquire():
                return False, f"Порт {port_name} уже занят другим экземпляром клиента. Нет доступа к Arduino."
            self._port_lock = lock

            import serial
            serial_args = dict(
                port=port_name,
                baudrate=9600,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                write_timeout=0.5,
            )
            # На macOS/Linux просим драйвер открыть порт эксклюзивно.
            if os.name != "nt":
                serial_args["exclusive"] = True

            opened = serial.Serial(**serial_args)
            if not opened.is_open:
                opened.close()
                raise serial.SerialException("Операционная система не подтвердила открытие порта")

            self.serial = opened
            self.port_name = port_name
            self._ready = False

            # Arduino Leonardo перезапускается после открытия USB CDC.
            time.sleep(2.5)
            if not self.serial or not self.serial.is_open:
                raise serial.SerialException("Порт закрылся во время инициализации платы")

            # Успех означает: найден USB serial-девайс, порт открыт эксклюзивно,
            # резервирование удерживается до disconnect(). Протокольный handshake
            # зависит от прошивки и здесь намеренно не выдумывается.
            self._ready = True
            return True, f"Arduino/USB-устройство подключено: {port_name} (9600, 8N1)"

        except Exception as exc:
            self.disconnect()
            if self._is_busy_error(exc):
                return False, f"Порт {port_name} уже занят другой программой или клиентом. Закройте её и повторите подключение."
            return False, f"Не удалось подключить устройство через {port_name}: {exc}"

    def disconnect(self):
        with self._lock:
            old_serial = self.serial
            old_lock = self._port_lock
            self.serial = None
            self.port_name = None
            self._port_lock = None
            self._ready = False
            try:
                if old_serial and getattr(old_serial, "is_open", False):
                    old_serial.close()
            except Exception:
                pass
            finally:
                if old_lock:
                    old_lock.release()

    def format_for_display(self, line):
        if not line:
            return None
        if line.startswith(("OK ", "TFT ", "READY", ">>", "~")):
            return None

        prefix = ""
        body = line
        if line.startswith("[PM] -> @ "):
            body = line[len("[PM] -> @ "):]
            prefix = "->@"
        elif line.startswith("[PM] @ "):
            body = line[len("[PM] @ "):]
            prefix = "@"

        colon = body.find(":")
        if colon <= 0:
            return None
        who = body[:colon].strip()
        text = body[colon + 1:].strip()
        text = re.sub(r"\s+\(сообщение #(\d+)\)\s*$", r" (#\1)", text)
        if self.only_sender_name:
            return prefix + who
        return prefix + who + ": " + text

    def send_chat_line(self, line):
        formatted = self.format_for_display(line)
        if not formatted or not self.is_connected:
            return
        if not self.send_utf8:
            formatted = to_ascii(formatted)
        formatted = formatted[:160]

        def writer():
            try:
                with self._lock:
                    if self.serial and self.serial.is_open and self._ready:
                        self.serial.write((formatted + "\n").encode("utf-8"))
                        self.serial.flush()
            except Exception as exc:
                failed_port = self.port_name or "Arduino"
                self.disconnect()
                if self.status_callback:
                    self.status_callback(f"[COM] Потеряно соединение с {failed_port}: {exc}")

        threading.Thread(target=writer, daemon=True).start()


class SocketLineReader:
    """Читает TCP-поток построчно, не предполагая, что recv() совпадает с сообщением."""
    def __init__(self, sock):
        self.sock = sock
        self.buffer = bytearray()

    def readline(self):
        while b"\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                if self.buffer:
                    data = bytes(self.buffer)
                    self.buffer.clear()
                    return data.decode("utf-8", errors="replace").rstrip("\r")
                return ""
            self.buffer.extend(chunk)
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        return line.decode("utf-8", errors="replace").rstrip("\r")


def validate_name(name: str):
    name = clean_text(name or "")
    if not name:
        return "Имя не может быть пустым."
    if len(name) > 20:
        return "Имя слишком длинное (максимум 20 символов)."
    if any(ch.isspace() for ch in name):
        return "Имя должно быть одним словом без пробелов и табов."
    if any(ch in name for ch in "@#*:"):
        return "Имя не может содержать символы @, #, *, :."
    if name.lower() in {"exit", "help", "agent", "system", "server", "admin", "root"}:
        return f"Имя «{name}» зарезервировано."
    return None


class GraphCanvas(FigureCanvasQTAgg):
    """График с неизменным диапазоном X при любом размере области."""
    def __init__(self, parent=None):
        self.figure = Figure(figsize=(6, 2.7), tight_layout=True)
        super().__init__(self.figure)
        self.setParent(parent)
        self.ax = self.figure.add_subplot(111)
        self.xmin, self.xmax = 0.0, 10.0
        self.ymin, self.ymax = -1.5, 1.5
        self.ax.set_xlim(self.xmin, self.xmax)
        self.ax.set_ylim(self.ymin, self.ymax)
        self.ax.set_xlabel("x")
        self.ax.set_ylabel("y")
        self.ax.set_title("График функции y = sin(x)")
        self.ax.grid(True, alpha=0.3)
        self.ax.axhline(0, color="#555555", linewidth=0.8)
        self.ax.axvline(0, color="#555555", linewidth=0.8)
        self.x_values = [self.xmin + i * (self.xmax-self.xmin) / 1000 for i in range(1001)]
        self.y_values = [__import__("math").sin(x) for x in self.x_values]
        self.ax.plot(self.x_values, self.y_values, linewidth=2, color="#2878B5")
        self.ax.set_xticks([0, 2, 4, 6, 8, 10])
        self.ax.set_yticks([-1, -0.5, 0, 0.5, 1])
        self.draw()

    def resizeEvent(self, event):
        # Matplotlib пересчитывает геометрию холста; пределы осей намеренно неизменны.
        super().resizeEvent(event)
        self.ax.set_xlim(self.xmin, self.xmax)
        self.ax.set_ylim(self.ymin, self.ymax)
        self.draw_idle()


class ChatClientWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Оконный клиент чата — не подключён")
        self.resize(1450, 900)
        self.setMinimumSize(1050, 700)

        self.sock = None
        self.reader_thread = None
        self.reader_stop = threading.Event()
        self.incoming = queue.Queue()
        self.connected = False
        self.my_name = ""
        self.users = set()
        self.user_colors = {}
        self.color_index = 0
        self.pending_general_echoes = deque()
        self.pending_pm_echoes = deque()
        self.users_block = False
        self.users_buffer = []
        self.last_users_request = 0.0

        self.stats = ChatStatistics()
        self.arduino = ArduinoManager()
        self._build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._process_incoming)
        self.poll_timer.start(100)

        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self._send_ping)
        self.users_timer = QTimer(self)
        self.users_timer.timeout.connect(self._request_users)

        self.arduino.status_callback = self._show_status

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)

        connection = QGroupBox("Подключение к серверу")
        conn_layout = QHBoxLayout(connection)
        self.host_edit = QLineEdit(DEFAULT_HOST)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)
        self.connect_button = QPushButton("Подключиться")
        self.disconnect_button = QPushButton("Отключиться")
        self.disconnect_button.setEnabled(False)
        conn_layout.addWidget(QLabel("Адрес:"))
        conn_layout.addWidget(self.host_edit, 2)
        conn_layout.addWidget(QLabel("Порт:"))
        conn_layout.addWidget(self.port_spin)
        conn_layout.addWidget(self.connect_button)
        conn_layout.addWidget(self.disconnect_button)
        main.addWidget(connection)

        splitter = QSplitter(Qt.Horizontal)
        main.addWidget(splitter, 5)

        # Левая колонка — пользователи и управление.
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Участники чата"))
        self.users_list = QListWidget()
        self.users_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.users_list.customContextMenuRequested.connect(self._user_context_menu)
        left_layout.addWidget(self.users_list, 1)
        self.rename_button = QPushButton("Переименовать себя")
        self.rename_button.setEnabled(False)
        left_layout.addWidget(self.rename_button)

        arduino_box = QGroupBox("Arduino Leonardo / TFT")
        arduino_layout = QFormLayout(arduino_box)
        self.port_combo = QComboBox()
        self.refresh_ports_button = QPushButton("Обновить порты")
        port_row = QWidget()
        port_row_layout = QHBoxLayout(port_row)
        port_row_layout.setContentsMargins(0, 0, 0, 0)
        port_row_layout.addWidget(self.port_combo, 1)
        port_row_layout.addWidget(self.refresh_ports_button)
        self.arduino_connect_button = QPushButton("Подключить Arduino")
        self.arduino_disconnect_button = QPushButton("Отключить Arduino")
        self.arduino_disconnect_button.setEnabled(False)
        self.arduino_status = QLabel("Не подключена")
        self.mode_button = QPushButton("Режим дисплея: имя")
        self.enc_button = QPushButton("Кодировка: UTF-8")
        arduino_layout.addRow("COM-порт:", port_row)
        arduino_layout.addRow(self.arduino_connect_button)
        arduino_layout.addRow(self.arduino_disconnect_button)
        arduino_layout.addRow("Состояние:", self.arduino_status)
        arduino_layout.addRow(self.mode_button)
        arduino_layout.addRow(self.enc_button)
        left_layout.addWidget(arduino_box)
        splitter.addWidget(left)

        # Центральная колонка — чат.
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(QLabel("Лента сообщений"))
        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setStyleSheet("QTextEdit { background: #FFFFFF; }")
        center_layout.addWidget(self.chat_view, 1)
        send_row = QHBoxLayout()
        self.message_edit = QLineEdit()
        self.message_edit.setPlaceholderText("Введите сообщение и нажмите Enter…")
        self.send_button = QPushButton("Отправить")
        self.message_edit.setEnabled(False)
        self.send_button.setEnabled(False)
        send_row.addWidget(self.message_edit, 1)
        send_row.addWidget(self.send_button)
        center_layout.addLayout(send_row)
        splitter.addWidget(center)

        # Правая колонка — статистика и параметры.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        stats_box = QGroupBox("Статистика чата")
        stats_layout = QVBoxLayout(stats_box)
        self.stats_view = QTextEdit()
        self.stats_view.setReadOnly(True)
        self.stats_view.setMaximumHeight(210)
        stats_layout.addWidget(self.stats_view)
        query_row = QHBoxLayout()
        self.stats_query = QLineEdit()
        self.stats_query.setPlaceholderText("Имя участника или запрос: help")
        self.stats_query_button = QPushButton("Выполнить")
        query_row.addWidget(self.stats_query, 1)
        query_row.addWidget(self.stats_query_button)
        stats_layout.addLayout(query_row)
        self.stats_answer = QLabel("Введите имя участника, чтобы узнать число его сообщений.")
        self.stats_answer.setWordWrap(True)
        stats_layout.addWidget(self.stats_answer)
        right_layout.addWidget(stats_box)

        graph_box = QGroupBox("График функции")
        graph_layout = QVBoxLayout(graph_box)
        self.graph = GraphCanvas()
        graph_layout.addWidget(self.graph)
        range_label = QLabel("Диапазон X: [0; 10] — фиксирован при изменении размера окна")
        range_label.setStyleSheet("color: #657080;")
        graph_layout.addWidget(range_label)
        right_layout.addWidget(graph_box, 1)
        splitter.addWidget(right)
        splitter.setSizes([260, 620, 500])

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Готов к подключению")

        self.connect_button.clicked.connect(self.connect_to_server)
        self.disconnect_button.clicked.connect(self.disconnect_from_server)
        self.rename_button.clicked.connect(self.rename_self)
        self.send_button.clicked.connect(self.send_chat_message)
        self.message_edit.returnPressed.connect(self.send_chat_message)
        self.stats_query_button.clicked.connect(self.run_stats_query)
        self.stats_query.returnPressed.connect(self.run_stats_query)
        self.refresh_ports_button.clicked.connect(self.refresh_serial_ports)
        self.arduino_connect_button.clicked.connect(self.connect_arduino)
        self.arduino_disconnect_button.clicked.connect(self.disconnect_arduino)
        self.mode_button.clicked.connect(self.toggle_mode)
        self.enc_button.clicked.connect(self.toggle_encoding)
        self.refresh_serial_ports()

        self._update_stats_view()

    def _show_status(self, message):
        self.incoming.put(("status", message))

    def _append_system(self, text):
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(SYSTEM_COLOR))
        fmt.setFontItalic(True)
        cursor.insertText(text + "\n", fmt)
        self.chat_view.setTextCursor(cursor)
        self.chat_view.ensureCursorVisible()

    def _color_for(self, name):
        if name not in self.user_colors:
            self.user_colors[name] = PALETTE[self.color_index % len(PALETTE)]
            self.color_index += 1
        return self.user_colors[name]

    def _append_message(self, sender, body, private=False, outgoing=False, recipient=None):
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        name_fmt = QTextCharFormat()
        name_fmt.setForeground(QColor(self._color_for(sender)))
        name_fmt.setFontWeight(700)
        body_fmt = QTextCharFormat()
        body_fmt.setForeground(QColor(self._color_for(sender) if private else GENERAL_TEXT_COLOR))
        if private:
            prefix = f"@{sender}: " if not outgoing else f"→ {recipient}: "
            cursor.insertText(prefix, name_fmt)
            cursor.insertText(body + "\n", body_fmt)
        else:
            cursor.insertText(f"{sender}: ", name_fmt)
            cursor.insertText(body + "\n", body_fmt)
        self.chat_view.setTextCursor(cursor)
        self.chat_view.ensureCursorVisible()
        self._send_chat_line_to_arduino(sender, body, private, outgoing, recipient)

    def _send_chat_line_to_arduino(self, sender, body, private, outgoing, recipient):
        if not self.arduino.is_connected:
            return
        if outgoing and private:
            raw = f"@{self.my_name}>{recipient}: {body}"
        elif private:
            raw = f"@{sender}: {body}"
        else:
            raw = f"{sender}: {body}"
        self.arduino.send_chat_line(raw)

    def _update_users(self):
        ordered = []
        if self.my_name:
            ordered.append(self.my_name)
        ordered.extend(sorted(u for u in self.users if u != self.my_name))
        self.users_list.clear()
        for name in ordered:
            label = f"{name} (вы)" if name == self.my_name else name
            item = QListWidgetItem(label)
            item.setForeground(QColor(self._color_for(name)))
            if name == self.my_name:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.users_list.addItem(item)
        self.stats.set_users(ordered)
        self._update_stats_view()

    def _connect_socket(self, host, port):
        """
        Подключение к TCP-серверу.
        Поддерживает серверы, которые сначала отправляют приглашение,
        и серверы, которые сразу ожидают имя пользователя.
        """
        sock = socket.create_connection((host, port), timeout=20)
        sock.settimeout(20)

        reader = SocketLineReader(sock)

        prompt = ""

        try:
            prompt = reader.readline()
        except socket.timeout:
            # Сервер не отправил приглашение — возможно, он сразу ждёт имя.
            pass

        if prompt:
            self.incoming.put(("system", prompt))

        return sock, reader

    def connect_to_server(self):
        if self.connected:
            return

        host = self.host_edit.text().strip()
        port = self.port_spin.value()

        if not host:
            QMessageBox.warning(
                self,
                "Ошибка",
                "Введите IP-адрес сервера."
            )
            return

        name, ok = QInputDialog.getText(
            self,
            "Регистрация",
            "Введите имя (одно слово, до 20 символов):"
        )

        if not ok:
            return

        name = clean_text(name or "")
        error = validate_name(name)

        if error:
            QMessageBox.warning(self, "Некорректное имя", error)
            return

        self.connect_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        sock = None

        try:
            sock, reader = self._connect_socket(host, port)

            # Отправляем имя серверу.
            sock.sendall((name + "\n").encode("utf-8"))

            # Ждём ответ сервера.
            sock.settimeout(5)

            response = ""

            try:
                response = reader.readline()
            except socket.timeout:
                raise TimeoutError(
                    "Сервер не ответил на регистрацию за 5 секунд.\n\n"
                    "Проверьте, что сервер запущен и ожидает подключения "
                    "на указанном порту."
                )

            if not response:
                raise ConnectionError(
                    "Сервер закрыл соединение во время регистрации."
                )

            self.incoming.put(("system", response))

            low = response.lower()

            if any(word in low for word in (
                    "ошибка",
                    "занят",
                    "некоррект",
                    "запрещено"
            )):
                raise RuntimeError(
                    "Сервер отклонил регистрацию:\n" + response
                )

            # Подключение установлено.
            sock.settimeout(None)

            self.sock = sock
            self.my_name = name
            self.connected = True

            self.reader_stop.clear()
            self.users = {name}

            self.stats.start_session()
            self._update_users()

            self.reader_thread = threading.Thread(
                target=self._reader_loop,
                args=(reader,),
                daemon=True
            )
            self.reader_thread.start()

            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(True)
            self.rename_button.setEnabled(True)
            self.message_edit.setEnabled(True)
            self.send_button.setEnabled(True)

            self.message_edit.setFocus()

            self.heartbeat_timer.start(PING_INTERVAL_MS)
            self.users_timer.start(USERS_REFRESH_MS)

            self.setWindowTitle(
                f"Оконный клиент чата — {name}"
            )

            self.statusBar().showMessage(
                f"Подключён к {host}:{port}"
            )

            self._append_system(
                f"Подключение установлено. Вы вошли как {name}."
            )

            self._send_raw("#users")

        except socket.timeout:
            QMessageBox.critical(
                self,
                "Тайм-аут",
                "Время ожидания подключения истекло.\n\n"
                "Проверьте IP-адрес, порт и состояние сервера."
            )

        except ConnectionRefusedError:
            QMessageBox.critical(
                self,
                "Сервер недоступен",
                f"Подключение отклонено.\n\n"
                f"Адрес: {host}\n"
                f"Порт: {port}\n\n"
                "Возможные причины:\n"
                "• сервер не запущен;\n"
                "• указан неправильный порт;\n"
                "• сервер слушает другой сетевой интерфейс;\n"
                "• соединение блокируется сетью."
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Ошибка подключения",
                str(exc)
            )

        finally:
            if not self.connected:
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

                self.sock = None
                self.connect_button.setEnabled(True)

            QApplication.restoreOverrideCursor()

    def _reader_loop(self, stream):
        try:
            while not self.reader_stop.is_set():
                line = stream.readline()

                if not line:
                    break

                self.incoming.put(
                    ("line", line.rstrip("\r\n"))
                )

        except (OSError, UnicodeError, ValueError) as exc:
            if not self.reader_stop.is_set():
                self.incoming.put(
                    ("status", f"Ошибка чтения сервера: {exc}")
                )

        finally:
            if not self.reader_stop.is_set():
                self.incoming.put(
                    ("disconnected", "Соединение с сервером закрыто.")
                )

    def _process_incoming(self):
        changed_stats = False
        while True:
            try:
                kind, payload = self.incoming.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                changed_stats |= self._handle_server_line(payload)
            elif kind == "system":
                self._append_system(payload)
            elif kind == "status":
                self._append_system(payload)
                self.statusBar().showMessage(payload, 8000)
            elif kind == "disconnected":
                if self.connected:
                    self._append_system(payload)
                    self._set_disconnected_state()
        if changed_stats:
            self._update_stats_view()

    def _handle_server_line(self, line):
        if not line:
            return False
        lower = line.lower()

        if "пользователи онлайн" in lower or lower.startswith("пользователи:"):
            self.users_block = True
            self.users_buffer = []
            # Вариант сервера со списком в той же строке.
            if ":" in line and "," in line:
                self.users_buffer.extend(x.strip() for x in line.split(":", 1)[1].split(",") if x.strip())
            return False
        if self.users_block and (line.strip().startswith("-") or line.startswith("  ")):
            candidate = line.strip().lstrip("-").strip()
            if candidate:
                self.users_buffer.append(candidate)
                self.users = set(self.users_buffer)
                self.users.add(self.my_name)
                self._update_users()
            return False
        if self.users_block:
            if self.users_buffer:
                self.users = set(self.users_buffer)
                self.users.add(self.my_name)
                self._update_users()
            self.users_block = False

        # Служебные уведомления переименования.
        rename_match = re.search(r"\*\*\*\s*(\S+)\s+сменил имя на\s+(\S+)\s*\*\*\*", line)
        if rename_match:
            old, new = rename_match.groups()
            self.users.discard(old)
            self.users.add(new)
            if old == self.my_name:
                self.my_name = new
                self.setWindowTitle(f"Оконный клиент чата — {new}")
            if old in self.user_colors:
                self.user_colors[new] = self.user_colors.pop(old)
            self._append_system(line)
            self._update_users()
            return True

        # Служебные строки о входе/выходе.
        if line.startswith("***"):
            self._append_system(line)
            m = re.search(r"\*\*\*\s*(\S+)\s+(?:присоединился|покинул)", line)
            if m:
                if "покинул" in line:
                    self.users.discard(m.group(1))
                else:
                    self.users.add(m.group(1))
                self._update_users()
            return True

        # Формат Java-сервера: [PM] @ sender: text / [PM] -> @ recipient: text
        if line.startswith("[PM] @ "):
            body = line[len("[PM] @ "):]
            if ":" in body:
                sender, message = body.split(":", 1)
                sender, message = sender.strip(), message.strip()
                self._append_message(sender, message, private=True)
                self.stats.record_private(sender, incoming=True)
                return True
        if line.startswith("[PM] -> @ "):
            body = line[len("[PM] -> @ "):]
            if ":" in body:
                recipient, message = body.split(":", 1)
                recipient, message = recipient.strip(), message.strip()
                self._append_message(self.my_name, message.strip(), private=True, outgoing=True, recipient=recipient)
                self.stats.record_private(self.my_name, incoming=False)
                return True

        # Формат из ТЗ другого типового сервера: @Имя: сообщение.
        if line.startswith("@") and ":" in line:
            sender, message = line[1:].split(":", 1)
            sender, message = sender.strip(), message.strip()
            self.users.add(sender)
            self._append_message(sender, message, private=True)
            self.stats.record_private(sender, incoming=True)
            self._update_users()
            return True

        # Общая строка: имя отделяется по первому двоеточию.
        if ": " in line and not line.startswith(("OK ", "TFT ", "READY", ">>", "~")):
            sender, message = line.split(":", 1)
            sender = sender.strip()
            message = message.strip()
            if sender and " " not in sender and len(sender) <= 20:
                if sender == self.my_name and self.pending_general_echoes:
                    expected = self.pending_general_echoes[0]
                    if message.startswith(expected):
                        self.pending_general_echoes.popleft()
                        # Собственное сообщение уже показано при отправке; Arduino получает его там.
                        self.stats.record_general(sender, outgoing=True, count_message=False)
                        return True
                self.users.add(sender)
                self._append_message(sender, message, private=False)
                self.stats.record_general(sender, outgoing=(sender == self.my_name))
                self._update_users()
                return True

        # Не выводим автоматические строки справки и ответов как сообщения чата.
        self._append_system(line)
        if "уже занято" in lower or lower.startswith("ошибка:"):
            self.statusBar().showMessage(line, 8000)
        return False

    def _send_raw(self, line):
        if not self.connected or not self.sock:
            return False
        try:
            self.sock.sendall((line + "\n").encode("utf-8"))
            return True
        except OSError as exc:
            self._append_system(f"Не удалось отправить данные: {exc}")
            self._set_disconnected_state()
            return False

    def _send_ping(self):
        self._send_raw("#ping")

    def _request_users(self):
        if self.connected:
            self._send_raw("#users")

    def send_chat_message(self):
        if not self.connected:
            return
        line = clean_text(self.message_edit.text() or "")
        if not line:
            return
        if len(line) > MAX_MESSAGE_LENGTH:
            QMessageBox.warning(self, "Слишком длинное сообщение", "Максимальная длина — 999 символов.")
            return
        self.message_edit.clear()
        self.message_edit.setFocus()

        # Локальные команды прежнего клиента не отправляются серверу.
        if line.lower() == "#mode":
            self.toggle_mode()
            return
        if line.lower() == "#enc":
            self.toggle_encoding()
            return
        if line.lower() == "#exit":
            self.disconnect_from_server()
            return

        if line.startswith("#"):
            self._send_raw(line)
            return

        if self._send_raw(line):
            self.pending_general_echoes.append(line)
            self._append_message(self.my_name, line, private=False)
            self.stats.record_general(self.my_name, outgoing=True)
            self._update_stats_view()

    def rename_self(self):
        if not self.connected:
            return
        new_name, ok = QInputDialog.getText(self, "Переименование", "Новое имя:", text=self.my_name)
        if not ok:
            return
        new_name = clean_text(new_name or "")
        error = validate_name(new_name)
        if error:
            QMessageBox.warning(self, "Некорректное имя", error)
            return
        if new_name == self.my_name:
            return
        self._send_raw("#NewName " + new_name)

    def _user_context_menu(self, point):
        item = self.users_list.itemAt(point)
        if not item or not self.connected:
            return
        target = item.text().replace(" (вы)", "")
        if target == self.my_name:
            return
        menu = QMenu(self)
        action = QAction("Личное сообщение…", self)
        menu.addAction(action)
        selected = menu.exec(self.users_list.mapToGlobal(point))
        if selected == action:
            text, ok = QInputDialog.getText(self, "Личное сообщение", f"Сообщение для {target}:")
            if ok and clean_text(text or ""):
                message = clean_text(text)
                if len(message) > MAX_MESSAGE_LENGTH:
                    QMessageBox.warning(self, "Слишком длинное сообщение", "Максимальная длина — 999 символов.")
                    return
                if self._send_raw(f"#ToUser {target} {message}"):
                    self.pending_pm_echoes.append((target, message))

    def run_stats_query(self):
        query = clean_text(self.stats_query.text() or "")
        if not query:
            return
        answer = self.stats.query(query, self.users)
        self.stats_answer.setText(answer)

    def _update_stats_view(self):
        self.stats_view.setPlainText(self.stats.summary(len(self.users)))

    def refresh_serial_ports(self):
        self.port_combo.clear()
        ports = self.arduino.list_ports()
        self.port_combo.addItems(ports)
        if not ports:
            self.port_combo.addItem("Порты не найдены")

    def connect_arduino(self):
        port = self.port_combo.currentText()
        if not port or port == "Порты не найдены":
            QMessageBox.information(self, "Arduino", "Сначала подключите плату и обновите список портов.")
            return
        ok, message = self.arduino.connect(port)
        self.arduino_status.setText(message)
        self.arduino_connect_button.setEnabled(not ok)
        self.arduino_disconnect_button.setEnabled(ok)
        self.statusBar().showMessage(message, 8000)
        if ok:
            self._append_system(message)

    def disconnect_arduino(self):
        self.arduino.disconnect()
        self.arduino_status.setText("Не подключена")
        self.arduino_connect_button.setEnabled(True)
        self.arduino_disconnect_button.setEnabled(False)

    def toggle_mode(self):
        self.arduino.only_sender_name = not self.arduino.only_sender_name
        self.mode_button.setText("Режим дисплея: имя" if self.arduino.only_sender_name else "Режим дисплея: сообщение")
        self._append_system("[COM-РЕЖИМ] " + ("Только имя отправителя" if self.arduino.only_sender_name else "Полное сообщение"))

    def toggle_encoding(self):
        self.arduino.send_utf8 = not self.arduino.send_utf8
        self.enc_button.setText("Кодировка: UTF-8" if self.arduino.send_utf8 else "Кодировка: ASCII")
        self._append_system("[COM-КОДИРОВКА] " + ("UTF-8 (кириллица)" if self.arduino.send_utf8 else "ASCII (транслит)"))

    def _set_disconnected_state(self):
        self.connected = False
        self.reader_stop.set()
        self.heartbeat_timer.stop()
        self.users_timer.stop()
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.message_edit.setEnabled(False)
        self.send_button.setEnabled(False)
        self.statusBar().showMessage("Не подключён")
        self.setWindowTitle("Оконный клиент чата — не подключён")
        if self.sock:
            try: self.sock.close()
            except OSError: pass
            self.sock = None

    def disconnect_from_server(self):
        if self.connected:
            try:
                self._send_raw("exit")
            except Exception:
                pass
            self._set_disconnected_state()
            self._append_system("Вы отключились от чата.")

    def closeEvent(self, event):
        self.disconnect_from_server()
        self.arduino.disconnect()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Lab 3 Chat Client")
    window = ChatClientWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
