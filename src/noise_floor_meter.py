#!/usr/bin/env python3
# =============================================================================
#  NOISE FLOOR METER — F8AOF
#  Windows Edition · Tkinter + sounddevice + Hamlib CAT
#  IC-706MkIIH · EMU 0202 · 24 bits / 48 kHz
# =============================================================================

import sys
import os
import threading
import time
import socket
import queue
import csv
import json
from datetime import datetime
from pathlib import Path
from collections import deque

import numpy as np
from scipy import signal as scipy_signal
from scipy.stats import scoreatpercentile
import sounddevice as sd
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.ticker as ticker

# =============================================================================
#  CONSTANTES
# =============================================================================
APP_NAME    = "Noise Floor Meter"
APP_VERSION = "1.0.0"
APP_AUTHOR  = "F8AOF"

SAMPLE_RATE  = 48000
BLOCK_SIZE   = 4096
FFT_SIZES    = [1024, 2048, 4096, 8192, 16384]
MAX_AVG      = 128
HIST_MAX_PTS = 3000
UPDATE_MS    = 150   # ms entre rafraîchissements GUI

# Bandes radioamateur HF/VHF
HAM_BANDS = {
    "160m": 1.840,  "80m":  3.700,  "60m":  5.357,
    "40m":  7.100,  "30m": 10.130,  "20m": 14.200,
    "17m": 18.100,  "15m": 21.200,  "12m": 24.940,
    "10m": 28.500,   "6m": 50.100,   "2m":144.200,
    "70cm":430.000,
}

# Modes Hamlib
HAM_MODES = ["USB", "LSB", "AM", "FM", "CW", "RTTY", "PKTUSB"]

# Palette couleurs
C = {
    "bg":      "#07090b",
    "surf":    "#0e1117",
    "surf2":   "#141820",
    "border":  "#1c2535",
    "cyan":    "#00d4ff",
    "amber":   "#ffb300",
    "green":   "#00e676",
    "red":     "#ff4444",
    "text":    "#c9d6e8",
    "dim":     "#4a5a72",
}

# Config sauvegardée
CONFIG_FILE = Path.home() / ".nfm_config.json"

# =============================================================================
#  ÉTAT GLOBAL
# =============================================================================
class State:
    def __init__(self):
        self.lock         = threading.Lock()
        self.running      = False
        self.fft_size     = 4096
        self.window_name  = "hann"
        self.n_avg        = 32
        self.percentile   = 10
        self.bits         = 24
        self.device_idx   = None

        self.audio_queue  = queue.Queue(maxsize=128)
        self.psd_frames   = deque(maxlen=MAX_AVG)
        self.psd_avg      = None
        self.freqs        = None

        self.hist_nf      = deque(maxlen=HIST_MAX_PTS)
        self.hist_rms     = deque(maxlen=HIST_MAX_PTS)

        self.nf_current   = -999.0
        self.nf_min       = 0.0
        self.nf_max       = -999.0
        self.rms_dbfs     = -999.0
        self.peak_dbfs    = -999.0
        self.frame_count  = 0
        self.clip_count   = 0
        self.start_time   = None

        self.cal_offset   = None
        self.cal_ref_dbm  = -73.0

        self._win         = None
        self._win_key     = None

        # CAT Hamlib
        self.cat_connected  = False
        self.cat_freq_hz    = None
        self.cat_mode       = None
        self.cat_host       = "127.0.0.1"
        self.cat_port       = 4532
        self.cat_rig        = "3021"  # Icom IC-706MkIIG Hamlib model
        self.cat_serial     = "COM1"
        self.cat_baud       = 9600
        self.rigctld_proc   = None
        self._cat_sock      = None

    def get_window(self):
        key = (self.window_name, self.fft_size)
        if self._win_key != key:
            N = self.fft_size
            name = self.window_name
            if name == "hann":
                w = np.hanning(N)
            elif name == "blackman":
                w = np.blackman(N)
            elif name == "flattop":
                w = scipy_signal.windows.flattop(N)
            else:
                w = np.ones(N)
            w = w / np.sqrt(np.mean(w**2))
            self._win = w.astype(np.float32)
            self._win_key = key
        return self._win

    @property
    def elapsed(self):
        return time.time() - self.start_time if self.start_time else 0.0

    @property
    def nf_dbm(self):
        if self.cal_offset is None or self.nf_current <= -999:
            return None
        return self.nf_current + self.cal_offset

    def reset(self):
        with self.lock:
            self.psd_frames.clear()
            self.psd_avg    = None
            self.hist_nf.clear()
            self.hist_rms.clear()
            self.nf_current = -999.0
            self.nf_min     = 0.0
            self.nf_max     = -999.0
            self.rms_dbfs   = -999.0
            self.peak_dbfs  = -999.0
            self.frame_count= 0
            self.clip_count = 0
            self.start_time = None

STATE = State()

# =============================================================================
#  DSP
# =============================================================================
def compute_psd(block, window, fft_size, sr):
    x = block[:fft_size] * window
    spec = np.fft.rfft(x, n=fft_size)
    pow_lin = (np.abs(spec)**2) / fft_size
    pow_lin[1:-1] *= 2
    bin_hz = sr / fft_size
    psd_lin = pow_lin / bin_hz
    return 10.0 * np.log10(np.maximum(psd_lin, 1e-30))

def audio_callback(indata, frames, time_info, status):
    if not STATE.running:
        return
    mono = indata[:, 0].copy().astype(np.float32)
    try:
        STATE.audio_queue.put_nowait(mono)
    except queue.Full:
        pass

