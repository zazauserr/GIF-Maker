import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import tempfile
import shutil
import subprocess
import sys
import time
import re
import json
from pathlib import Path
import webbrowser
from typing import Optional, List, Dict, Any, Callable, Tuple

# --- Dependency Management ---
def check_and_install_dependencies():
    """Проверяет и устанавливает необходимые зависимости, если они отсутствуют."""
    required = {'yt_dlp': 'yt-dlp', 'PIL': 'Pillow', 'requests': 'requests'}
    if sys.platform == 'win32':
        required['wmi'] = 'wmi'
    missing = []
    for module, package in required.items():
        try:
            if module == 'PIL':
                from PIL import Image, ImageTk
            else:
                __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        msg = f"Не найдены обязательные модули: {', '.join(missing)}.\n\nУстановить их автоматически с помощью pip?"
        if messagebox.askyesno("Проверка зависимостей", msg):
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', *missing])
                messagebox.showinfo("Успех", "Зависимости установлены. Пожалуйста, перезапустите приложение.")
                return False
            except subprocess.CalledProcessError as e:
                messagebox.showerror("Ошибка установки", f"Не удалось установить зависимости:\n{e}\n\nПопробуйте установить их вручную: pip install {' '.join(missing)}")
                return False
        else:
            return False
    return True

if not check_and_install_dependencies():
    sys.exit()

from PIL import Image, ImageTk, ImageSequence
import yt_dlp
import requests
if sys.platform == 'win32':
    try:
        import wmi
    except ImportError:
        wmi = None # Определяем wmi как None, если импорт не удался

# --- Constants ---
URL_PLACEHOLDER = "Insert URL (YouTube, etc.)"
TEMP_GIF_FILENAME = "output.gif"
TEMP_PALETTE_FILENAME = "palette.png"

