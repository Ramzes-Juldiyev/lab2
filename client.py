#!/usr/bin/env python3
# ЛР4. Клиент удалённого расчёта.
# Расчёты выполняются на сервере, клиент отвечает за управление и визуализацию.

import json
import math
import queue
import socket
import sys
import threading
import uuid

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QSlider,
    QTextEdit, QMessageBox, QSplitter
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure


# ============================================================
# ГРАФИК
# ============================================================

class GraphCanvas(FigureCanvasQTAgg):

    def __init__(self, parent=None):
        self.figure = Figure(figsize=(8, 5), dpi=100)
        super().__init__(self.figure)

        self.setParent(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.ax = self.figure.add_subplot(111)

        self.ax.set_xlabel("x — координата", labelpad=9)
        self.ax.set_ylabel("y — значение функции", labelpad=9)
        self.ax.set_title("Ожидание результатов расчёта", pad=12)

        self.ax.minorticks_on()
        self.ax.grid(True, which="major", alpha=0.28, linewidth=0.8)
        self.ax.grid(True, which="minor", alpha=0.12, linewidth=0.45)
        self.ax.axhline(0, color="#888888", linewidth=0.7)
        self.ax.axvline(0, color="#888888", linewidth=0.7)

        # Основная линия
        self.line, = self.ax.plot(
            [],
            [],
            color="#1976D2",
            linewidth=1.8,
            label="Функция"
        )

        # Точки расчёта
        self.points = self.ax.scatter(
            [],
            [],
            s=13,
            color="#1976D2",
            zorder=3
        )

        # Выделенная точка
        self.selected_point = self.ax.scatter(
            [],
            [],
            s=85,
            color="#E53935",
            edgecolors="white",
            linewidths=1.2,
            zorder=5
        )

        # Подсказка с координатами
        self.annotation = self.ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox=dict(
                boxstyle="round,pad=0.4",
                fc="white",
                ec="#888888",
                alpha=0.95
            ),
            arrowprops=dict(
                arrowstyle="->",
                color="#555555"
            ),
            fontsize=9,
            zorder=10
        )

        self.annotation.set_visible(False)

        self.x_data = []
        self.y_data = []

        self.auto_scale = True
        self.coordinate_callback = None

        # Перемещение графика
        self.pan_active = False
        self.pan_start = None
        self.pan_xlim = None
        self.pan_ylim = None
        self.dragged = False

        # Подключение событий Matplotlib
        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_press)
        self.mpl_connect("button_release_event", self.on_release)
        self.mpl_connect("motion_notify_event", self.on_motion)

        self.figure.tight_layout()

    # --------------------------------------------------------
    # Отображение нового кадра
    # --------------------------------------------------------

    def show_frame(self, frame):

        xs = frame.get("x", [])
        ys = frame.get("y", [])

        if not isinstance(xs, list) or not isinstance(ys, list):
            return

        if len(xs) != len(ys):
            return

        clean_x = []
        clean_y = []

        for x, y in zip(xs, ys):

            try:
                x = float(x)

                if y is None:
                    y = math.nan
                else:
                    y = float(y)

                if not math.isfinite(x):
                    continue

                if not math.isfinite(y):
                    y = math.nan

                clean_x.append(x)
                clean_y.append(y)

            except (TypeError, ValueError):
                continue

        self.x_data = clean_x
        self.y_data = clean_y

        # Обновляем линию и точки
        self.line.set_data(clean_x, clean_y)

        valid_points = [
            (x, y)
            for x, y in zip(clean_x, clean_y)
            if math.isfinite(y)
        ]

        if valid_points:
            px = [p[0] for p in valid_points]
            py = [p[1] for p in valid_points]

            self.points.set_offsets(list(zip(px, py)))
        else:
            self.points.set_offsets([[math.nan, math.nan]])

        self.selected_point.set_offsets([[math.nan, math.nan]])
        self.annotation.set_visible(False)
        if self.coordinate_callback:
            self.coordinate_callback("Координаты: наведите курсор на точку графика")

        formula = frame.get("formula", "Функция")
        t = frame.get("t", 0)

        frame_number = int(frame.get("k", 0)) + 1
        total_frames = int(frame.get("total", frame_number))
        self.ax.set_title(
            f"{formula}    |    кадр {frame_number}/{total_frames}    |    t = {t:.6g}",
            wrap=True
        )

        # Автоматическое масштабирование выполняется,
        # только если пользователь сам не изменил масштаб.
        if self.auto_scale:
            self.fit_view()

        self.draw_idle()

    # --------------------------------------------------------
    # Автоматическое масштабирование
    # --------------------------------------------------------

    def fit_view(self):

        if not self.x_data:
            self.ax.set_xlim(-1, 1)
            self.ax.set_ylim(-1, 1)
            self.draw_idle()
            return

        valid = [
            (x, y)
            for x, y in zip(self.x_data, self.y_data)
            if math.isfinite(y)
        ]

        if not valid:
            xmin = min(self.x_data)
            xmax = max(self.x_data)

            if xmin == xmax:
                xmin -= 1
                xmax += 1

            self.ax.set_xlim(xmin, xmax)
            self.ax.set_ylim(-1, 1)
            self.draw_idle()
            return

        xs = [p[0] for p in valid]
        ys = [p[1] for p in valid]

        xmin = min(xs)
        xmax = max(xs)
        ymin = min(ys)
        ymax = max(ys)

        dx = xmax - xmin
        dy = ymax - ymin

        if dx == 0:
            dx = 1

        if dy == 0:
            dy = max(abs(ymin) * 0.1, 1)

        # Небольшие отступы по краям
        self.ax.set_xlim(
            xmin - dx * 0.04,
            xmax + dx * 0.04
        )

        self.ax.set_ylim(
            ymin - dy * 0.12,
            ymax + dy * 0.12
        )

        self.draw_idle()

    def reset_view(self):
        self.auto_scale = True
        self.fit_view()

    # --------------------------------------------------------
    # Масштабирование относительно курсора
    # --------------------------------------------------------

    def _zoom_at(self, x, y, scale):
        if x is None or y is None or not math.isfinite(scale) or scale <= 0:
            return
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        self.ax.set_xlim(
            x - (x - xlim[0]) * scale,
            x + (xlim[1] - x) * scale
        )
        self.ax.set_ylim(
            y - (y - ylim[0]) * scale,
            y + (ylim[1] - y) * scale
        )
        self.auto_scale = False
        self.draw_idle()

    def on_scroll(self, event):
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return

        # Дробный шаг учитывает плавную прокрутку тачпада.
        step = float(getattr(event, "step", 0) or 0)
        if step == 0:
            button = getattr(event, "button", None)
            step = 1.0 if button == "up" else (-1.0 if button == "down" else 0.0)
        if step == 0:
            return

        scale = math.exp(-step * 0.18)
        scale = min(2.5, max(0.4, scale))
        self._zoom_at(event.xdata, event.ydata, scale)

    def event(self, event):
        # Нативный pinch-to-zoom на тачпадах и сенсорных панелях Qt.
        if event.type() == QEvent.Type.NativeGesture:
            zoom_type = getattr(Qt.NativeGestureType, "ZoomNativeGesture", None)
            if zoom_type is not None and event.gestureType() == zoom_type:
                try:
                    pos = event.position()
                    ratio = float(self.devicePixelRatioF())
                    px = pos.x() * ratio
                    py = self.figure.bbox.height - pos.y() * ratio
                    if not self.ax.bbox.contains(px, py):
                        return super().event(event)
                    x, y = self.ax.transData.inverted().transform((px, py))
                    value = float(event.value())
                    scale = 1.0 / max(0.2, 1.0 + value)
                    self._zoom_at(x, y, min(2.5, max(0.4, scale)))
                    event.accept()
                    return True
                except (AttributeError, TypeError, ValueError):
                    pass
        return super().event(event)

    # --------------------------------------------------------
    # Начало перемещения
    # --------------------------------------------------------

    def on_press(self, event):

        if event.inaxes != self.ax:
            return

        if event.button != 1:
            return

        if event.x is None or event.y is None:
            return

        self.pan_active = True
        self.dragged = False

        self.pan_start = (event.x, event.y)
        self.pan_xlim = self.ax.get_xlim()
        self.pan_ylim = self.ax.get_ylim()

    # --------------------------------------------------------
    # Перемещение графика
    # --------------------------------------------------------

    def on_motion(self, event):

        if self.pan_active:

            if event.x is None or event.y is None:
                return

            dx = event.x - self.pan_start[0]
            dy = event.y - self.pan_start[1]

            if abs(dx) > 3 or abs(dy) > 3:
                self.dragged = True

            if not self.dragged:
                return

            bbox = self.ax.get_window_extent()

            if bbox.width <= 0 or bbox.height <= 0:
                return

            x_range = self.pan_xlim[1] - self.pan_xlim[0]
            y_range = self.pan_ylim[1] - self.pan_ylim[0]

            shift_x = -dx / bbox.width * x_range
            shift_y = -dy / bbox.height * y_range

            self.ax.set_xlim(
                self.pan_xlim[0] + shift_x,
                self.pan_xlim[1] + shift_x
            )

            self.ax.set_ylim(
                self.pan_ylim[0] + shift_y,
                self.pan_ylim[1] + shift_y
            )

            self.auto_scale = False
            self.draw_idle()
            return

        # Если не перемещаем график — ищем ближайшую точку
        if event.inaxes != self.ax or event.xdata is None:
            return

        self.update_hover(event)

    # --------------------------------------------------------
    # Завершение перемещения или выбор точки
    # --------------------------------------------------------

    def on_release(self, event):

        if not self.pan_active:
            return

        was_dragged = self.dragged

        self.pan_active = False
        self.pan_start = None

        if not was_dragged:
            self.select_nearest_point(event)

    # --------------------------------------------------------
    # Поиск ближайшей точки при наведении
    # --------------------------------------------------------

    def find_nearest_point(self, event):

        if event.x is None or event.y is None:
            return None

        best_index = None
        best_distance = float("inf")

        for i, (x, y) in enumerate(zip(self.x_data, self.y_data)):

            if not math.isfinite(y):
                continue

            px, py = self.ax.transData.transform((x, y))

            distance = math.hypot(
                px - event.x,
                py - event.y
            )

            if distance < best_distance:
                best_distance = distance
                best_index = i

        # Радиус поиска в пикселях
        if best_distance <= 14:
            return best_index

        return None

    def update_hover(self, event):

        index = self.find_nearest_point(event)

        if index is None:
            if self.annotation.get_visible():
                self.annotation.set_visible(False)
                self.draw_idle()
            if self.coordinate_callback:
                self.coordinate_callback("Координаты: наведите курсор на точку графика")
            return

        x = self.x_data[index]
        y = self.y_data[index]

        self.annotation.xy = (x, y)
        self.annotation.set_text(
            f"x = {x:.7g}\ny = {y:.7g}"
        )

        self.annotation.set_visible(True)
        if self.coordinate_callback:
            self.coordinate_callback(f"x = {x:.9g}    |    y = {y:.9g}")
        self.draw_idle()

    # --------------------------------------------------------
    # Выбор точки
    # --------------------------------------------------------

    def select_nearest_point(self, event):

        if event.inaxes != self.ax:
            return

        index = self.find_nearest_point(event)

        if index is None:
            return

        x = self.x_data[index]
        y = self.y_data[index]

        self.selected_point.set_offsets([[x, y]])

        self.annotation.xy = (x, y)
        self.annotation.set_text(
            f"Точка №{index + 1}\n"
            f"x = {x:.8g}\n"
            f"y = {y:.8g}"
        )

        self.annotation.set_visible(True)
        if self.coordinate_callback:
            self.coordinate_callback(
                f"Выбрана точка №{index + 1}:  x = {x:.9g}    |    y = {y:.9g}"
            )
        self.draw_idle()

    # --------------------------------------------------------
    # Кнопки масштабирования
    # --------------------------------------------------------

    def zoom(self, factor):

        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        cx = (xlim[0] + xlim[1]) / 2
        cy = (ylim[0] + ylim[1]) / 2

        half_x = (xlim[1] - xlim[0]) * factor / 2
        half_y = (ylim[1] - ylim[0]) * factor / 2

        self.ax.set_xlim(cx - half_x, cx + half_x)
        self.ax.set_ylim(cy - half_y, cy + half_y)

        self.auto_scale = False
        self.draw_idle()


