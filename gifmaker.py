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
    """Checks and installs required dependencies if they are missing."""
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
        msg = f"Required modules not found: {', '.join(missing)}.\n\nInstall them automatically using pip?"
        if messagebox.askyesno("Dependency Check", msg):
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', *missing])
                messagebox.showinfo("Success", "Dependencies installed. Please restart the application.")
                return False
            except subprocess.CalledProcessError as e:
                messagebox.showerror("Installation Error", f"Failed to install dependencies:\n{e}\n\nTry installing them manually: pip install {' '.join(missing)}")
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
        wmi = None # Define wmi as None if import failed

# --- Constants ---
URL_PLACEHOLDER = "Insert URL (YouTube, etc.)"
TEMP_GIF_FILENAME = "output.gif"
TEMP_PALETTE_FILENAME = "palette.png"

# --- Utility Classes ---
class CancellableThread(threading.Thread):
    """A thread that can be safely stopped."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        """Sets the stop flag."""
        self._stop_event.set()

    def stopped(self) -> bool:
        """Checks if the stop flag was set."""
        return self._stop_event.is_set()

class FFmpegProcessManager:
    """Manages launching, tracking progress and canceling FFmpeg process."""
    def __init__(self, command: List[str], progress_callback: Callable, completion_callback: Callable, total_duration: float = 0):
        self.command = command
        self.progress_callback = progress_callback
        self.completion_callback = completion_callback
        self.total_duration = total_duration
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def run(self):
        """Starts FFmpeg process in a separate thread."""
        self.thread = threading.Thread(target=self._run_process, daemon=True)
        self.thread.start()

    def _run_process(self):
        """Internal method with improved FFmpeg process handling."""
        try:
            # Fixed command preparation
            cmd_str = []
            for arg in self.command:
                if isinstance(arg, Path):
                    # Use absolute paths and proper escaping
                    path_str = str(arg.resolve().as_posix()) if sys.platform != 'win32' else str(arg.resolve())
                    cmd_str.append(path_str)
                else:
                    cmd_str.append(str(arg))

            print(f"Executing: {' '.join(cmd_str)}")  # Debug output

            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            # Improved process startup parameters
            self.process = subprocess.Popen(
                cmd_str,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Changed: separate thread for stderr
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                cwd=str(Path.cwd()),
                env=os.environ.copy()  # Added: environment copying
            )

            # Read stdout and stderr in parallel
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

            # Start reading in separate threads
            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)

            stdout_thread.start()
            stderr_thread.start()

            # Wait for process completion
            return_code = self.process.wait()

            # Wait for output reading completion
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            self.process.stdout.close()
            self.process.stderr.close()

            if self._stop_event.is_set():
                self.completion_callback(-2, "Process canceled by user")
            elif return_code != 0:
                # Fixed error code handling
                if return_code > 2147483647:  # Fix for large unsigned values
                    return_code = return_code - 4294967296

                all_logs = output_log + error_log
                error_lines = [line for line in all_logs if
                            any(keyword in line.lower() for keyword in
                                ['error', 'failed', 'not found', 'invalid', 'cannot', 'permission denied'])]

                if error_lines:
                    error_msg = "\n".join(error_lines[-5:])  # Last 5 errors
                else:
                    error_msg = "\n".join(all_logs[-15:])  # Last 15 lines

                self.completion_callback(return_code, f"FFmpeg error (code {return_code}):\n{error_msg}")
            else:
                self.completion_callback(0, None)

        except FileNotFoundError:
            self.completion_callback(-1, "FFmpeg not found. Check the executable path.")
        except Exception as e:
            self.completion_callback(-1, f"Critical error: {str(e)}")

    def _process_output_line(self, line: str, last_progress: float):
        """Processes FFmpeg output line to extract progress."""
        # Improved progress recognition
        time_match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
        if time_match and self.total_duration > 0:
            h, m, s, ms = map(int, time_match.groups())
            current_time = h * 3600 + m * 60 + s + ms / 100
            progress = min(100, (current_time / self.total_duration) * 100)

            # Update only if progress changed significantly
            if abs(progress - last_progress) > 0.5:
                self.progress_callback(progress, f"Processing: {progress:.1f}%")
                last_progress = progress
        elif "frame=" in line:
            # Alternative progress tracking method
            self.progress_callback(-1, "Processing frames...")

    def terminate(self):
        """Forcefully terminates FFmpeg process."""
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                # On Windows terminate() for ffmpeg may leave zombie processes
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
    """Helper for creating stylized custom widgets."""
    def __init__(self, colors: Dict[str, str], fonts: Dict[str, Tuple]):
        self.colors = colors
        self.fonts = fonts

    def create_rounded_rect(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
        """Draws a rectangle with rounded corners on canvas."""
        points = [
            x1 + radius, y1, x1 + radius, y1, x2 - radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y1 + radius, x2, y2 - radius, x2, y2 - radius, x2, y2, x2 - radius, y2, x2 - radius, y2,
            x1 + radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y2 - radius, x1, y1 + radius,
            x1, y1 + radius, x1, y1, x1 + radius, y1]
        return canvas.create_polygon(points, **kwargs, smooth=True)

    def create_custom_button(self, parent: tk.Widget, text: str, command: Callable, width: int, height: int) -> tk.Canvas:
        """Creates custom animated button."""
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
        """Creates custom input field."""
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
        """Loads and resizes logo from bam.png file."""
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
            print(f"Logo loading error: {e}")
            self.logo_image = None

    def setup_theme_and_style(self):
        """Sets up application appearance: colors, fonts, styles."""
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
        """Creates and arranges main interface elements."""
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
        """Populates left panel with control elements."""
        self._create_panel_header(self.left_panel, "Source")

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

        self.duration_var = tk.StringVar(value="Duration: 5.0 sec")
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
        """Populates right panel with control elements."""
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

        self.create_btn = self.widget_helper.create_custom_button(actions_frame, "CREATE GIF", self.start_gif_creation, 200, 50)
        self.create_btn.grid(row=0, column=0, sticky='e', padx=5)
        self.save_btn = self.widget_helper.create_custom_button(actions_frame, "SAVE", self.save_gif, 200, 50)
        self.save_btn.grid(row=0, column=1, sticky='w', padx=5)

        self.cancel_btn = self.widget_helper.create_custom_button(self.right_panel, "CANCEL", self.cancel_operation, 150, 40)

    def _create_setting_control(self, parent: tk.Widget, label_text: str, variable: tk.StringVar, values: List[str], default_value: str):
        """Creates dropdown list for settings."""
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
        """Shows window with suggestion to specify FFmpeg path."""
        self.ffmpeg_finder_frame = tk.Frame(self.root, bg=self.colors['bg'], highlightbackground=self.colors['accent'], highlightthickness=1)
        self.ffmpeg_finder_frame.place(relx=0.5, rely=0.5, anchor='center')

        tk.Label(self.ffmpeg_finder_frame, text="FFMPEG NOT FOUND", font=self.fonts['h1'], fg=self.colors['accent_alt'], bg=self.colors['bg']).pack(pady=10, padx=20)
        tk.Label(self.ffmpeg_finder_frame, text="FFmpeg is required for video conversion.\nPlease specify path to ffmpeg.exe.", font=self.fonts['body'], fg=self.colors['text_primary'], bg=self.colors['bg']).pack(pady=5, padx=20)

        link = tk.Label(self.ffmpeg_finder_frame, text="Download FFmpeg", font=self.fonts['body'], fg=self.colors['accent'], bg=self.colors['bg'], cursor="hand2")
        link.pack(pady=5)
        link.bind("<Button-1>", lambda e: webbrowser.open("https://ffmpeg.org/download.html", new=2))

        self.widget_helper.create_custom_button(self.ffmpeg_finder_frame, "SELECT PATH", self.select_ffmpeg_path, 180, 40).pack(pady=20)

    def select_ffmpeg_path(self):
        """Opens file selection dialog for ffmpeg.exe."""
        path = filedialog.askopenfilename(title="Select ffmpeg.exe", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
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
        """Pastes text from clipboard."""
        try:
            clipboard_text = self.root.clipboard_get()
            if clipboard_text:
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, clipboard_text)
                self.url_entry.config(fg=self.colors['text_primary'])
        except tk.TclError:
            pass

    def validate_time_input(self, value: str) -> bool:
        """Time input validation."""
        if not value:
            return True
        try:
            float(value)
            return True
        except ValueError:
            return False

    def update_duration(self, *args):
        """Updates duration display."""
        try:
            start = float(self.start_var.get() or 0)
            end = float(self.end_var.get() or 0)
            duration = max(0, end - start)
            self.duration_var.set(f"Duration: {duration:.1f} sec")
        except ValueError:
            self.duration_var.set("Duration: ?.? sec")

    def update_ui_state(self):
        """Updates interface element states."""
        has_video = self.video_path is not None
        has_gif = self.gif_path is not None
        has_ffmpeg = self.ffmpeg_path is not None

        # Button management
        self.load_btn.configure_state('normal' if has_ffmpeg and not self.is_processing else 'disabled')
        self.create_btn.configure_state('normal' if has_video and has_ffmpeg and not self.is_processing else 'disabled')
        self.save_btn.configure_state('normal' if has_gif and not self.is_processing else 'disabled')

        # Cancel button display
        if self.is_processing:
            self.cancel_btn.place(relx=0.5, rely=0.95, anchor='s')
        else:
            self.cancel_btn.place_forget()

    def update_progress(self, progress: float, message: str = ""):
        """Updates progress bar."""
        if progress >= 0:
            canvas_width = self.progress_canvas.winfo_width()
            fill_width = int((progress / 100) * canvas_width)
            self.progress_canvas.coords(self.progress_fill, 0, 0, fill_width, 10)

        if message:
            self.status_var.set(f"> {message}")

    def update_info_display(self, text: str):
        """Updates information field."""
        self.info_text.config(state='normal')
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(tk.END, text)
        self.info_text.config(state='disabled')

    # --- Video Processing ---
    def find_ffmpeg(self) -> Optional[Path]:
        """Searches for FFmpeg in system with improved search logic."""
        possible_names = ['ffmpeg.exe', 'ffmpeg'] if sys.platform == 'win32' else ['ffmpeg']

        # First check PATH
        for name in possible_names:
            found_path = shutil.which(name)
            if found_path:
                path = Path(found_path)
                if self.test_ffmpeg(path):
                    return path

        # Check local paths
        local_paths = [
            Path.cwd() / "ffmpeg.exe",
            Path.cwd() / "ffmpeg",
            Path.cwd() / "bin" / "ffmpeg.exe",
            Path.cwd() / "bin" / "ffmpeg"
        ]

        # System paths
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
        """Tests FFmpeg functionality with improved checking."""
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
            print(f"FFmpeg testing error {ffmpeg_path}: {e}")
            return False

    def get_video_info(self, video_path: Path) -> Dict[str, Any]:
        """Gets video information using FFprobe."""
        try:
            # Try to find ffprobe next to ffmpeg
            ffprobe_name = "ffprobe.exe" if sys.platform == 'win32' else "ffprobe"
            ffprobe_path = self.ffmpeg_path.parent / ffprobe_name
            
            use_ffprobe = ffprobe_path.exists()
            
            if use_ffprobe:
                cmd = [str(ffprobe_path), '-v', 'quiet', '-print_format', 'json', '-show_format', str(video_path)]
            else:
                # If ffprobe not found, use ffmpeg to get information
                cmd = [str(self.ffmpeg_path), '-i', str(video_path), '-f', 'null', '-']

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
            
            if use_ffprobe and result.returncode == 0:
                info = json.loads(result.stdout)
                duration = float(info['format']['duration'])
                return {'duration': duration}
            else:
                 # Simple parsing of FFmpeg output to get duration
                duration_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})", result.stderr)
                if duration_match:
                    h, m, s, ms = map(int, duration_match.groups())
                    duration = h * 3600 + m * 60 + s + ms / 100
                    return {'duration': duration}
            
            return {'duration': 0}

        except Exception as e:
            print(f"Video information error: {e}")
            return {'duration': 0}

    def start_download(self):
        """Starts video download."""
        url = self.url_var.get().strip()
        if not url or url == URL_PLACEHOLDER:
            messagebox.showwarning("Warning", "Enter video URL")
            return

        if self.active_thread:
            return

        self.is_processing = True
        self.update_ui_state()
        self.update_progress(0, "Starting download...")

        self.active_thread = CancellableThread(target=self.download_video, args=(url,))
        self.active_thread.start()

    def download_video(self, url: str):
        """Downloads video using yt-dlp."""
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
                    raise Exception("Download canceled")

                if d['status'] == 'downloading':
                    try:
                        percent = float(d.get('_percent_str', '0%').replace('%', ''))
                        self.root.after(0, self.update_progress, percent, f"Downloading: {percent:.1f}%")
                    except:
                        self.root.after(0, self.update_progress, -1, "Downloading...")
                elif d['status'] == 'finished':
                    self.root.after(0, self.update_progress, 100, "Download completed")

            ydl_opts['progress_hooks'] = [progress_hook]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Find downloaded file
            for file in self.temp_dir.glob("downloaded_video.*"):
                if file.is_file():
                    self.video_path = file
                    break

            if self.video_path:
                self.video_info = self.get_video_info(self.video_path)
                self.root.after(0, self.on_video_loaded)
            else:
                self.root.after(0, self.on_download_error, "Downloaded file not found")

        except Exception as e:
            if "canceled" not in str(e):
                self.root.after(0, self.on_download_error, str(e))
        finally:
            self.active_thread = None

    def on_video_loaded(self):
        """Handler for successful video loading."""
        duration = self.video_info.get('duration', 0)
        self.end_var.set(str(min(5.0, duration)))

        info_text = f"Video uploaded successfully\nDuration: {duration:.1f} sec"
        self.update_info_display(info_text)
        self.update_progress(100, "Video ready for processing")

        self.is_processing = False
        self.update_ui_state()

    def on_download_error(self, error_message: str):
        """Download error handler."""
        self.update_progress(0, "Download error")
        self.update_info_display(f"Error: {error_message}")
        self.is_processing = False
        self.update_ui_state()

    # --- GIF Creation ---
    def start_gif_creation(self):
        """Starts GIF creation."""
        if not self.video_path or not self.ffmpeg_path:
            return

        try:
            start_time = float(self.start_var.get() or 0)
            end_time = float(self.end_var.get() or 0)

            if start_time >= end_time:
                messagebox.showwarning("Warning", "Start time must be less than end time")
                return

            duration = end_time - start_time
            if duration <= 0:
                messagebox.showwarning("Warning", "Duration must be greater than 0")
                return

        except ValueError:
            messagebox.showwarning("Warning", "Enter correct time values")
            return

        self.is_processing = True
        self.update_ui_state()
        self.update_progress(0, "Creating GIF...")

        self.active_thread = CancellableThread(target=self.create_gif)
        self.active_thread.start()

    def create_gif(self):
        """Creates GIF with improved error handling."""
        try:
            start_time = float(self.start_var.get())
            end_time = float(self.end_var.get())
            width = int(self.width_var.get())
            fps = int(self.fps_var.get())
            quality = self.quality_var.get()

            duration = end_time - start_time

            # Use absolute paths
            output_path = self.temp_dir.resolve() / TEMP_GIF_FILENAME
            palette_path = self.temp_dir.resolve() / TEMP_PALETTE_FILENAME
            video_path = self.video_path.resolve()

            # File existence checks
            if not video_path.exists():
                self.root.after(0, self.on_gif_error, f"Video file not found: {video_path}")
                return

            if not self.ffmpeg_path.exists():
                self.root.after(0, self.on_gif_error, f"FFmpeg not found: {self.ffmpeg_path}")
                return

            # Clear old files with improved error handling
            for file in [output_path, palette_path]:
                if file.exists():
                    try:
                        file.unlink()
                        time.sleep(0.1)  # Small delay for file release
                    except PermissionError:
                        # Try to rename file if can't delete
                        try:
                            backup_path = file.with_suffix(f'.backup_{int(time.time())}')
                            file.rename(backup_path)
                        except Exception as rename_error:
                            self.root.after(0, self.on_gif_error, f"Failed to clear temporary file: {rename_error}")
                            return
                    except Exception as e:
                        print(f"Warning: failed to delete {file}: {e}")

            # Create directory if doesn't exist
            output_path.parent.mkdir(parents=True, exist_ok=True)
            palette_path.parent.mkdir(parents=True, exist_ok=True)

            # Quality settings (simplified to avoid errors)
            quality_settings = {
                'fast': 'stats_mode=single',
                'medium': 'stats_mode=diff',  # Changed from full to diff
                'high': 'stats_mode=diff:max_colors=256'  # Changed from full to diff
            }

            palette_gen = quality_settings.get(quality, quality_settings['medium'])

            # Simplified palette creation command with parameter checking
            palette_cmd = [
                str(self.ffmpeg_path.resolve()),
                '-y',
                '-ss', f'{start_time:.3f}',
                '-t', f'{duration:.3f}',
                '-i', str(video_path),
                '-vf', f'scale={width}:-1:flags=lanczos,palettegen={palette_gen}',
                '-vframes', '1',  # NEW CHANGE: Explicitly specify only one frame needed
                '-loglevel', 'warning',
                str(palette_path)
            ]

            print(f"Palette command: {' '.join(palette_cmd)}")

            # Start palette creation
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
            """Palette creation progress handler."""
            if progress >= 0:
                self.root.after(0, self.update_progress, progress * 0.3, f"Creating palette: {progress:.1f}%")
            else:
                self.root.after(0, self.update_progress, -1, f"Palette: {message[:60]}...")

    def on_palette_complete(self, return_code: int, error_message: str):
        """Palette creation completion handler with improved handling."""
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

        # GIF creation with improved parameters
        try:
            start_time = float(self.start_var.get())
            end_time = float(self.end_var.get())
            width = int(self.width_var.get())
            fps = int(self.fps_var.get())
            quality = self.quality_var.get()

            duration = end_time - start_time
            output_path = self.temp_dir.resolve() / TEMP_GIF_FILENAME
            video_path = self.video_path.resolve()

            # Dithering settings (simplified)
            dither_settings = {
                'fast': 'dither=none',
                'medium': 'dither=bayer:bayer_scale=2',
                'high': 'dither=floyd_steinberg'  # Changed to more stable algorithm
            }

            dither = dither_settings.get(quality, dither_settings['medium'])

            # Simplified and more stable GIF creation command
            gif_cmd = [
                str(self.ffmpeg_path.resolve()),
                '-y',
                '-ss', f'{start_time:.3f}',
                '-t', f'{duration:.3f}',
                '-i', str(video_path),
                '-i', str(palette_path),
                '-filter_complex', f'[0:v]scale={width}:-1:flags=lanczos,fps={fps}[v];[v][1:v]paletteuse={dither}',
                '-loglevel', 'warning',
                '-f', 'gif',  # Explicitly specify output format
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
            self.root.after(0, self.on_gif_error, f"GIF creation error: {str(e)}")

    def on_gif_progress(self, progress: float, message: str):
        """GIF creation progress handler."""
        if progress >= 0:
            self.root.after(0, self.update_progress, 30 + progress * 0.7, f"Creating GIF: {progress:.1f}%")
        else:
            self.root.after(0, self.update_progress, -1, f"GIF: {message[:60]}...")

    def on_gif_complete(self, return_code: int, error_message: str):
        """GIF creation completion handler."""
        if return_code != 0:
            self.root.after(0, self.on_gif_error, error_message or "GIF creation error")
            return

        gif_path = self.temp_dir / TEMP_GIF_FILENAME
        if gif_path.exists():
            self.gif_path = gif_path
            self.root.after(0, self.on_gif_created)
        else:
            self.root.after(0, self.on_gif_error, "GIF file not found")

    def on_gif_created(self):
        """Successful GIF creation handler."""
        self.update_progress(100, "GIF created successfully!")

        # Load GIF for preview
        self.load_gif_preview()

        file_size = self.gif_path.stat().st_size / (1024 * 1024)  # MB
        info_text = f"GIF created successfully!\nFile size: {file_size:.2f} MB"
        self.update_info_display(info_text)

        self.is_processing = False
        self.active_ffmpeg_process = None
        self.active_thread = None
        self.update_ui_state()

    def on_gif_error(self, error_message: str):
        """GIF creation error handler."""
        self.update_progress(0, "GIF creation error")
        self.update_info_display(f"Error: {error_message}")

        self.is_processing = False
        self.active_ffmpeg_process = None
        self.active_thread = None
        self.update_ui_state()

    # --- GIF Preview ---
    def load_gif_preview(self):
        """Loads GIF for preview."""
        if not self.gif_path or not self.gif_path.exists():
            return

        try:
            with Image.open(self.gif_path) as gif:
                self.animation_frames = []
                self.animation_frame_delays = []

                # Get preview dimensions
                preview_width = self.preview_label.winfo_width()
                preview_height = self.preview_label.winfo_height()

                if preview_width <= 1 or preview_height <= 1:
                    # If dimensions not set yet, try later
                    self.root.after(100, self.load_gif_preview)
                    return

                for frame in ImageSequence.Iterator(gif):
                    # Scale frame for preview
                    frame_copy = frame.copy()
                    frame_copy.thumbnail((preview_width - 20, preview_height - 20), Image.Resampling.LANCZOS)

                    # Convert to PhotoImage
                    photo = ImageTk.PhotoImage(frame_copy)
                    self.animation_frames.append(photo)

                    # Get frame delay
                    delay = frame.info.get('duration', 50)  # Default 50ms
                    self.animation_frame_delays.append(delay)

            if self.animation_frames:
                self.current_frame_index = 0
                self.start_preview_animation()

        except Exception as e:
            print(f"GIF preview loading error: {e}")

    def start_preview_animation(self):
        """Starts preview animation."""
        if self.animation_frames:
            self.animate_preview()

    def animate_preview(self):
        """Animates GIF preview."""
        if not self.animation_frames:
            return

        # Display current frame
        current_frame = self.animation_frames[self.current_frame_index]
        self.preview_label.config(image=current_frame, text="")

        # Schedule next frame
        delay = self.animation_frame_delays[self.current_frame_index]
        self.current_frame_index = (self.current_frame_index + 1) % len(self.animation_frames)

        self.preview_animation_id = self.root.after(delay, self.animate_preview)

    def stop_preview_animation(self):
        """Stops preview animation."""
        if self.preview_animation_id:
            self.root.after_cancel(self.preview_animation_id)
            self.preview_animation_id = None

    # --- File Operations ---
    def save_gif(self):
        """Saves created GIF."""
        if not self.gif_path or not self.gif_path.exists():
            return

        file_path = filedialog.asksaveasfilename(
            title="Save GIF",
            defaultextension=".gif",
            filetypes=[("GIF files", "*.gif"), ("All files", "*.*")]
        )

        if file_path:
            try:
                shutil.copy2(self.gif_path, file_path)
                messagebox.showinfo("Success", f"GIF saved: {file_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save file:\n{e}")

    # --- Operation Control ---
    def cancel_operation(self):
        """Cancels current operation."""
        if self.active_thread:
            self.active_thread.stop()

        if self.active_ffmpeg_process:
            self.active_ffmpeg_process.terminate()

        self.update_progress(0, "Operation canceled")
        self.is_processing = False
        self.update_ui_state()

    # --- Cleanup ---
    def cleanup_temp_files(self):
        """Cleans up temporary files."""
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            print(f"Temporary files cleanup error: {e}")

    def on_closing(self):
        """Application closing handler."""
        self.cancel_operation()
        self.stop_preview_animation()
        self.cleanup_temp_files()
        self.root.destroy()

# --- Application Entry Point ---
def main():
    """Application entry point."""
    root = tk.Tk()
    app = GifStudioPro(root)
    root.mainloop()

if __name__ == "__main__":
    main()