# --- Utility Classes ---
class CancellableThread(threading.Thread):
    """Поток, который можно безопасно остановить."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        """Устанавливает флаг остановки."""
        self._stop_event.set()

    def stopped(self) -> bool:
        """Проверяет, был ли установлен флаг остановки."""
        return self._stop_event.is_set()

class FFmpegProcessManager:
    """Управляет запуском, отслеживанием прогресса и отменой процесса FFmpeg."""
    def __init__(self, command: List[str], progress_callback: Callable, completion_callback: Callable, total_duration: float = 0):
        self.command = command
        self.progress_callback = progress_callback
        self.completion_callback = completion_callback
        self.total_duration = total_duration
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def run(self):
        """Запускает процесс FFmpeg в отдельном потоке."""
        self.thread = threading.Thread(target=self._run_process, daemon=True)
        self.thread.start()

    def _run_process(self):
        """Внутренний метод с улучшенной обработкой процесса FFmpeg."""
        try:
            # Исправленная подготовка команды
            cmd_str = []
            for arg in self.command:
                if isinstance(arg, Path):
                    # Используем абсолютные пути и правильное экранирование
                    path_str = str(arg.resolve().as_posix()) if sys.platform != 'win32' else str(arg.resolve())
                    cmd_str.append(path_str)
                else:
                    cmd_str.append(str(arg))

            print(f"Executing: {' '.join(cmd_str)}")  # Отладочный вывод

            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            # Улучшенные параметры запуска процесса
            self.process = subprocess.Popen(
                cmd_str,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Изменено: отдельный поток для stderr
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                cwd=str(Path.cwd()),
                env=os.environ.copy()  # Добавлено: копирование окружения
            )

            # Читаем stdout и stderr параллельно
            output_log = []
            error_log = []
            last_progress = -1

            def read_stdout():
                for line in iter(self.process.stdout.readline, ''):
                    if self._stop_event.is_set():
                        break
                    line = line.strip()
                    if line:
                        output_log.append(line)
                        self._process_output_line(line, last_progress)

            def read_stderr():
                for line in iter(self.process.stderr.readline, ''):
                    if self._stop_event.is_set():
                        break
                    line = line.strip()
                    if line:
                        error_log.append(line)
                        self._process_output_line(line, last_progress)

            # Запускаем чтение в отдельных потоках
            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)

            stdout_thread.start()
            stderr_thread.start()

            # Ждем завершения процесса
            return_code = self.process.wait()

            # Ждем завершения чтения выводов
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            self.process.stdout.close()
            self.process.stderr.close()

            if self._stop_event.is_set():
                self.completion_callback(-2, "Процесс отменен пользователем")
            elif return_code != 0:
                # Исправленная обработка кодов ошибок
                if return_code > 2147483647:  # Исправление для больших unsigned значений
                    return_code = return_code - 4294967296

                all_logs = output_log + error_log
                error_lines = [line for line in all_logs if
                            any(keyword in line.lower() for keyword in
                                ['error', 'failed', 'not found', 'invalid', 'cannot', 'permission denied'])]

                if error_lines:
                    error_msg = "\n".join(error_lines[-5:])  # Последние 5 ошибок
                else:
                    error_msg = "\n".join(all_logs[-15:])  # Последние 15 строк

                self.completion_callback(return_code, f"FFmpeg error (код {return_code}):\n{error_msg}")
            else:
                self.completion_callback(0, None)

        except FileNotFoundError:
            self.completion_callback(-1, "FFmpeg не найден. Проверьте путь к исполняемому файлу.")
        except Exception as e:
            self.completion_callback(-1, f"Критическая ошибка: {str(e)}")

    def _process_output_line(self, line: str, last_progress: float):
        """Обрабатывает строку вывода FFmpeg для извлечения прогресса."""
        # Улучшенное распознавание прогресса
        time_match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
        if time_match and self.total_duration > 0:
            h, m, s, ms = map(int, time_match.groups())
            current_time = h * 3600 + m * 60 + s + ms / 100
            progress = min(100, (current_time / self.total_duration) * 100)

            # Обновляем только если прогресс изменился значительно
            if abs(progress - last_progress) > 0.5:
                self.progress_callback(progress, f"Обработка: {progress:.1f}%")
                last_progress = progress
        elif "frame=" in line:
            # Альтернативный способ отслеживания прогресса
            self.progress_callback(-1, "Обработка кадров...")

    def terminate(self):
        """Принудительно завершает процесс FFmpeg."""
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                # На Windows terminate() для ffmpeg может оставлять зомби-процессы
                if sys.platform == 'win32':
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(self.process.pid)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.process.terminate()
                self.process.wait(timeout=5)
            except (subprocess.TimeoutExpired, PermissionError):
                try:
                    self.process.kill()
                    self.process.wait(timeout=2)
                except:
                    pass
            except Exception as e:
                print(f"Error terminating FFmpeg process: {e}")


# --- Custom Widget Toolkit ---
class CustomWidgetHelper:
    """Помощник для создания стилизованных кастомных виджетов."""
    def __init__(self, colors: Dict[str, str], fonts: Dict[str, Tuple]):
        self.colors = colors
        self.fonts = fonts

    def create_rounded_rect(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
        """Рисует прямоугольник со скругленными углами на холсте."""
        points = [
            x1 + radius, y1, x1 + radius, y1, x2 - radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y1 + radius, x2, y2 - radius, x2, y2 - radius, x2, y2, x2 - radius, y2, x2 - radius, y2,
            x1 + radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y2 - radius, x1, y1 + radius,
            x1, y1 + radius, x1, y1, x1 + radius, y1]
        return canvas.create_polygon(points, **kwargs, smooth=True)

    def create_custom_button(self, parent: tk.Widget, text: str, command: Callable, width: int, height: int) -> tk.Canvas:
        """Создает кастомную анимированную кнопку."""
        canvas = tk.Canvas(parent, width=width, height=height, bg=self.colors['bg'], highlightthickness=0)

        btn_shape = self.create_rounded_rect(canvas, 2, 2, width - 2, height - 2, 10, fill=self.colors['bg_accent'], outline="")
        btn_text = canvas.create_text(width / 2, height / 2, text=text, fill=self.colors['text_primary'], font=self.fonts['button'])
        glow_shape = self.create_rounded_rect(canvas, 1, 1, width - 1, height - 1, 11, outline="", fill="")
        canvas.state = 'normal'

        def on_enter(e):
            if canvas.state == 'normal':
                canvas.itemconfig(glow_shape, fill=self.colors['accent'], outline=self.colors['accent'])
                canvas.tag_lower(glow_shape, btn_shape)

        def on_leave(e):
            canvas.itemconfig(glow_shape, fill="")

        def on_click(e):
            if canvas.state == 'normal':
                command()

        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.bind("<Button-1>", on_click)

        def configure_state(new_state: str):
            canvas.state = new_state
            if new_state == 'disabled':
                canvas.itemconfig(btn_shape, fill=self.colors['disabled_bg'])
                canvas.itemconfig(btn_text, fill=self.colors['disabled_fg'])
                canvas.itemconfig(glow_shape, fill="")
                on_leave(None)
            else:
                canvas.itemconfig(btn_shape, fill=self.colors['bg_accent'])
                canvas.itemconfig(btn_text, fill=self.colors['text_primary'])

        canvas.configure_state = configure_state
        return canvas

    def create_custom_entry(self, parent: tk.Widget, textvariable: tk.StringVar, validation_cmd: Tuple) -> Tuple[tk.Frame, tk.Entry]:
        """Создает кастомное поле ввода."""
        container = tk.Frame(parent, bg=self.colors['bg_panel'])
        canvas = tk.Canvas(container, width=200, height=40, bg=self.colors['bg_panel'], highlightthickness=0)
        canvas.pack()

        self.create_rounded_rect(canvas, 1, 1, 199, 39, 10, fill=self.colors['bg_accent'])
        rect_border = self.create_rounded_rect(canvas, 1, 1, 199, 39, 10, outline=self.colors['border'], fill="", width=2)

        entry = tk.Entry(
            container, textvariable=textvariable, bg=self.colors['bg_accent'], fg=self.colors['text_primary'],
            relief='flat', font=self.fonts['body'], insertbackground=self.colors['accent'], justify='center',
            validate='key', validatecommand=validation_cmd
        )
        entry.place(x=10, y=10, width=180, height=20)

        def on_focus_in(e): canvas.itemconfig(rect_border, outline=self.colors['accent'])
        def on_focus_out(e): canvas.itemconfig(rect_border, outline=self.colors['border'])
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

        entry.canvas_border = rect_border
        entry.canvas_ref = canvas
        return container, entry

# --- Main Application ---
class GifStudioPro:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.setup_theme_and_style()

        self.temp_dir: Path = Path(tempfile.mkdtemp(prefix="gif_studio_"))
        self.video_path: Optional[Path] = None
        self.gif_path: Optional[Path] = None
        self.video_info: Optional[Dict[str, Any]] = None
        self.ffmpeg_path: Optional[Path] = self.find_ffmpeg()

        self.active_thread: Optional[CancellableThread] = None
        self.active_ffmpeg_process: Optional[FFmpegProcessManager] = None
        self.is_processing: bool = False

        self.preview_animation_id: Optional[str] = None
        self.animation_frames: List[ImageTk.PhotoImage] = []
        self.animation_frame_delays: List[int] = []
        self.current_frame_index: int = 0

        self.logo_image: Optional[ImageTk.PhotoImage] = None
        self.load_logo()

        self._create_main_layout()
        self.update_ui_state()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        if not self.ffmpeg_path:
            self.root.after(100, self.show_ffmpeg_finder)

    def load_logo(self):
        """Загружает и изменяет размер логотипа из файла bam.png."""
        try:
            logo_path = Path("bam.png")
            if logo_path.exists():
                with Image.open(logo_path) as pil_image:
                    max_height = 80
                    ratio = max_height / pil_image.height
                    new_width = int(pil_image.width * ratio)
                    resized_image = pil_image.resize((new_width, max_height), Image.Resampling.LANCZOS)
                    self.logo_image = ImageTk.PhotoImage(resized_image)
        except Exception as e:
            print(f"Ошибка загрузки логотипа: {e}")
            self.logo_image = None

    def setup_theme_and_style(self):
        """Настраивает внешний вид приложения: цвета, шрифты, стили."""
        self.root.title("GIF Studio Pro")
        self.root.geometry("1100x750")
        self.root.minsize(1000, 650)

        self.colors = {
            'bg': '#0D0221', 'bg_panel': '#140E38', 'bg_accent': '#231955',
            'border': '#45378D', 'text_primary': '#F0F0F0', 'text_secondary': '#A6A6A6',
            'text_title': '#FFFFFF', 'accent': '#00FFFF', 'accent_alt': '#FF00FF',
            'disabled_bg': '#333333', 'disabled_fg': '#6A6A6A', 'gold': '#B8860B',
            'error': '#FF4444'
        }

        self.fonts = {
            "title": ("Consolas", 24, "bold"), "h1": ("Segoe UI", 16, "bold"),
            "h2": ("Segoe UI", 11, "bold"), "body": ("Segoe UI", 10, "normal"),
            "small": ("Segoe UI", 9, "normal"), "button": ("Segoe UI", 10, "bold"),
            "signature": ("Consolas", 10, "italic")
        }

        self.root.configure(bg=self.colors['bg'])
        self.widget_helper = CustomWidgetHelper(self.colors, self.fonts)

    def _create_main_layout(self):
        """Создает и размещает основные элементы интерфейса."""
        header = tk.Frame(self.root, bg=self.colors['bg'])
        header.pack(fill='x', pady=20)

        logo_container = tk.Frame(header, bg=self.colors['bg'])
        logo_container.pack()

        if self.logo_image:
            tk.Label(logo_container, image=self.logo_image, bg=self.colors['bg']).pack()
        else:
            tk.Label(logo_container, text="G I F _ S T U D I O", font=self.fonts['title'],
                     fg=self.colors['text_title'], bg=self.colors['bg']).pack()

        author_label = tk.Label(logo_container, text="created by zazauserr", font=self.fonts['signature'],
                                fg=self.colors['gold'], bg=self.colors['bg'], cursor="hand2")
        author_label.pack(pady=(5, 0))
        author_label.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/zazauserr", new=2))

        main_container = tk.Frame(self.root, bg=self.colors['bg'])
        main_container.pack(fill='both', expand=True, padx=20, pady=(0, 20))
        main_container.grid_columnconfigure(0, weight=3, uniform="group1")
        main_container.grid_columnconfigure(1, weight=5, uniform="group1")
        main_container.grid_rowconfigure(0, weight=1)

        self.left_panel = tk.Frame(main_container, bg=self.colors['bg_panel'])
        self.left_panel.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        self._populate_left_panel()

        self.right_panel = tk.Frame(main_container, bg=self.colors['bg_panel'])
        self.right_panel.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self._populate_right_panel()

    def _create_panel_header(self, parent: tk.Widget, text: str):
        tk.Label(parent, text=text, font=self.fonts['h1'], fg=self.colors['text_primary'], bg=parent.cget('bg')).pack(pady=(20, 15))

    def _populate_left_panel(self):
        """Заполняет левую панель элементами управления."""
        self._create_panel_header(self.left_panel, "Источник")

        url_container = tk.Frame(self.left_panel, bg=self.colors['bg_panel'])
        url_container.pack(fill='x', padx=20, pady=5)

        self.url_var = tk.StringVar()
        url_entry_container, self.url_entry = self.widget_helper.create_custom_entry(url_container, self.url_var, validation_cmd=(self.root.register(lambda P: True), '%P'))
        url_entry_container.pack(side='left', fill='x', expand=True, ipady=5)

        self.url_entry.bind('<FocusIn>', self.on_entry_focus_in)
        self.url_entry.bind('<FocusOut>', self.on_entry_focus_out)
        self.on_entry_focus_out(type('obj', (object,), {'widget': self.url_entry}))

        paste_button = tk.Button(url_container, text="📋", command=self.paste_from_clipboard,
                                 font=("Segoe UI", 12), relief='flat', fg=self.colors['accent'],
                                 bg=self.colors['bg_accent'], activebackground=self.colors['border'],
                                 activeforeground=self.colors['text_primary'], bd=0)
        paste_button.pack(side='left', padx=5, ipady=1)

        self.load_btn = self.widget_helper.create_custom_button(url_container, "UPLOAD", self.start_download, 100, 36)
        self.load_btn.pack(side='right', padx=(5, 0))

        self._create_panel_header(self.left_panel, "Settings")
        settings_frame = tk.Frame(self.left_panel, bg=self.colors['bg_panel'])
        settings_frame.pack(fill='both', expand=True, padx=30, pady=10)

        vcmd = (self.root.register(self.validate_time_input), '%P')

        tk.Label(settings_frame, text="START (sec)", font=self.fonts['h2'], fg=self.colors['text_secondary'], bg=self.colors['bg_panel']).pack()
        self.start_var = tk.StringVar(value="0.0")
        start_cont, self.start_entry = self.widget_helper.create_custom_entry(settings_frame, self.start_var, vcmd)
        start_cont.pack()

        tk.Label(settings_frame, text="END (sec)", font=self.fonts['h2'], fg=self.colors['text_secondary'], bg=self.colors['bg_panel']).pack(pady=(10,0))
        self.end_var = tk.StringVar(value="5.0")
        end_cont, self.end_entry = self.widget_helper.create_custom_entry(settings_frame, self.end_var, vcmd)
        end_cont.pack()

        self.duration_var = tk.StringVar(value="Duration: 5.0 сек")
        tk.Label(self.left_panel, textvariable=self.duration_var, font=self.fonts['body'], fg=self.colors['accent'], bg=self.colors['bg_panel']).pack(pady=5)
        self.start_var.trace_add('write', self.update_duration)
        self.end_var.trace_add('write', self.update_duration)

        params_frame = tk.Frame(settings_frame, bg=self.colors['bg_panel'])
        params_frame.pack(pady=5, fill='x', expand=True)
        self.width_var = tk.StringVar()
        self.fps_var = tk.StringVar()
        self.quality_var = tk.StringVar()
        self._create_setting_control(params_frame, "WIDTH (PX)", self.width_var, ["320", "480", "640", "720"], "480")
        self._create_setting_control(params_frame, "FPS", self.fps_var, ["10", "15", "20", "25", "30"], "25")
        self._create_setting_control(params_frame, "QUALITY", self.quality_var, ["fast", "medium", "high"], "medium")

    def _populate_right_panel(self):
        """Заполняет правую панель элементами управления."""
        self.right_panel.grid_rowconfigure(0, weight=6)
        self.right_panel.grid_rowconfigure(1, weight=2)
        self.right_panel.grid_rowconfigure(2, weight=1)
        self.right_panel.grid_columnconfigure(0, weight=1)

        preview_frame = tk.Frame(self.right_panel, bg=self.colors['bg_accent'])
        preview_frame.grid(row=0, column=0, sticky='nsew', padx=20, pady=(20, 10))
        self.preview_label = tk.Label(preview_frame, text="[ PREVIEW ]", bg=self.colors['bg_accent'], fg=self.colors['border'], font=self.fonts['title'])
        self.preview_label.pack(expand=True, fill='both')

        status_frame = tk.Frame(self.right_panel, bg=self.colors['bg_panel'])
        status_frame.grid(row=1, column=0, sticky='nsew', padx=20)
        self.info_text = tk.Text(status_frame, height=3, font=self.fonts['small'], bg=self.colors['bg_panel'], fg=self.colors['text_secondary'], relief='flat', bd=0, wrap='word', state='disabled')
        self.info_text.pack(fill='x', pady=(5,0))

        self.status_var = tk.StringVar(value="> Ready to go...")
        tk.Label(status_frame, textvariable=self.status_var, font=self.fonts['body'], fg=self.colors['text_primary'], bg=self.colors['bg_panel'], anchor='w', wraplength=500).pack(fill='x')
        self.progress_canvas = tk.Canvas(status_frame, width=400, height=10, bg=self.colors['bg_accent'], highlightthickness=0)
        self.progress_canvas.pack(fill='x', pady=5)
        self.progress_fill = self.widget_helper.create_rounded_rect(self.progress_canvas, 0, 0, 0, 10, 5, fill=self.colors['accent'], outline="")

        actions_frame = tk.Frame(self.right_panel, bg=self.colors['bg_panel'])
        actions_frame.grid(row=2, column=0, sticky='sew', padx=20, pady=(10, 20))
        actions_frame.grid_columnconfigure((0,1), weight=1)

        self.create_btn = self.widget_helper.create_custom_button(actions_frame, "СREATE GIF", self.start_gif_creation, 200, 50)
        self.create_btn.grid(row=0, column=0, sticky='e', padx=5)
        self.save_btn = self.widget_helper.create_custom_button(actions_frame, "СОХРАНИТЬ", self.save_gif, 200, 50)
        self.save_btn.grid(row=0, column=1, sticky='w', padx=5)

        self.cancel_btn = self.widget_helper.create_custom_button(self.right_panel, "ОТМЕНА", self.cancel_operation, 150, 40)

    def _create_setting_control(self, parent: tk.Widget, label_text: str, variable: tk.StringVar, values: List[str], default_value: str):
        """Создает выпадающий список для настроек."""
        frame = tk.Frame(parent, bg=self.colors['bg_panel'])
        frame.pack(fill='x', pady=8)
        tk.Label(frame, text=label_text, font=self.fonts['h2'], fg=self.colors['text_secondary'], bg=self.colors['bg_panel']).pack(side='left')

        style = ttk.Style()
        combo_style_name = f'{label_text}.TCombobox'
        style.theme_use('default')
        style.map(combo_style_name,
                  fieldbackground=[('readonly', self.colors['bg_accent'])],
                  background=[('readonly', self.colors['bg_accent'])],
                  foreground=[('readonly', self.colors['text_primary'])])
        style.configure(combo_style_name, padding=5, bordercolor=self.colors['border'])

        self.root.option_add('*TCombobox*Listbox.background', self.colors['bg_accent'])
        self.root.option_add('*TCombobox*Listbox.foreground', self.colors['text_primary'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', self.colors['accent'])

        combo = ttk.Combobox(frame, textvariable=variable, values=values, font=self.fonts['body'],
                             state='readonly', width=12, style=combo_style_name)
        combo.pack(side='right')
        variable.set(default_value)

    def show_ffmpeg_finder(self):
        """Показывает окно с предложением указать путь к FFmpeg."""
        self.ffmpeg_finder_frame = tk.Frame(self.root, bg=self.colors['bg'], highlightbackground=self.colors['accent'], highlightthickness=1)
        self.ffmpeg_finder_frame.place(relx=0.5, rely=0.5, anchor='center')

        tk.Label(self.ffmpeg_finder_frame, text="FFMPEG НЕ НАЙДЕН", font=self.fonts['h1'], fg=self.colors['accent_alt'], bg=self.colors['bg']).pack(pady=10, padx=20)
        tk.Label(self.ffmpeg_finder_frame, text="FFmpeg необходим для конвертации видео.\nПожалуйста, укажите путь к ffmpeg.exe.", font=self.fonts['body'], fg=self.colors['text_primary'], bg=self.colors['bg']).pack(pady=5, padx=20)

        link = tk.Label(self.ffmpeg_finder_frame, text="Скачать FFmpeg", font=self.fonts['body'], fg=self.colors['accent'], bg=self.colors['bg'], cursor="hand2")
        link.pack(pady=5)
        link.bind("<Button-1>", lambda e: webbrowser.open("https://ffmpeg.org/download.html", new=2))

        self.widget_helper.create_custom_button(self.ffmpeg_finder_frame, "УКАЗАТЬ ПУТЬ", self.select_ffmpeg_path, 180, 40).pack(pady=20)

    def select_ffmpeg_path(self):
        """Открывает диалог выбора файла для ffmpeg.exe."""
        path = filedialog.askopenfilename(title="Выберите ffmpeg.exe", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.ffmpeg_path = Path(path)
            self.ffmpeg_finder_frame.destroy()
            self.update_ui_state()

    # --- UI Event Handlers & Updaters ---
    def on_entry_focus_in(self, event: tk.Event):
        if event.widget.get() == URL_PLACEHOLDER:
            event.widget.delete(0, tk.END)
            event.widget.config(fg=self.colors['text_primary'])

    def on_entry_focus_out(self, event: tk.Event):
        if not event.widget.get().strip():
            event.widget.insert(0, URL_PLACEHOLDER)
            event.widget.config(fg=self.colors['text_secondary'])

    def paste_from_clipboard(self):
        """Вставляет текст из буфера обмена."""
        try:
            clipboard_text = self.root.clipboard_get()
            if clipboard_text:
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, clipboard_text)
                self.url_entry.config(fg=self.colors['text_primary'])
        except tk.TclError:
            pass

    def validate_time_input(self, value: str) -> bool:
        """Валидация ввода времени."""
        if not value:
            return True
        try:
            float(value)
            return True
        except ValueError:
            return False

    def update_duration(self, *args):
        """Обновляет отображение длительности."""
        try:
            start = float(self.start_var.get() or 0)
            end = float(self.end_var.get() or 0)
            duration = max(0, end - start)
            self.duration_var.set(f"Длительность: {duration:.1f} сек")
        except ValueError:
            self.duration_var.set("Длительность: ?.? сек")

    def update_ui_state(self):
        """Обновляет состояние элементов интерфейса."""
        has_video = self.video_path is not None
        has_gif = self.gif_path is not None
        has_ffmpeg = self.ffmpeg_path is not None

        # Управление кнопками
        self.load_btn.configure_state('normal' if has_ffmpeg and not self.is_processing else 'disabled')
        self.create_btn.configure_state('normal' if has_video and has_ffmpeg and not self.is_processing else 'disabled')
        self.save_btn.configure_state('normal' if has_gif and not self.is_processing else 'disabled')

        # Отображение кнопки отмены
        if self.is_processing:
            self.cancel_btn.place(relx=0.5, rely=0.95, anchor='s')
        else:
            self.cancel_btn.place_forget()

    def update_progress(self, progress: float, message: str = ""):
        """Обновляет прогресс-бар."""
        if progress >= 0:
            canvas_width = self.progress_canvas.winfo_width()
            fill_width = int((progress / 100) * canvas_width)
            self.progress_canvas.coords(self.progress_fill, 0, 0, fill_width, 10)

        if message:
            self.status_var.set(f"> {message}")

    def update_info_display(self, text: str):
        """Обновляет информационное поле."""
        self.info_text.config(state='normal')
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(tk.END, text)
        self.info_text.config(state='disabled')

    # --- Video Processing ---
    def find_ffmpeg(self) -> Optional[Path]:
        """Ищет FFmpeg в системе с улучшенной логикой поиска."""
        possible_names = ['ffmpeg.exe', 'ffmpeg'] if sys.platform == 'win32' else ['ffmpeg']

        # Сначала проверяем PATH
        for name in possible_names:
            found_path = shutil.which(name)
            if found_path:
                path = Path(found_path)
                if self.test_ffmpeg(path):
                    return path

        # Проверяем локальные пути
        local_paths = [
            Path.cwd() / "ffmpeg.exe",
            Path.cwd() / "ffmpeg",
            Path.cwd() / "bin" / "ffmpeg.exe",
            Path.cwd() / "bin" / "ffmpeg"
        ]

        # Системные пути
        if sys.platform == 'win32':
            system_paths = [
                Path("C:/ffmpeg/bin/ffmpeg.exe"),
                Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
                Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe")
            ]
        else:
            system_paths = [
                Path("/usr/bin/ffmpeg"),
                Path("/usr/local/bin/ffmpeg"),
                Path("/opt/homebrew/bin/ffmpeg")
            ]

        all_paths = local_paths + system_paths

        for path in all_paths:
            if path.exists() and path.is_file() and self.test_ffmpeg(path):
                return path

        return None

    def test_ffmpeg(self, ffmpeg_path: Path) -> bool:
        """Тестирует работоспособность FFmpeg с улучшенной проверкой."""
        try:
            cmd = [str(ffmpeg_path), '-version']
            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                startupinfo=startupinfo
            )

            return (result.returncode == 0 and
                    'ffmpeg version' in result.stdout.lower())
        except Exception as e:
            print(f"Ошибка тестирования FFmpeg {ffmpeg_path}: {e}")
            return False

    def get_video_info(self, video_path: Path) -> Dict[str, Any]:
        """Получает информацию о видео с помощью FFprobe."""
        try:
            # Пытаемся найти ffprobe рядом с ffmpeg
            ffprobe_name = "ffprobe.exe" if sys.platform == 'win32' else "ffprobe"
            ffprobe_path = self.ffmpeg_path.parent / ffprobe_name
            
            use_ffprobe = ffprobe_path.exists()
            
            if use_ffprobe:
                cmd = [str(ffprobe_path), '-v', 'quiet', '-print_format', 'json', '-show_format', str(video_path)]
            else:
                # Если ffprobe не найден, используем ffmpeg для получения информации
                cmd = [str(self.ffmpeg_path), '-i', str(video_path), '-f', 'null', '-']

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
            
            if use_ffprobe and result.returncode == 0:
                info = json.loads(result.stdout)
                duration = float(info['format']['duration'])
                return {'duration': duration}
            else:
                 # Простой парсинг вывода FFmpeg для получения длительности
                duration_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})", result.stderr)
                if duration_match:
                    h, m, s, ms = map(int, duration_match.groups())
                    duration = h * 3600 + m * 60 + s + ms / 100
                    return {'duration': duration}
            
            return {'duration': 0}

        except Exception as e:
            print(f"Ошибка получения информации о видео: {e}")
            return {'duration': 0}

    def start_download(self):
        """Запускает загрузку видео."""
        url = self.url_var.get().strip()
        if not url or url == URL_PLACEHOLDER:
            messagebox.showwarning("Внимание", "Введите URL видео")
            return

        if self.active_thread:
            return

        self.is_processing = True
        self.update_ui_state()
        self.update_progress(0, "Начинаем загрузку...")

        self.active_thread = CancellableThread(target=self.download_video, args=(url,))
        self.active_thread.start()

    def download_video(self, url: str):
        """Загружает видео с помощью yt-dlp."""
        try:
            output_path = self.temp_dir / "downloaded_video.%(ext)s"

            ydl_opts = {
                'format': 'best[height<=720]/best',
                'outtmpl': str(output_path),
                'noplaylist': True,
                'extract_flat': False,
            }

            def progress_hook(d):
                if self.active_thread and self.active_thread.stopped():
                    raise Exception("Загрузка отменена")

                if d['status'] == 'downloading':
                    try:
                        percent = float(d.get('_percent_str', '0%').replace('%', ''))
                        self.root.after(0, self.update_progress, percent, f"Загрузка: {percent:.1f}%")
                    except:
                        self.root.after(0, self.update_progress, -1, "Загрузка...")
                elif d['status'] == 'finished':
                    self.root.after(0, self.update_progress, 100, "Загрузка завершена")

            ydl_opts['progress_hooks'] = [progress_hook]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Находим загруженный файл
            for file in self.temp_dir.glob("downloaded_video.*"):
                if file.is_file():
                    self.video_path = file
                    break

            if self.video_path:
                self.video_info = self.get_video_info(self.video_path)
                self.root.after(0, self.on_video_loaded)
            else:
                self.root.after(0, self.on_download_error, "Загруженный файл не найден")

        except Exception as e:
            if "отменена" not in str(e):
                self.root.after(0, self.on_download_error, str(e))
        finally:
            self.active_thread = None

    def on_video_loaded(self):
        """Обработчик успешной загрузки видео."""
        duration = self.video_info.get('duration', 0)
        self.end_var.set(str(min(5.0, duration)))

        info_text = f"Video uploaded successfully\nDuration: {duration:.1f} sec"
        self.update_info_display(info_text)
        self.update_progress(100, "The video is ready for processing")

        self.is_processing = False
        self.update_ui_state()

    def on_download_error(self, error_message: str):
        """Обработчик ошибки загрузки."""
        self.update_progress(0, "Download error")
        self.update_info_display(f"Error: {error_message}")
        self.is_processing = False
        self.update_ui_state()

    # --- GIF Creation ---
    def start_gif_creation(self):
        """Запускает создание GIF."""
        if not self.video_path or not self.ffmpeg_path:
            return

        try:
            start_time = float(self.start_var.get() or 0)
            end_time = float(self.end_var.get() or 0)

            if start_time >= end_time:
                messagebox.showwarning("Attention", "Start time must be less than end timeDuration must be greater than 0")
                return

            duration = end_time - start_time
            if duration <= 0:
                messagebox.showwarning("Attention", "The duration must be greater than 0")
                return

        except ValueError:
            messagebox.showwarning("Attention", "Enter correct time values")
            return

        self.is_processing = True
        self.update_ui_state()
        self.update_progress(0, "Creating a GIF...")

        self.active_thread = CancellableThread(target=self.create_gif)
        self.active_thread.start()

    def create_gif(self):
        """Создает GIF с улучшенной обработкой ошибок."""
        try:
            start_time = float(self.start_var.get())
            end_time = float(self.end_var.get())
            width = int(self.width_var.get())
            fps = int(self.fps_var.get())
            quality = self.quality_var.get()

            duration = end_time - start_time

            # Используем абсолютные пути
            output_path = self.temp_dir.resolve() / TEMP_GIF_FILENAME
            palette_path = self.temp_dir.resolve() / TEMP_PALETTE_FILENAME
            video_path = self.video_path.resolve()

            # Проверки существования файлов
            if not video_path.exists():
                self.root.after(0, self.on_gif_error, f"Video file not found: {video_path}")
                return

            if not self.ffmpeg_path.exists():
                self.root.after(0, self.on_gif_error, f"FFmpeg not found: {self.ffmpeg_path}")
                return

            # Очистка старых файлов с улучшенной обработкой ошибок
            for file in [output_path, palette_path]:
                if file.exists():
                    try:
                        file.unlink()
                        time.sleep(0.1)  # Небольшая задержка для освобождения файла
                    except PermissionError:
                        # Пытаемся переименовать файл, если не можем удалить
                        try:
                            backup_path = file.with_suffix(f'.backup_{int(time.time())}')
                            file.rename(backup_path)
                        except Exception as rename_error:
                            self.root.after(0, self.on_gif_error, f"Failed to clear the temporary file: {rename_error}")
                            return
                    except Exception as e:
                        print(f"Warning: failed to delete {file}: {e}")

            # Создаем директорию если не существует
            output_path.parent.mkdir(parents=True, exist_ok=True)
            palette_path.parent.mkdir(parents=True, exist_ok=True)

            # Настройки качества (упрощенные для избежания ошибок)
            quality_settings = {
                'fast': 'stats_mode=single',
                'medium': 'stats_mode=diff',  # Изменено с full на diff
                'high': 'stats_mode=diff:max_colors=256'  # Изменено с full на diff
            }

            palette_gen = quality_settings.get(quality, quality_settings['medium'])

            # Упрощенная команда создания палитры с проверкой параметров
            palette_cmd = [
                str(self.ffmpeg_path.resolve()),
                '-y',
                '-ss', f'{start_time:.3f}',
                '-t', f'{duration:.3f}',
                '-i', str(video_path),
                '-vf', f'scale={width}:-1:flags=lanczos,palettegen={palette_gen}',
                '-vframes', '1',  # <-- НОВОЕ ИЗМЕНЕНИЕ: Явно указываем, что нужен только один кадр
                '-loglevel', 'warning',
                str(palette_path)
            ]

            print(f"Palette command: {' '.join(palette_cmd)}")

            # Запуск создания палитры
            palette_manager = FFmpegProcessManager(
                palette_cmd,
                self.on_palette_progress,
                self.on_palette_complete,
                duration
            )

            self.active_ffmpeg_process = palette_manager
            palette_manager.run()

        except Exception as e:
            self.root.after(0, self.on_gif_error, f"Configuration error: {str(e)}")

    def on_palette_progress(self, progress: float, message: str):
            """Обработчик прогресса создания палитры."""
            if progress >= 0:
                self.root.after(0, self.update_progress, progress * 0.3, f"Creating a palette: {progress:.1f}%")
            else:
                self.root.after(0, self.update_progress, -1, f"Palette: {message[:60]}...")

    def on_palette_complete(self, return_code: int, error_message: str):
        """Обработчик завершения создания палитры с улучшенной обработкой."""
        if return_code != 0:
            error_msg = error_message or f"Palette creation error (code {return_code})"
            self.root.after(0, self.on_gif_error, error_msg)
            return

        palette_path = self.temp_dir.resolve() / TEMP_PALETTE_FILENAME
        if not palette_path.exists() or palette_path.stat().st_size == 0:
            self.root.after(0, self.on_gif_error, "Palette file not created or empty")
            return

        if self.active_thread and self.active_thread.stopped():
            return

        # Создание GIF с улучшенными параметрами
        try:
            start_time = float(self.start_var.get())
            end_time = float(self.end_var.get())
            width = int(self.width_var.get())
            fps = int(self.fps_var.get())
            quality = self.quality_var.get()

            duration = end_time - start_time
            output_path = self.temp_dir.resolve() / TEMP_GIF_FILENAME
            video_path = self.video_path.resolve()

            # Настройки dithering (упрощенные)
            dither_settings = {
                'fast': 'dither=none',
                'medium': 'dither=bayer:bayer_scale=2',
                'high': 'dither=floyd_steinberg'  # Изменено на более стабильный алгоритм
            }

            dither = dither_settings.get(quality, dither_settings['medium'])

            # Упрощенная и более стабильная команда создания GIF
            gif_cmd = [
                str(self.ffmpeg_path.resolve()),
                '-y',
                '-ss', f'{start_time:.3f}',
                '-t', f'{duration:.3f}',
                '-i', str(video_path),
                '-i', str(palette_path),
                '-filter_complex', f'[0:v]scale={width}:-1:flags=lanczos,fps={fps}[v];[v][1:v]paletteuse={dither}',
                '-loglevel', 'warning',
                '-f', 'gif',  # Явно указываем формат выхода
                str(output_path)
            ]

            print(f"GIF command: {' '.join(gif_cmd)}")

            gif_manager = FFmpegProcessManager(
                gif_cmd,
                self.on_gif_progress,
                self.on_gif_complete,
                duration
            )

            self.active_ffmpeg_process = gif_manager
            gif_manager.run()

        except Exception as e:
            self.root.after(0, self.on_gif_error, f"Ошибка создания GIF: {str(e)}")

    def on_gif_progress(self, progress: float, message: str):
        """Обработчик прогресса создания GIF."""
        if progress >= 0:
            self.root.after(0, self.update_progress, 30 + progress * 0.7, f"Создание GIF: {progress:.1f}%")
        else:
            self.root.after(0, self.update_progress, -1, f"GIF: {message[:60]}...")

    def on_gif_complete(self, return_code: int, error_message: str):
        """Обработчик завершения создания GIF."""
        if return_code != 0:
            self.root.after(0, self.on_gif_error, error_message or "Ошибка создания GIF")
            return

        gif_path = self.temp_dir / TEMP_GIF_FILENAME
        if gif_path.exists():
            self.gif_path = gif_path
            self.root.after(0, self.on_gif_created)
        else:
            self.root.after(0, self.on_gif_error, "GIF файл не найден")

    def on_gif_created(self):
        """Обработчик успешного создания GIF."""
        self.update_progress(100, "GIF создан успешно!")

        # Загружаем GIF для предварительного просмотра
        self.load_gif_preview()

        file_size = self.gif_path.stat().st_size / (1024 * 1024)  # MB
        info_text = f"GIF создан успешно!\nРазмер файла: {file_size:.2f} MB"
        self.update_info_display(info_text)

        self.is_processing = False
        self.active_ffmpeg_process = None
        self.active_thread = None
        self.update_ui_state()

    def on_gif_error(self, error_message: str):
        """Обработчик ошибки создания GIF."""
        self.update_progress(0, "Ошибка создания GIF")
        self.update_info_display(f"Ошибка: {error_message}")

        self.is_processing = False
        self.active_ffmpeg_process = None
        self.active_thread = None
        self.update_ui_state()

    # --- GIF Preview ---
    def load_gif_preview(self):
        """Загружает GIF для предварительного просмотра."""
        if not self.gif_path or not self.gif_path.exists():
            return

        try:
            with Image.open(self.gif_path) as gif:
                self.animation_frames = []
                self.animation_frame_delays = []

                # Получаем размеры предварительного просмотра
                preview_width = self.preview_label.winfo_width()
                preview_height = self.preview_label.winfo_height()

                if preview_width <= 1 or preview_height <= 1:
                    # Если размеры еще не установлены, попробуем позже
                    self.root.after(100, self.load_gif_preview)
                    return

                for frame in ImageSequence.Iterator(gif):
                    # Масштабируем кадр для предварительного просмотра
                    frame_copy = frame.copy()
                    frame_copy.thumbnail((preview_width - 20, preview_height - 20), Image.Resampling.LANCZOS)

                    # Конвертируем в PhotoImage
                    photo = ImageTk.PhotoImage(frame_copy)
                    self.animation_frames.append(photo)

                    # Получаем задержку кадра
                    delay = frame.info.get('duration', 50)  # По умолчанию 50ms
                    self.animation_frame_delays.append(delay)

            if self.animation_frames:
                self.current_frame_index = 0
                self.start_preview_animation()

        except Exception as e:
            print(f"Ошибка загрузки GIF для предварительного просмотра: {e}")

    def start_preview_animation(self):
        """Запускает анимацию предварительного просмотра."""
        if self.animation_frames:
            self.animate_preview()

    def animate_preview(self):
        """Анимирует предварительный просмотр GIF."""
        if not self.animation_frames:
            return

        # Отображаем текущий кадр
        current_frame = self.animation_frames[self.current_frame_index]
        self.preview_label.config(image=current_frame, text="")

        # Планируем следующий кадр
        delay = self.animation_frame_delays[self.current_frame_index]
        self.current_frame_index = (self.current_frame_index + 1) % len(self.animation_frames)

        self.preview_animation_id = self.root.after(delay, self.animate_preview)

    def stop_preview_animation(self):
        """Останавливает анимацию предварительного просмотра."""
        if self.preview_animation_id:
            self.root.after_cancel(self.preview_animation_id)
            self.preview_animation_id = None

    # --- File Operations ---
    def save_gif(self):
        """Сохраняет созданный GIF."""
        if not self.gif_path or not self.gif_path.exists():
            return

        file_path = filedialog.asksaveasfilename(
            title="Сохранить GIF",
            defaultextension=".gif",
            filetypes=[("GIF files", "*.gif"), ("All files", "*.*")]
        )

        if file_path:
            try:
                shutil.copy2(self.gif_path, file_path)
                messagebox.showinfo("Успех", f"GIF сохранен: {file_path}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить файл:\n{e}")

    # --- Operation Control ---
    def cancel_operation(self):
        """Отменяет текущую операцию."""
        if self.active_thread:
            self.active_thread.stop()

        if self.active_ffmpeg_process:
            self.active_ffmpeg_process.terminate()

        self.update_progress(0, "Операция отменена")
        self.is_processing = False
        self.update_ui_state()

    # --- Cleanup ---
    def cleanup_temp_files(self):
        """Очищает временные файлы."""
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            print(f"Ошибка очистки временных файлов: {e}")

    def on_closing(self):
        """Обработчик закрытия приложения."""
        self.cancel_operation()
        self.stop_preview_animation()
        self.cleanup_temp_files()
        self.root.destroy()

# --- Application Entry Point ---
def main():
    """Точка входа в приложение."""
    root = tk.Tk()
    app = GifStudioPro(root)
    root.mainloop()

if __name__ == "__main__":
    main()