# ============================================================
# ГЛАВНОЕ ОКНО
# ============================================================

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "Лабораторная работа №4 — удалённый расчёт"
        )

        self.resize(1450, 900)

        self.sock = None
        self.send_lock = threading.Lock()

        self.reader_stop = threading.Event()
        self.reader = None

        self.incoming = queue.Queue()

        self.catalog = []
        self.frames = []

        self.running = False
        self.paused = False
        self.run_id = None

        self.param_widgets = []

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_messages)
        self.timer.start(50)

    # --------------------------------------------------------
    # Интерфейс
    # --------------------------------------------------------

    def _build_ui(self):

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        # Подключение
        connection = QGroupBox("Подключение к серверу")
        row = QHBoxLayout(connection)

        self.host_edit = QLineEdit("127.0.0.1")

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(5124)

        self.connect_btn = QPushButton("Подключиться")
        self.disconnect_btn = QPushButton("Отключиться")
        self.disconnect_btn.setEnabled(False)

        self.refresh_btn = QPushButton("Обновить функции")

        row.addWidget(QLabel("Адрес:"))
        row.addWidget(self.host_edit)
        row.addWidget(QLabel("Порт:"))
        row.addWidget(self.port_spin)
        row.addWidget(self.connect_btn)
        row.addWidget(self.disconnect_btn)
        row.addWidget(self.refresh_btn)

        root.addWidget(connection)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Левая панель
        left = QWidget()
        lv = QVBoxLayout(left)

        settings = QGroupBox("Параметры расчёта")
        form = QFormLayout(settings)

        self.function_combo = QComboBox()
        self.function_combo.setEnabled(False)

        self.formula_label = QLabel(
            "Подключитесь к серверу для загрузки функций."
        )
        self.formula_label.setWordWrap(True)

        form.addRow("Функция из DLL:", self.function_combo)
        form.addRow("Формула:", self.formula_label)

        # Диапазоны
        ranges = QGridLayout()

        self.x_from = self._double(0)
        self.x_to = self._double(10)
        self.x_step = self._double(0.05, 0.000001)

        self.t_from = self._double(0)
        self.t_to = self._double(5)
        self.t_step = self._double(0.1, 0.000001)

        fields = [
            ("x от", self.x_from),
            ("x до", self.x_to),
            ("шаг x", self.x_step),
            ("t от", self.t_from),
            ("t до", self.t_to),
            ("шаг t", self.t_step)
        ]

        for i, (label, widget) in enumerate(fields):

            r, c = divmod(i, 3)

            ranges.addWidget(QLabel(label), r, c * 2)
            ranges.addWidget(widget, r, c * 2 + 1)

        form.addRow("Диапазоны:", ranges)

        # Параметры функции
        self.params_box = QWidget()
        self.params_layout = QHBoxLayout(self.params_box)
        self.params_layout.setContentsMargins(0, 0, 0, 0)

        form.addRow("Параметры функции:", self.params_box)

        # Настройки передачи
        extra = QHBoxLayout()

        self.emit_spin = QSpinBox()
        self.emit_spin.setRange(10, 10000)
        self.emit_spin.setValue(200)

        self.complexity_spin = QSpinBox()
        self.complexity_spin.setRange(1, 40000)
        self.complexity_spin.setValue(20000)

        extra.addWidget(QLabel("Интервал emit, мс:"))
        extra.addWidget(self.emit_spin)

        extra.addWidget(QLabel("Нагрузка CPU:"))
        extra.addWidget(self.complexity_spin)

        form.addRow("Передача:", extra)

        lv.addWidget(settings)

        # Управление расчётом
        buttons = QHBoxLayout()

        self.start_btn = QPushButton("Начать расчёт")
        self.pause_btn = QPushButton("Пауза")

        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)

        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.pause_btn)

        lv.addLayout(buttons)

        self.progress_label = QLabel("Ожидание подключения.")
        self.cpu_label = QLabel("Загрузка CPU процесса сервера: —")

        lv.addWidget(self.progress_label)
        lv.addWidget(self.cpu_label)

        # Журнал
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(220)

        lv.addWidget(self.log)

        splitter.addWidget(left)

        # Правая панель — график
        right = QWidget()
        rv = QVBoxLayout(right)

        self.graph = GraphCanvas()

        rv.addWidget(self.graph, 1)

        # Панель управления графиком
        graph_controls = QHBoxLayout()

        self.zoom_in_btn = QPushButton("Приблизить")
        self.zoom_out_btn = QPushButton("Отдалить")
        self.reset_graph_btn = QPushButton("Сбросить масштаб")

        graph_controls.addWidget(self.zoom_in_btn)
        graph_controls.addWidget(self.zoom_out_btn)
        graph_controls.addWidget(self.reset_graph_btn)

        rv.addLayout(graph_controls)

        self.frame_label = QLabel(
            "Контрольные точки появятся после начала расчёта."
        )
        self.graph_info_label = QLabel(
            "Координаты: наведите курсор на точку графика"
        )
        self.graph_info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.graph.coordinate_callback = self.update_graph_info

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setEnabled(False)

        rv.addWidget(self.frame_label)
        rv.addWidget(self.graph_info_label)
        rv.addWidget(self.slider)

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        splitter.setSizes([420, 1000])

        root.addWidget(splitter, 1)

        self.statusBar().showMessage("Не подключён к серверу")

        # События интерфейса
        self.connect_btn.clicked.connect(self.connect_server)
        self.disconnect_btn.clicked.connect(self.disconnect_server)

        self.refresh_btn.clicked.connect(self.request_functions)

        self.function_combo.currentIndexChanged.connect(
            self.function_changed
        )

        self.start_btn.clicked.connect(self.start_or_stop)
        self.pause_btn.clicked.connect(self.pause_or_resume)

        self.slider.valueChanged.connect(self.select_frame)

        self.zoom_in_btn.clicked.connect(
            lambda: self.graph.zoom(0.75)
        )

        self.zoom_out_btn.clicked.connect(
            lambda: self.graph.zoom(1.33)
        )

        self.reset_graph_btn.clicked.connect(
            self.graph.reset_view
        )

    # --------------------------------------------------------
    # Вспомогательные элементы
    # --------------------------------------------------------

    def update_graph_info(self, message):
        self.graph_info_label.setText(message)

    @staticmethod
    def _double(value, minimum=-1e6):

        widget = QDoubleSpinBox()

        widget.setRange(minimum, 1e6)
        widget.setDecimals(6)
        widget.setValue(value)

        return widget

    def add_log(self, message):
        self.log.append(str(message))

    # --------------------------------------------------------
    # Отправка JSON
    # --------------------------------------------------------

    def send(self, obj):

        sock = self.sock

        if sock is None:
            raise OSError("Нет подключения к серверу")

        data = (
            json.dumps(
                obj,
                ensure_ascii=False,
                allow_nan=False
            ) + "\n"
        ).encode("utf-8")

        with self.send_lock:
            if self.sock is not sock:
                raise OSError("Соединение уже закрыто")

            sock.sendall(data)

        self.add_log(
            "→ JSON: " + json.dumps(obj, ensure_ascii=False)
        )

    # --------------------------------------------------------
    # Подключение к серверу
    # --------------------------------------------------------

    def connect_server(self):

        if self.sock:
            return

        host = self.host_edit.text().strip()

        if not host:
            QMessageBox.warning(
                self,
                "Ошибка",
                "Введите адрес сервера."
            )
            return

        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"

        try:

            sock = socket.create_connection(
                (host, self.port_spin.value()),
                timeout=5
            )

            sock.settimeout(None)

            self.sock = sock

            self.reader_stop.clear()

            self.reader = threading.Thread(
                target=self.reader_loop,
                args=(sock,),
                daemon=True
            )

            self.reader.start()

            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)

            self.function_combo.setEnabled(True)

            self.statusBar().showMessage(
                f"Подключено к {host}:{self.port_spin.value()}"
            )

            self.add_log(
                f"Подключено к серверу {host}:{self.port_spin.value()}"
            )

            self.request_functions()

        except OSError as exc:
            self.statusBar().showMessage("Ошибка соединения")
            QMessageBox.critical(
                self,
                "Ошибка подключения",
                str(exc)
            )

    # --------------------------------------------------------
    # Получение сообщений в отдельном потоке
    # --------------------------------------------------------

    def reader_loop(self, sock):

        buffer = bytearray()

        try:

            while not self.reader_stop.is_set():

                chunk = sock.recv(8192)

                if not chunk:
                    break

                buffer.extend(chunk)

                while b"\n" in buffer:

                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)

                    if not line.strip():
                        continue

                    try:

                        message = json.loads(
                            line.decode("utf-8")
                        )

                        self.incoming.put(message)

                    except (ValueError, UnicodeDecodeError):

                        self.incoming.put({
                            "type": "error",
                            "message": "Некорректный JSON от сервера"
                        })

        except OSError as exc:

            if not self.reader_stop.is_set():

                self.incoming.put({
                    "type": "error",
                    "message": f"Соединение потеряно: {exc}"
                })

        finally:

            if not self.reader_stop.is_set():
                self.incoming.put({
                    "type": "disconnected"
                })

    # --------------------------------------------------------
    # Отключение
    # --------------------------------------------------------

    def disconnect_server(self):

        self.reader_stop.set()

        old_sock = self.sock
        self.sock = None

        if old_sock:

            try:
                old_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            try:
                old_sock.close()
            except OSError:
                pass

        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.statusBar().showMessage("Не подключён к серверу")

        self.function_combo.setEnabled(False)
        self.start_btn.setEnabled(False)

        self._finish("Соединение закрыто")

    # --------------------------------------------------------
    # Запрос списка функций из DLL
    # --------------------------------------------------------

    def request_functions(self):

        if not self.sock:
            return

        try:

            self.send({
                "type": "list_functions",
                "rescan": True
            })

        except OSError as exc:
            self.add_log(str(exc))

    # --------------------------------------------------------
    # Изменение выбранной функции
    # --------------------------------------------------------

    def function_changed(self, *_):

        item = self.function_combo.currentData()

        while self.params_layout.count():
            child = self.params_layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()
        self.param_widgets = []

        if not item:
            self.formula_label.setText("Нет доступных функций.")
            return

        self.formula_label.setText(
            item.get("formula", item.get("title", ""))
        )

        # Создаём поля для параметров текущей функции
        for param in item.get("params", []):

            box = QWidget()
            layout = QVBoxLayout(box)

            layout.setContentsMargins(0, 0, 0, 0)

            layout.addWidget(
                QLabel(param["name"])
            )

            spin = self._double(
                float(param.get("default", 0))
            )

            layout.addWidget(spin)

            self.params_layout.addWidget(box)

            self.param_widgets.append(
                (param["name"], spin)
            )

    # --------------------------------------------------------
    # Обработка входящих сообщений
    # --------------------------------------------------------

    def process_messages(self):

        while True:

            try:
                msg = self.incoming.get_nowait()
            except queue.Empty:
                break

            kind = msg.get("type")

            # Список функций
            if kind == "functions":

                self.catalog = msg.get("items", [])

                self.function_combo.blockSignals(True)
                self.function_combo.clear()

                for item in self.catalog:

                    title = (
                        f"{item.get('title')} — "
                        f"{item.get('lib')} "
                        f"({item.get('author', '—')})"
                    )

                    self.function_combo.addItem(
                        title,
                        item
                    )

                self.function_combo.blockSignals(False)

                self.function_changed()

                self.start_btn.setEnabled(
                    bool(self.catalog) and not self.running
                )

                self.add_log(
                    f"Каталог DLL обновлён: {len(self.catalog)} функций"
                )

            # Расчёт начат
            elif kind == "started":

                self.running = True
                self.paused = False
                self.run_id = msg.get("run_id")

                self.start_btn.setEnabled(True)
                self.start_btn.setText("Остановить расчёт")

                self.pause_btn.setEnabled(True)
                self.pause_btn.setText("Пауза")

                self._set_inputs(False)

                self.progress_label.setText(
                    f"Расчёт запущен: "
                    f"{msg.get('frames')} кадров, "
                    f"{msg.get('points')} точек на кадр"
                )

            # Новый кадр
            elif kind == "frame":

                follow_latest = (
                    not self.frames
                    or self.slider.value() >= len(self.frames) - 1
                )
                self.frames.append(msg)
                latest_index = len(self.frames) - 1

                self.slider.blockSignals(True)
                self.slider.setRange(0, latest_index)
                self.slider.setTickInterval(max(1, (latest_index + 1) // 20))
                if follow_latest:
                    self.slider.setValue(latest_index)
                self.slider.setEnabled(True)
                self.slider.blockSignals(False)

                if follow_latest:
                    self.select_frame(latest_index)

                self.add_log(
                    f"Получен кадр {len(self.frames)} "
                    f"| t = {msg.get('t', 0):.5g} "
                    f"| точек = {len(msg.get('x', []))}"
                )

            # Пауза
            elif kind == "paused":

                self.paused = True

                self.pause_btn.setText("Продолжить")

                self.progress_label.setText(
                    "Расчёт приостановлен"
                )

            # Продолжение
            elif kind == "resumed":

                self.paused = False

                self.pause_btn.setText("Пауза")

                self.progress_label.setText(
                    "Расчёт продолжается"
                )

            # Завершение
            elif kind == "done":

                self._finish(
                    f"Расчёт {msg.get('reason')}. "
                    f"Получено кадров: {msg.get('frames')}"
                )

            # Загрузка CPU
            elif kind == "server_cpu":

                self.cpu_label.setText(
                    f"Загрузка CPU процесса сервера: "
                    f"{msg.get('percent', 0):.1f}%"
                )

            # Ошибка
            elif kind == "error":

                error = msg.get("message", "Неизвестная ошибка")

                self.add_log("Ошибка сервера: " + error)

                self.statusBar().showMessage(error)

                if not self.running:
                    self.start_btn.setEnabled(
                        bool(self.sock and self.catalog)
                    )

            # Отключение сервера
            elif kind == "disconnected":

                self.disconnect_server()

                self.add_log(
                    "Соединение закрыто сервером"
                )

    # --------------------------------------------------------
    # Блокировка параметров во время расчёта
    # --------------------------------------------------------

    def _set_inputs(self, enabled):

        widgets = (
            self.function_combo,
            self.x_from,
            self.x_to,
            self.x_step,
            self.t_from,
            self.t_to,
            self.t_step,
            self.emit_spin,
            self.complexity_spin
        )

        for widget in widgets:
            widget.setEnabled(enabled)

        for _, widget in self.param_widgets:
            widget.setEnabled(enabled)

    # --------------------------------------------------------
    # Начало и остановка расчёта
    # --------------------------------------------------------

    def start_or_stop(self):

        # Остановка
        if self.running:

            try:

                self.send({
                    "type": "stop",
                    "run_id": self.run_id
                })

                self.start_btn.setEnabled(False)

                self.progress_label.setText(
                    "Отправлена команда остановки..."
                )

            except OSError as exc:

                self.add_log(str(exc))

            return

        item = self.function_combo.currentData()

        if not item:
            return

        if self.x_from.value() >= self.x_to.value():

            QMessageBox.warning(
                self,
                "Ошибка параметров",
                "Начальное значение x должно быть меньше конечного."
            )

            return

        if self.t_from.value() > self.t_to.value():

            QMessageBox.warning(
                self,
                "Ошибка параметров",
                "Начальное значение t не может быть больше конечного."
            )

            return

        if self.x_step.value() <= 0 or self.t_step.value() <= 0:
            QMessageBox.warning(
                self, "Ошибка параметров",
                "Шаги по x и t должны быть положительными."
            )
            return

        nx = math.floor(
            (self.x_to.value() - self.x_from.value()) / self.x_step.value() + 1e-10
        ) + 1
        nt = math.floor(
            (self.t_to.value() - self.t_from.value()) / self.t_step.value() + 1e-10
        ) + 1
        if nx > 5000:
            QMessageBox.warning(
                self, "Слишком много точек",
                f"Получается {nx} точек на кадр. Максимум — 5000. Увеличьте шаг x."
            )
            return
        if nt > 5000:
            QMessageBox.warning(
                self, "Слишком много кадров",
                f"Получается {nt} кадров. Максимум — 5000. Увеличьте шаг t."
            )
            return
        if nx * nt > 2_000_000:
            QMessageBox.warning(
                self, "Слишком большой расчёт",
                "Общее число вычислений превышает 2 000 000. Увеличьте шаг x или t."
            )
            return

        self.frames.clear()

        self.slider.blockSignals(True)
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.slider.setEnabled(False)
        self.slider.blockSignals(False)

        self.graph.reset_view()

        params = {
            name: widget.value()
            for name, widget in self.param_widgets
        }

        # Идентификатор связывает все сообщения одного расчёта.
        self.run_id = uuid.uuid4().hex

        # Формат запроса полностью соответствует server.py
        request = {
            "type": "start",
            "run_id": self.run_id,

            "lib": item["lib"],
            "func": item["id"],

            "callback": "on_frame",

            "x": {
                "from": self.x_from.value(),
                "to": self.x_to.value(),
                "step": self.x_step.value()
            },

            "t": {
                "from": self.t_from.value(),
                "to": self.t_to.value(),
                "step": self.t_step.value()
            },

            "params": params,

            "emit_ms": self.emit_spin.value(),

            "complexity": self.complexity_spin.value()
        }

        try:

            self.send(request)

            self.start_btn.setEnabled(False)

            self.progress_label.setText(
                "Ожидание запуска расчёта сервером..."
            )

            self.add_log(
                "Параметры расчёта отправлены серверу."
            )

        except OSError as exc:

            self.run_id = None
            self.add_log(str(exc))

            self.start_btn.setEnabled(
                bool(self.sock and self.catalog)
            )

    # --------------------------------------------------------
    # Пауза и продолжение
    # --------------------------------------------------------

    def pause_or_resume(self):

        if not self.running:
            return

        try:

            self.send({
                "type": "resume" if self.paused else "pause",
                "run_id": self.run_id
            })

        except OSError as exc:

            self.add_log(str(exc))

    # --------------------------------------------------------
    # Переключение контрольных кадров
    # --------------------------------------------------------

    def select_frame(self, index):

        if not (0 <= index < len(self.frames)):
            return

        frame = self.frames[index]

        self.graph.show_frame(frame)

        frame_number = int(frame.get("k", index)) + 1
        total_frames = int(frame.get("total", len(self.frames)))
        self.frame_label.setText(
            f"Кадр {frame_number} из {total_frames}"
            f"    |    t = {frame.get('t', 0):.6g}"
            f"    |    {frame.get('formula', '')}"
        )

    # --------------------------------------------------------
    # Завершение расчёта
    # --------------------------------------------------------

    def _finish(self, message):

        self.running = False
        self.paused = False
        self.run_id = None

        self.start_btn.setText("Начать расчёт")

        self.start_btn.setEnabled(
            bool(self.sock and self.catalog)
        )

        self.pause_btn.setText("Пауза")
        self.pause_btn.setEnabled(False)

        self._set_inputs(bool(self.sock))

        self.progress_label.setText(message)

        self.add_log(message)

    # --------------------------------------------------------
    # Закрытие окна
    # --------------------------------------------------------

    def closeEvent(self, event):

        if self.running and self.sock:

            try:
                self.send({
                    "type": "stop",
                    "run_id": self.run_id
                })
            except OSError:
                pass

        self.disconnect_server()

        event.accept()


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