def dsp_thread():
    block_buf = np.zeros(0, dtype=np.float32)
    while True:
        if not STATE.running:
            time.sleep(0.05)
            continue
        try:
            chunk = STATE.audio_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        block_buf = np.concatenate([block_buf, chunk])
        fft_size = STATE.fft_size
        if len(block_buf) < fft_size:
            continue

        block = block_buf[:fft_size]
        block_buf = block_buf[fft_size // 2:]

        window = STATE.get_window()
        psd_db = compute_psd(block, window, fft_size, SAMPLE_RATE)

        peak  = float(np.max(np.abs(block)))
        rms   = float(np.sqrt(np.mean(block**2)))
        rms_db  = 20.0 * np.log10(max(rms,  1e-15))
        peak_db = 20.0 * np.log10(max(peak, 1e-15))

        if peak >= 0.99:
            STATE.clip_count += 1

        psd_lin = 10.0 ** (psd_db / 10.0)
        STATE.psd_frames.append(psd_lin)
        n_use = min(STATE.n_avg, len(STATE.psd_frames))
        avg_lin = np.mean(list(STATE.psd_frames)[-n_use:], axis=0)
        psd_avg = 10.0 * np.log10(np.maximum(avg_lin, 1e-30))

        nf = float(scoreatpercentile(psd_avg, STATE.percentile))
        freqs = np.fft.rfftfreq(fft_size, 1.0 / SAMPLE_RATE)

        t = time.time()
        with STATE.lock:
            STATE.psd_avg    = psd_avg
            STATE.freqs      = freqs
            STATE.nf_current = nf
            STATE.rms_dbfs   = rms_db
            STATE.peak_dbfs  = peak_db
            STATE.frame_count += 1
            if STATE.start_time is None:
                STATE.start_time = t
            if STATE.frame_count == 1 or nf < STATE.nf_min:
                STATE.nf_min = nf
            if nf > STATE.nf_max:
                STATE.nf_max = nf
            STATE.hist_nf.append((t, nf))
            STATE.hist_rms.append((t, rms_db))

# =============================================================================
#  CAT HAMLIB via rigctld
# =============================================================================
class HamlibCAT:
    """Contrôle CAT via rigctld en mode socket TCP."""

    def __init__(self):
        self._sock   = None
        self._lock   = threading.Lock()
        self._thread = None
        self._stop   = False

    def connect(self, host="127.0.0.1", port=4532):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((host, port))
            self._sock = s
            STATE.cat_connected = True
            self._stop = False
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
            return True, "Connecté à rigctld"
        except Exception as e:
            STATE.cat_connected = False
            return False, str(e)

    def disconnect(self):
        self._stop = True
        STATE.cat_connected = False
        STATE.cat_freq_hz = None
        STATE.cat_mode = None
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    def _send(self, cmd):
        try:
            with self._lock:
                self._sock.sendall((cmd + "\n").encode())
                resp = b""
                while not resp.endswith(b"\n"):
                    chunk = self._sock.recv(1024)
                    if not chunk:
                        break
                    resp += chunk
                return resp.decode().strip()
        except Exception:
            return None

    def _poll_loop(self):
        """Lit fréquence et mode toutes les 500ms."""
        while not self._stop and self._sock:
            try:
                freq = self._send("f")
                if freq and freq.isdigit():
                    STATE.cat_freq_hz = int(freq)

                mode_resp = self._send("m")
                if mode_resp:
                    parts = mode_resp.split()
                    if parts:
                        STATE.cat_mode = parts[0]

                time.sleep(0.5)
            except Exception:
                STATE.cat_connected = False
                break

    def set_frequency(self, freq_hz):
        resp = self._send(f"F {int(freq_hz)}")
        return resp is not None

    def set_mode(self, mode, passband=0):
        resp = self._send(f"M {mode} {passband}")
        return resp is not None

    def get_level(self, level="STRENGTH"):
        resp = self._send(f"l {level}")
        try:
            return float(resp)
        except Exception:
            return None

CAT = HamlibCAT()

# =============================================================================
#  CONFIG
# =============================================================================
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        STATE.fft_size    = cfg.get("fft_size", 4096)
        STATE.n_avg       = cfg.get("n_avg", 32)
        STATE.window_name = cfg.get("window", "hann")
        STATE.percentile  = cfg.get("percentile", 10)
        STATE.bits        = cfg.get("bits", 24)
        STATE.cat_host    = cfg.get("cat_host", "127.0.0.1")
        STATE.cat_port    = cfg.get("cat_port", 4532)
        STATE.cat_serial  = cfg.get("cat_serial", "COM1")
        STATE.cat_baud    = cfg.get("cat_baud", 9600)
        STATE.cat_rig     = cfg.get("cat_rig", "3021")
        return cfg.get("device_name", None)
    except Exception:
        return None

def save_config(device_name=None):
    try:
        cfg = {
            "fft_size":    STATE.fft_size,
            "n_avg":       STATE.n_avg,
            "window":      STATE.window_name,
            "percentile":  STATE.percentile,
            "bits":        STATE.bits,
            "device_name": device_name,
            "cat_host":    STATE.cat_host,
            "cat_port":    STATE.cat_port,
            "cat_serial":  STATE.cat_serial,
            "cat_baud":    STATE.cat_baud,
            "cat_rig":     STATE.cat_rig,
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

# =============================================================================
#  EXPORT CSV
# =============================================================================
def export_csv(filepath):
    if not STATE.hist_nf:
        return False
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["# Noise Floor Meter — F8AOF"])
        w.writerow([f"# Date : {datetime.now().isoformat()}"])
        w.writerow([f"# FFT : {STATE.fft_size} pts  Fenetre : {STATE.window_name}"])
        w.writerow([f"# Calibration offset : "
                    f"{STATE.cal_offset:+.1f} dB" if STATE.cal_offset else "# Non calibre"])
        w.writerow([])
        w.writerow(["Timestamp_UNIX", "Timestamp_ISO",
                    "Plancher_dBFS_Hz", "Plancher_dBm_Hz"])
        for t, nf in STATE.hist_nf:
            dbm = f"{nf + STATE.cal_offset:.3f}" if STATE.cal_offset else ""
            w.writerow([f"{t:.3f}", datetime.fromtimestamp(t).isoformat(),
                        f"{nf:.3f}", dbm])
        if STATE.psd_avg is not None and STATE.freqs is not None:
            w.writerow([])
            w.writerow(["# Snapshot PSD"])
            w.writerow(["Freq_Hz", "PSD_dBFS_Hz"])
            for freq, val in zip(STATE.freqs, STATE.psd_avg):
                w.writerow([f"{freq:.2f}", f"{val:.3f}"])
    return True

# =============================================================================
#  INTERFACE GRAPHIQUE PRINCIPALE
# =============================================================================
class NFMApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(f"{APP_NAME} {APP_VERSION} — {APP_AUTHOR}")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(1100, 720)

        # Icône (si disponible)
        try:
            icon_path = Path(sys._MEIPASS) / "nfm.ico"
            self.iconbitmap(str(icon_path))
        except Exception:
            pass

        # Stream audio
        self.stream = None

        # Charger config
        saved_device_name = load_config()

        # Construire l'UI
        self._build_menu()
        self._build_ui()

        # Peupler les périphériques
        self._populate_devices(saved_device_name)

        # Lancer le thread DSP
        t = threading.Thread(target=dsp_thread, daemon=True)
        t.start()

        # Boucle de rafraîchissement
        self._schedule_update()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ──────────────────────────────────────────
    #  MENU
    # ──────────────────────────────────────────
    def _build_menu(self):
        mb = tk.Menu(self, bg=C["surf"], fg=C["text"],
                     activebackground=C["cyan"], activeforeground="#000")

        # Fichier
        fm = tk.Menu(mb, tearoff=0, bg=C["surf"], fg=C["text"])
        fm.add_command(label="Exporter CSV…", command=self._export_csv)
        fm.add_separator()
        fm.add_command(label="Quitter", command=self._on_close)
        mb.add_cascade(label="Fichier", menu=fm)

        # Mesure
        mm = tk.Menu(mb, tearoff=0, bg=C["surf"], fg=C["text"])
        mm.add_command(label="Démarrer", command=self._start)
        mm.add_command(label="Arrêter",  command=self._stop)
        mm.add_command(label="Reset",    command=self._reset)
        mb.add_cascade(label="Mesure", menu=mm)

        # CAT
        cm = tk.Menu(mb, tearoff=0, bg=C["surf"], fg=C["text"])
        cm.add_command(label="Configurer rigctld…", command=self._open_cat_dialog)
        cm.add_command(label="Connecter CAT",       command=self._cat_connect)
        cm.add_command(label="Déconnecter CAT",     command=self._cat_disconnect)
        mb.add_cascade(label="CAT Hamlib", menu=cm)

        # Aide
        hm = tk.Menu(mb, tearoff=0, bg=C["surf"], fg=C["text"])
        hm.add_command(label="À propos…", command=self._about)
        mb.add_cascade(label="Aide", menu=hm)

        self.config(menu=mb)

    # ──────────────────────────────────────────
    #  UI PRINCIPALE
    # ──────────────────────────────────────────
    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame",       background=C["bg"])
        style.configure("Dark.TFrame",  background=C["surf"])
        style.configure("TLabel",       background=C["bg"],    foreground=C["text"])
        style.configure("Dim.TLabel",   background=C["surf"],  foreground=C["dim"],
                        font=("Courier New", 8))
        style.configure("Val.TLabel",   background=C["surf"],  foreground=C["cyan"],
                        font=("Courier New", 11, "bold"))
        style.configure("TCombobox",    fieldbackground=C["surf2"], background=C["surf2"],
                        foreground=C["text"], selectbackground=C["cyan"])
        style.configure("TScale",       background=C["surf"],  troughcolor=C["surf2"])
        style.configure("TNotebook",    background=C["bg"])
        style.configure("TNotebook.Tab",background=C["surf"],  foreground=C["dim"],
                        padding=[10,4])
        style.map("TNotebook.Tab",
                  background=[("selected", C["surf2"])],
                  foreground=[("selected", C["cyan"])])
        style.configure("Run.TButton",  background=C["surf"],  foreground=C["cyan"],
                        font=("Courier New", 9, "bold"), padding=5)
        style.configure("Stop.TButton", background=C["surf"],  foreground=C["red"],
                        font=("Courier New", 9, "bold"), padding=5)
        style.configure("Neu.TButton",  background=C["surf"],  foreground=C["dim"],
                        font=("Courier New", 9), padding=5)
        style.configure("Cal.TButton",  background=C["surf"],  foreground=C["amber"],
                        font=("Courier New", 9, "bold"), padding=5)
        style.configure("CSV.TButton",  background=C["surf"],  foreground=C["green"],
                        font=("Courier New", 9, "bold"), padding=5)

        # ── Root layout ──
        top    = ttk.Frame(self, style="TFrame", padding=6)
        top.pack(fill="x")
        main   = ttk.Frame(self, style="TFrame", padding=(6,0,6,6))
        main.pack(fill="both", expand=True)
        bottom = ttk.Frame(self, style="Dark.TFrame", padding=4)
        bottom.pack(fill="x", side="bottom")

        # ── TOP BAR ──
        self._build_topbar(top)

        # ── MAIN : left panel + notebook ──
        left  = ttk.Frame(main, style="Dark.TFrame", padding=8, width=280)
        left.pack(side="left", fill="y", padx=(0,6))
        left.pack_propagate(False)

        right = ttk.Frame(main, style="TFrame")
        right.pack(side="left", fill="both", expand=True)

        self._build_left(left)
        self._build_right(right)
        self._build_bottom(bottom)

    def _build_topbar(self, parent):
        """Barre de titre + CAT display."""
        tk.Label(parent, text="NOISE FLOOR METER",
                 bg=C["bg"], fg=C["cyan"],
                 font=("Courier New", 14, "bold")).pack(side="left")
        tk.Label(parent, text=f"  {APP_VERSION} · {APP_AUTHOR}",
                 bg=C["bg"], fg=C["dim"],
                 font=("Courier New", 9)).pack(side="left")

        # CAT freq display (droite)
        cat_frame = tk.Frame(parent, bg=C["surf"], padx=10, pady=4)
        cat_frame.pack(side="right", padx=4)
        tk.Label(cat_frame, text="CAT", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left", padx=(0,6))
        self.lbl_cat_freq = tk.Label(cat_frame, text="--- MHz",
                                      bg=C["surf"], fg=C["amber"],
                                      font=("Courier New", 16, "bold"))
        self.lbl_cat_freq.pack(side="left")
        self.lbl_cat_mode = tk.Label(cat_frame, text="---",
                                      bg=C["surf"], fg=C["dim"],
                                      font=("Courier New", 10))
        self.lbl_cat_mode.pack(side="left", padx=(8,0))
        self.dot_cat = tk.Label(cat_frame, text="●", bg=C["surf"], fg=C["dim"],
                                 font=("Courier New", 10))
        self.dot_cat.pack(side="left", padx=(10,0))

    def _build_left(self, parent):
        """Panneau gauche : métriques + config."""
        def section(text):
            f = tk.Frame(parent, bg=C["border"], height=1)
            f.pack(fill="x", pady=(8,2))
            tk.Label(parent, text=text, bg=C["surf"], fg=C["dim"],
                     font=("Courier New", 8)).pack(anchor="w")

        # ── Métriques ──
        section("PLANCHER DE BRUIT")
        self._metrics = {}
        metrics_def = [
            ("PLANCHER P10",  "nf",   C["cyan"]),
            ("PLANCHER MIN",  "min",  C["green"]),
            ("PLANCHER MAX",  "max",  C["amber"]),
            ("NIVEAU RMS",    "rms",  C["text"]),
            ("CRÊTE",         "peak", C["text"]),
            ("dBm/Hz (cal.)", "dbm",  C["amber"]),
        ]
        for label, key, color in metrics_def:
            row = tk.Frame(parent, bg=C["surf"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=C["surf"], fg=C["dim"],
                     font=("Courier New", 8), width=14, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="---", bg=C["surf"], fg=color,
                           font=("Courier New", 10, "bold"), anchor="e")
            lbl.pack(side="right")
            self._metrics[key] = lbl

        # Stats
        section("STATISTIQUES")
        stats_def = [
            ("Écrêtages",  "clips", C["red"]),
            ("Trames FFT", "frames", C["dim"]),
            ("Durée",      "dur",   C["dim"]),
        ]
        for label, key, color in stats_def:
            row = tk.Frame(parent, bg=C["surf"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=C["surf"], fg=C["dim"],
                     font=("Courier New", 8), width=14, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="---", bg=C["surf"], fg=color,
                           font=("Courier New", 10, "bold"), anchor="e")
            lbl.pack(side="right")
            self._metrics[key] = lbl

        # ── Calibration ──
        section("CALIBRATION dBFS → dBm")
        cal_frame = tk.Frame(parent, bg=C["surf"])
        cal_frame.pack(fill="x", pady=2)
        tk.Label(cal_frame, text="Réf. (dBm)", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).grid(row=0, column=0, sticky="w")
        self.cal_dbm_var = tk.DoubleVar(value=-73.0)
        tk.Spinbox(cal_frame, from_=-140, to=0, increment=1,
                   textvariable=self.cal_dbm_var, width=7,
                   bg=C["surf2"], fg=C["amber"], insertbackground=C["amber"],
                   font=("Courier New", 9),
                   command=self._update_cal).grid(row=0, column=1, padx=4)
        self.lbl_cal_offset = tk.Label(parent, text="Non calibré",
                                        bg=C["surf"], fg=C["dim"],
                                        font=("Courier New", 8))
        self.lbl_cal_offset.pack(anchor="w", pady=2)

        # ── Bandes HAM ──
        section("BANDES RADIOAMATEUR")
        bands_frame = tk.Frame(parent, bg=C["surf"])
        bands_frame.pack(fill="x")
        self.band_buttons = {}
        cols = 4
        for i, (band, freq) in enumerate(HAM_BANDS.items()):
            btn = tk.Button(bands_frame, text=band,
                            bg=C["surf2"], fg=C["dim"],
                            font=("Courier New", 8), relief="flat",
                            activebackground=C["cyan"], activeforeground="#000",
                            command=lambda f=freq, b=band: self._set_band(f, b))
            btn.grid(row=i//cols, column=i%cols, padx=1, pady=1, sticky="ew")
            self.band_buttons[band] = btn
        for c in range(cols):
            bands_frame.columnconfigure(c, weight=1)

        # ── Config audio ──
        section("PÉRIPHÉRIQUE AUDIO")
        self.combo_device = ttk.Combobox(parent, state="readonly",
                                          font=("Courier New", 8))
        self.combo_device.pack(fill="x", pady=2)

        row = tk.Frame(parent, bg=C["surf"])
        row.pack(fill="x")
        tk.Label(row, text="Résolution", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left")
        self.bits_var = tk.StringVar(value="24 bits")
        ttk.Combobox(row, textvariable=self.bits_var, state="readonly",
                     values=["16 bits", "24 bits"], width=8,
                     font=("Courier New", 8)).pack(side="right")

        # ── Config FFT ──
        section("PARAMÈTRES FFT")
        row2 = tk.Frame(parent, bg=C["surf"])
        row2.pack(fill="x", pady=2)
        tk.Label(row2, text="Taille FFT", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left")
        self.fft_var = tk.StringVar(value="4096")
        ttk.Combobox(row2, textvariable=self.fft_var, state="readonly",
                     values=[str(s) for s in FFT_SIZES], width=7,
                     font=("Courier New", 8)).pack(side="right")

        row3 = tk.Frame(parent, bg=C["surf"])
        row3.pack(fill="x", pady=2)
        tk.Label(row3, text="Fenêtrage", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left")
        self.win_var = tk.StringVar(value="hann")
        ttk.Combobox(row3, textvariable=self.win_var, state="readonly",
                     values=["hann","blackman","flattop","rect"], width=9,
                     font=("Courier New", 8)).pack(side="right")

        row4 = tk.Frame(parent, bg=C["surf"])
        row4.pack(fill="x", pady=2)
        tk.Label(row4, text=f"Moyennage", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left")
        self.avg_var = tk.IntVar(value=32)
        self.lbl_avg = tk.Label(row4, text="32", bg=C["surf"], fg=C["cyan"],
                                 font=("Courier New", 8))
        self.lbl_avg.pack(side="right")
        ttk.Scale(row4, from_=4, to=MAX_AVG, orient="horizontal",
                  variable=self.avg_var,
                  command=lambda v: self.lbl_avg.config(
                      text=str(int(float(v))))).pack(side="right", fill="x", expand=True)

        row5 = tk.Frame(parent, bg=C["surf"])
        row5.pack(fill="x", pady=2)
        tk.Label(row5, text="Percentile NF", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left")
        self.pct_var = tk.IntVar(value=10)
        self.lbl_pct = tk.Label(row5, text="P10", bg=C["surf"], fg=C["cyan"],
                                 font=("Courier New", 8))
        self.lbl_pct.pack(side="right")
        ttk.Scale(row5, from_=1, to=30, orient="horizontal",
                  variable=self.pct_var,
                  command=lambda v: self.lbl_pct.config(
                      text=f"P{int(float(v))}")
                  ).pack(side="right", fill="x", expand=True)

    def _build_right(self, parent):
        """Notebook droite : PSD + historique."""
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        # Tab PSD
        tab_psd = ttk.Frame(nb, style="TFrame")
        nb.add(tab_psd, text="  PSD — Spectre  ")

        # Tab historique
        tab_hist = ttk.Frame(nb, style="TFrame")
        nb.add(tab_hist, text="  Historique plancher  ")

        # Tab CAT
        tab_cat = ttk.Frame(nb, style="TFrame")
        nb.add(tab_cat, text="  Contrôle CAT  ")

        self._build_psd_tab(tab_psd)
        self._build_hist_tab(tab_hist)
        self._build_cat_tab(tab_cat)

    def _build_psd_tab(self, parent):
        self.fig_psd = Figure(figsize=(8, 4), facecolor=C["surf"])
        self.ax_psd  = self.fig_psd.add_subplot(111, facecolor=C["bg"])
        self.ax_psd.set_title("Densité Spectrale de Puissance (dBFS/Hz)",
                               color=C["text"], fontsize=9, pad=6)
        self.ax_psd.set_xlabel("Fréquence (Hz)", color=C["dim"], fontsize=8)
        self.ax_psd.set_ylabel("dBFS/Hz", color=C["dim"], fontsize=8)
        self.ax_psd.tick_params(colors=C["dim"], labelsize=7)
        self.ax_psd.set_xlim(0, SAMPLE_RATE/2)
        self.ax_psd.set_ylim(-160, -40)
        self.ax_psd.grid(True, color=C["border"], linewidth=0.5)
        self.ax_psd.xaxis.set_major_formatter(
            ticker.FuncFormatter(
                lambda x, _: f"{x/1000:.1f}k" if x >= 1000 else f"{int(x)}"))
        for sp in self.ax_psd.spines.values():
            sp.set_edgecolor(C["border"])
        self.fig_psd.tight_layout(pad=1.5)

        freqs = np.fft.rfftfreq(DEFAULT_FFT := 4096, 1/SAMPLE_RATE)
        self.line_psd, = self.ax_psd.plot(freqs, np.full_like(freqs, -160),
                                           color=C["cyan"], linewidth=0.9)
        self.fill_psd  = self.ax_psd.fill_between(freqs, -160,
                                                    np.full_like(freqs, -160),
                                                    color=C["cyan"], alpha=0.07)
        self.line_nf_h = self.ax_psd.axhline(-130, color=C["green"],
                                               linewidth=0.8, linestyle="--")
        self.txt_nf_psd = self.ax_psd.text(200, -128, "", color=C["green"],
                                             fontsize=7, fontfamily="monospace")

        canvas = FigureCanvasTkAgg(self.fig_psd, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas_psd = canvas

        # Tooltip PSD
        canvas.mpl_connect("motion_notify_event", self._on_psd_hover)
        self.lbl_psd_tip = tk.Label(parent, text="", bg=C["surf"], fg=C["cyan"],
                                     font=("Courier New", 8))
        self.lbl_psd_tip.pack(anchor="w", padx=4)

    def _build_hist_tab(self, parent):
        self.fig_hist = Figure(figsize=(8, 3.5), facecolor=C["surf"])
        self.ax_hist  = self.fig_hist.add_subplot(111, facecolor=C["bg"])
        self.ax_hist.set_title("Historique plancher de bruit", color=C["text"],
                                fontsize=9, pad=6)
        self.ax_hist.set_xlabel("Temps (s)", color=C["dim"], fontsize=8)
        self.ax_hist.set_ylabel("dBFS/Hz",   color=C["dim"], fontsize=8)
        self.ax_hist.tick_params(colors=C["dim"], labelsize=7)
        self.ax_hist.set_ylim(-160, -40)
        self.ax_hist.grid(True, color=C["border"], linewidth=0.4)
        for sp in self.ax_hist.spines.values():
            sp.set_edgecolor(C["border"])
        self.fig_hist.tight_layout(pad=1.5)

        self.line_hist, = self.ax_hist.plot([], [], color=C["cyan"], linewidth=0.9)
        self.line_hmin  = self.ax_hist.axhline(-130, color=C["green"],
                                                linewidth=0.7, linestyle=":")

        canvas = FigureCanvasTkAgg(self.fig_hist, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas_hist = canvas

    def _build_cat_tab(self, parent):
        """Onglet contrôle CAT Hamlib."""
        main = tk.Frame(parent, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Status ──
        status_frame = tk.Frame(main, bg=C["surf"], padx=10, pady=8)
        status_frame.pack(fill="x", pady=(0,8))
        tk.Label(status_frame, text="ÉTAT CAT", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(anchor="w")
        self.lbl_cat_status = tk.Label(status_frame, text="● Déconnecté",
                                        bg=C["surf"], fg=C["dim"],
                                        font=("Courier New", 11, "bold"))
        self.lbl_cat_status.pack(anchor="w")
        self.lbl_cat_detail = tk.Label(status_frame, text="",
                                        bg=C["surf"], fg=C["dim"],
                                        font=("Courier New", 8))
        self.lbl_cat_detail.pack(anchor="w")

        # ── Config rigctld ──
        cfg_frame = tk.LabelFrame(main, text=" Configuration rigctld ",
                                   bg=C["surf"], fg=C["cyan"],
                                   font=("Courier New", 9),
                                   padx=10, pady=8)
        cfg_frame.pack(fill="x", pady=(0,8))

        fields = [
            ("Rig Hamlib ID",  "cat_rig_var",  STATE.cat_rig,   8),
            ("Port série",     "cat_ser_var",  STATE.cat_serial, 8),
            ("Vitesse (baud)", "cat_baud_var", str(STATE.cat_baud), 8),
            ("Host rigctld",   "cat_host_var", STATE.cat_host,  12),
            ("Port TCP",       "cat_port_var", str(STATE.cat_port), 6),
        ]
        for i, (label, varname, default, width) in enumerate(fields):
            tk.Label(cfg_frame, text=label, bg=C["surf"], fg=C["dim"],
                     font=("Courier New", 8), width=16, anchor="w").grid(
                         row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=default)
            setattr(self, varname, var)
            tk.Entry(cfg_frame, textvariable=var, width=width,
                     bg=C["surf2"], fg=C["cyan"], insertbackground=C["cyan"],
                     font=("Courier New", 9), relief="flat").grid(
                         row=i, column=1, sticky="w", padx=8)

        # Bouton lancer rigctld
        tk.Label(cfg_frame,
                 text="Modèle Icom IC-706MkIIG = 3021  |  IC-7300 = 373",
                 bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 7)).grid(row=len(fields), column=0,
                                                columnspan=2, sticky="w", pady=(4,0))

        btn_row = tk.Frame(main, bg=C["bg"])
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="▶ Connecter CAT",
                   style="Run.TButton",
                   command=self._cat_connect).pack(side="left", padx=4)
        ttk.Button(btn_row, text="■ Déconnecter",
                   style="Stop.TButton",
                   command=self._cat_disconnect).pack(side="left", padx=4)

        # ── Contrôle fréquence ──
        ctrl_frame = tk.LabelFrame(main, text=" Contrôle fréquence / mode ",
                                    bg=C["surf"], fg=C["amber"],
                                    font=("Courier New", 9),
                                    padx=10, pady=8)
        ctrl_frame.pack(fill="x", pady=(0,8))

        row_f = tk.Frame(ctrl_frame, bg=C["surf"])
        row_f.pack(fill="x", pady=2)
        tk.Label(row_f, text="Fréquence (MHz)", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8), width=16, anchor="w").pack(side="left")
        self.entry_freq = tk.Entry(row_f, width=12, bg=C["surf2"], fg=C["amber"],
                                    insertbackground=C["amber"],
                                    font=("Courier New", 11, "bold"), relief="flat")
        self.entry_freq.insert(0, "14.200")
        self.entry_freq.pack(side="left", padx=8)
        ttk.Button(row_f, text="Envoyer →",
                   style="Cal.TButton",
                   command=self._cat_set_freq).pack(side="left")

        row_m = tk.Frame(ctrl_frame, bg=C["surf"])
        row_m.pack(fill="x", pady=2)
        tk.Label(row_m, text="Mode", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8), width=16, anchor="w").pack(side="left")
        self.mode_var = tk.StringVar(value="USB")
        ttk.Combobox(row_m, textvariable=self.mode_var, state="readonly",
                     values=HAM_MODES, width=8,
                     font=("Courier New", 9)).pack(side="left", padx=8)
        ttk.Button(row_m, text="Envoyer →",
                   style="Cal.TButton",
                   command=self._cat_set_mode).pack(side="left")

        # Boutons bandes rapides
        bb_frame = tk.Frame(ctrl_frame, bg=C["surf"])
        bb_frame.pack(fill="x", pady=(6,0))
        tk.Label(bb_frame, text="Bande rapide :", bg=C["surf"], fg=C["dim"],
                 font=("Courier New", 8)).pack(side="left", padx=(0,6))
        for band, freq in list(HAM_BANDS.items())[:9]:
            tk.Button(bb_frame, text=band, bg=C["surf2"], fg=C["dim"],
                      font=("Courier New", 7), relief="flat",
                      activebackground=C["amber"], activeforeground="#000",
                      command=lambda f=freq: self._cat_goto_freq(f)
                      ).pack(side="left", padx=1)

    def _build_bottom(self, parent):
        """Barre de boutons + status."""
        # Boutons action
        btn_frame = tk.Frame(parent, bg=C["surf"])
        btn_frame.pack(side="left", padx=4)

        ttk.Button(btn_frame, text="▶ DÉMARRER", style="Run.TButton",
                   command=self._start).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="■ ARRÊTER",  style="Stop.TButton",
                   command=self._stop).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="↺ RESET",    style="Neu.TButton",
                   command=self._reset).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="⚖ CALIBRER", style="Cal.TButton",
                   command=self._calibrate).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="⬇ CSV",      style="CSV.TButton",
                   command=self._export_csv).pack(side="left", padx=3)

        # Status
        self.lbl_status = tk.Label(parent,
                                    text="● ARRÊT — Sélectionner un périphérique et démarrer",
                                    bg=C["surf"], fg=C["dim"],
                                    font=("Courier New", 8))
        self.lbl_status.pack(side="left", padx=12)

        # Info droite
        self.lbl_info = tk.Label(parent, text=f"48000 Hz · 24 bits · FFT 4096",
                                  bg=C["surf"], fg=C["dim"],
                                  font=("Courier New", 8))
        self.lbl_info.pack(side="right", padx=8)

    # ──────────────────────────────────────────
    #  PÉRIPHÉRIQUES AUDIO
    # ──────────────────────────────────────────
    def _populate_devices(self, preferred_name=None):
        try:
            devs = sd.query_devices()
            inputs = [(i, d) for i, d in enumerate(devs)
                      if d["max_input_channels"] > 0]
            names = [f"[{i}] {d['name'][:50]} ({d['max_input_channels']}ch)"
                     for i, d in inputs]
            self._device_indices = [i for i, _ in inputs]
            self.combo_device["values"] = names

            # Sélection auto
            sel = 0
            for j, (i, d) in enumerate(inputs):
                n = d["name"].lower()
                if preferred_name and preferred_name.lower() in n:
                    sel = j; break
                if any(k in n for k in ["emu", "0202", "usb audio", "line"]):
                    sel = j
            if names:
                self.combo_device.current(sel)
                STATE.device_idx = self._device_indices[sel]
        except Exception as e:
            self.lbl_status.config(text=f"ERREUR devices : {e}", fg=C["red"])

    def _get_selected_device(self):
        idx = self.combo_device.current()
        if idx >= 0 and idx < len(self._device_indices):
            return self._device_indices[idx]
        return None

    # ──────────────────────────────────────────
    #  ACTIONS
    # ──────────────────────────────────────────
    def _start(self):
        if STATE.running:
            return
        dev = self._get_selected_device()
        if dev is None:
            messagebox.showerror("Erreur", "Aucun périphérique audio sélectionné.")
            return

        # Sync params
        STATE.device_idx  = dev
        STATE.fft_size    = int(self.fft_var.get())
        STATE.window_name = self.win_var.get()
        STATE.n_avg       = int(self.avg_var.get())
        STATE.percentile  = int(self.pct_var.get())
        STATE.bits        = int(self.bits_var.get().split()[0])

        try:
            self.stream = sd.InputStream(
                device=dev,
                channels=1,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=audio_callback,
                latency="low",
            )
            self.stream.start()
            STATE.running = True
            self._set_status(f"● MESURE EN COURS — {SAMPLE_RATE} Hz · "
                              f"{STATE.bits} bits · FFT {STATE.fft_size} pts",
                              C["cyan"])
            self.lbl_info.config(text=f"{SAMPLE_RATE} Hz · {STATE.bits} bits · "
                                       f"FFT {STATE.fft_size} pts")
            save_config(sd.query_devices(dev)["name"])
        except Exception as e:
            self._set_status(f"ERREUR audio : {e}", C["red"])

    def _stop(self):
        STATE.running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self._set_status("● ARRÊT", C["dim"])

    def _reset(self):
        self._stop()
        STATE.reset()
        self._set_status("● RESET — données effacées", C["dim"])

    def _calibrate(self):
        if STATE.nf_current <= -999:
            messagebox.showwarning("Calibration",
                                   "Aucune mesure en cours.\nDémarrez la mesure d'abord.")
            return
        STATE.cal_ref_dbm = self.cal_dbm_var.get()
        STATE.cal_offset  = STATE.cal_ref_dbm - STATE.nf_current
        self.lbl_cal_offset.config(
            text=f"Offset : {STATE.cal_offset:+.1f} dB  "
                 f"(réf. {STATE.cal_ref_dbm:.1f} dBm = {STATE.nf_current:.1f} dBFS/Hz)",
            fg=C["amber"])
        self._set_status(
            f"✓ Calibration OK — offset {STATE.cal_offset:+.1f} dB", C["amber"])

    def _update_cal(self):
        if STATE.cal_offset is not None:
            self._calibrate()

    def _export_csv(self):
        if not STATE.hist_nf:
            messagebox.showinfo("Export", "Aucune donnée à exporter.")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tous", "*.*")],
            initialfile=f"noise_floor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        if filepath:
            if export_csv(filepath):
                self._set_status(f"✓ Export CSV : {filepath}", C["green"])
            else:
                self._set_status("ERREUR export CSV", C["red"])

    def _set_status(self, text, color=None):
        self.lbl_status.config(text=text, fg=color or C["dim"])

    # ──────────────────────────────────────────
    #  CAT HAMLIB
    # ──────────────────────────────────────────
    def _open_cat_dialog(self):
        # Déjà dans l'onglet CAT — basculer l'onglet
        pass

    def _cat_connect(self):
        STATE.cat_host = self.cat_host_var.get()
        STATE.cat_port = int(self.cat_port_var.get())
        ok, msg = CAT.connect(STATE.cat_host, STATE.cat_port)
        if ok:
            self.lbl_cat_status.config(text="● Connecté", fg=C["green"])
            self.lbl_cat_detail.config(
                text=f"rigctld {STATE.cat_host}:{STATE.cat_port}")
            self.dot_cat.config(fg=C["green"])
            self._set_status(f"✓ CAT connecté — {msg}", C["green"])
        else:
            self.lbl_cat_status.config(text="● Erreur connexion", fg=C["red"])
            self.lbl_cat_detail.config(text=msg)
            self._set_status(f"CAT ERREUR : {msg}", C["red"])

    def _cat_disconnect(self):
        CAT.disconnect()
        self.lbl_cat_status.config(text="● Déconnecté", fg=C["dim"])
        self.lbl_cat_detail.config(text="")
        self.lbl_cat_freq.config(text="--- MHz")
        self.lbl_cat_mode.config(text="---")
        self.dot_cat.config(fg=C["dim"])
        self._set_status("CAT déconnecté", C["dim"])

    def _cat_set_freq(self):
        if not STATE.cat_connected:
            messagebox.showwarning("CAT", "Non connecté à rigctld.")
            return
        try:
            mhz = float(self.entry_freq.get())
            hz  = int(mhz * 1e6)
            if CAT.set_frequency(hz):
                self._set_status(f"→ CAT fréquence envoyée : {mhz:.3f} MHz", C["amber"])
            else:
                self._set_status("Erreur envoi fréquence CAT", C["red"])
        except ValueError:
            messagebox.showerror("Erreur", "Fréquence invalide (ex: 14.200)")

    def _cat_set_mode(self):
        if not STATE.cat_connected:
            return
        mode = self.mode_var.get()
        if CAT.set_mode(mode):
            self._set_status(f"→ CAT mode envoyé : {mode}", C["amber"])

    def _cat_goto_freq(self, freq_mhz):
        """Envoie une fréquence en MHz au transceiver ET met à jour le champ."""
        self.entry_freq.delete(0, "end")
        self.entry_freq.insert(0, f"{freq_mhz:.3f}")
        if STATE.cat_connected:
            CAT.set_frequency(int(freq_mhz * 1e6))
            self._set_status(f"→ CAT : {freq_mhz:.3f} MHz", C["amber"])

    def _set_band(self, freq_mhz, band):
        """Met en surbrillance le bouton de bande et envoie la fréquence CAT."""
        for b, btn in self.band_buttons.items():
            btn.config(fg=C["dim"] if b != band else C["cyan"],
                       bg=C["surf2"] if b != band else C["border"])
        if STATE.cat_connected:
            self._cat_goto_freq(freq_mhz)

    # ──────────────────────────────────────────
    #  BOUCLE AFFICHAGE
    # ──────────────────────────────────────────
    def _schedule_update(self):
        self._update_display()
        self.after(UPDATE_MS, self._schedule_update)

    def _update_display(self):
        with STATE.lock:
            psd   = STATE.psd_avg.copy() if STATE.psd_avg is not None else None
            freqs = STATE.freqs.copy()   if STATE.freqs   is not None else None
            nf    = STATE.nf_current
            nfmin = STATE.nf_min
            nfmax = STATE.nf_max
            rms   = STATE.rms_dbfs
            peak  = STATE.peak_dbfs
            clips = STATE.clip_count
            frames= STATE.frame_count
            elapsed = STATE.elapsed
            hist  = list(STATE.hist_nf)
            cal   = STATE.cal_offset
            cat_f = STATE.cat_freq_hz
            cat_m = STATE.cat_mode
            cat_c = STATE.cat_connected

        def fmt(v, unit=""):
            return f"{v:.1f}{unit}" if v > -999 else "---"

        # Métriques
        self._metrics["nf"].config(text=fmt(nf, " dBFS/Hz"))
        self._metrics["min"].config(text=fmt(nfmin, " dBFS/Hz"))
        self._metrics["max"].config(text=fmt(nfmax, " dBFS/Hz"))
        self._metrics["rms"].config(text=fmt(rms, " dBFS"))
        self._metrics["peak"].config(text=fmt(peak, " dBFS"))
        self._metrics["clips"].config(text=str(clips),
                                       fg=C["red"] if clips > 0 else C["dim"])
        self._metrics["frames"].config(text=str(frames))
        mm = int(elapsed // 60)
        ss = int(elapsed % 60)
        self._metrics["dur"].config(text=f"{mm:02d}:{ss:02d}")
        if cal is not None and nf > -999:
            self._metrics["dbm"].config(text=f"{nf + cal:.1f} dBm/Hz", fg=C["amber"])
        else:
            self._metrics["dbm"].config(text="--- (non cal.)", fg=C["dim"])

        # CAT display
        if cat_f:
            mhz = cat_f / 1e6
            self.lbl_cat_freq.config(text=f"{mhz:.3f} MHz", fg=C["amber"])
        if cat_m:
            self.lbl_cat_mode.config(text=cat_m, fg=C["cyan"])
        self.dot_cat.config(fg=C["green"] if cat_c else C["dim"])

        # PSD
        if psd is not None and freqs is not None:
            self.line_psd.set_xdata(freqs)
            self.line_psd.set_ydata(psd)
            self.ax_psd.set_xlim(0, SAMPLE_RATE/2)

            try:
                self.fill_psd.remove()
            except Exception:
                pass
            self.fill_psd = self.ax_psd.fill_between(
                freqs, -160, psd, color=C["cyan"], alpha=0.07)

            if nf > -999:
                self.line_nf_h.set_ydata([nf, nf])
                self.txt_nf_psd.set_position((200, nf + 2))
                self.txt_nf_psd.set_text(
                    f"P{STATE.percentile} = {nf:.1f} dBFS/Hz")

            self.canvas_psd.draw_idle()

        # Historique
        if len(hist) > 1:
            t0 = hist[0][0]
            xs = [h[0] - t0 for h in hist]
            ys = [h[1]      for h in hist]
            self.line_hist.set_xdata(xs)
            self.line_hist.set_ydata(ys)
            self.ax_hist.set_xlim(0, max(xs[-1], 10))
            margin = 5
            self.ax_hist.set_ylim(min(ys) - margin, max(ys) + margin)
            self.line_hmin.set_ydata([nfmin, nfmin])
            self.canvas_hist.draw_idle()

    def _on_psd_hover(self, event):
        if event.inaxes != self.ax_psd or STATE.psd_avg is None:
            self.lbl_psd_tip.config(text="")
            return
        freq_hz = event.xdata
        if freq_hz is None:
            return
        freqs = np.fft.rfftfreq(STATE.fft_size, 1/SAMPLE_RATE)
        idx = int(np.argmin(np.abs(freqs - freq_hz)))
        if 0 <= idx < len(STATE.psd_avg):
            self.lbl_psd_tip.config(
                text=f"  {freq_hz:.0f} Hz  →  {STATE.psd_avg[idx]:.1f} dBFS/Hz")

    # ──────────────────────────────────────────
    #  ABOUT
    # ──────────────────────────────────────────
    def _about(self):
        msg = (f"{APP_NAME} {APP_VERSION}\n"
               f"Auteur : {APP_AUTHOR}\n\n"
               f"Mesure du plancher de bruit RF\n"
               f"via carte son + analyse PSD (dBFS/Hz)\n\n"
               f"IC-706MkIIH · EMU 0202\n"
               f"Contrôle CAT via Hamlib rigctld\n\n"
               f"Chaîne : Antenne → IC-706 AF OUT\n"
               f"→ Carte son LINE IN → FFT → PSD")
        messagebox.showinfo(f"À propos — {APP_NAME}", msg)

    def _on_close(self):
        self._stop()
        CAT.disconnect()
        self.destroy()

# =============================================================================
#  POINT D'ENTRÉE
# =============================================================================
def main():
    app = NFMApp()
    app.mainloop()

if __name__ == "__main__":
    main()
