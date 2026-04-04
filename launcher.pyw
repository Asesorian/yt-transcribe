"""
YT-Transcribe Launcher — GUI mínima para lanzar desde escritorio
Pega URL → clic → transcribe → abre el archivo resultante
"""
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(SCRIPT_DIR, "yt_transcribe.py")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "transcripciones")


class Launcher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("YT-Transcribe")
        self.root.geometry("520x260")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        # Centrar en pantalla
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 520) // 2
        y = (self.root.winfo_screenheight() - 260) // 2
        self.root.geometry(f"+{x}+{y}")

        # Título
        tk.Label(
            self.root, text="YT-Transcribe", font=("Segoe UI", 16, "bold"),
            fg="#e94560", bg="#1a1a2e"
        ).pack(pady=(15, 5))

        tk.Label(
            self.root, text="YouTube → Transcripción en Markdown",
            font=("Segoe UI", 9), fg="#8888aa", bg="#1a1a2e"
        ).pack()

        # URL input
        frame = tk.Frame(self.root, bg="#1a1a2e")
        frame.pack(pady=15, padx=20, fill="x")

        tk.Label(
            frame, text="URL del video:", font=("Segoe UI", 10),
            fg="#eeeeee", bg="#1a1a2e"
        ).pack(anchor="w")

        self.url_var = tk.StringVar()
        self.entry = tk.Entry(
            frame, textvariable=self.url_var, font=("Segoe UI", 11),
            bg="#16213e", fg="#ffffff", insertbackground="#e94560",
            relief="flat", bd=0
        )
        self.entry.pack(fill="x", pady=(5, 0), ipady=6)
        self.entry.focus()
        self.entry.bind("<Return>", lambda e: self.transcribe())

        # Botones
        btn_frame = tk.Frame(self.root, bg="#1a1a2e")
        btn_frame.pack(pady=10)

        self.btn = tk.Button(
            btn_frame, text="Transcribir", font=("Segoe UI", 11, "bold"),
            bg="#e94560", fg="white", relief="flat", padx=20, pady=6,
            cursor="hand2", command=self.transcribe
        )
        self.btn.pack(side="left", padx=5)

        self.force_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            btn_frame, text="Forzar Groq (mejor calidad)",
            variable=self.force_var, font=("Segoe UI", 9),
            fg="#8888aa", bg="#1a1a2e", selectcolor="#16213e",
            activebackground="#1a1a2e", activeforeground="#eeeeee"
        ).pack(side="left", padx=10)

        # Status
        self.status_var = tk.StringVar(value="Listo")
        self.status = tk.Label(
            self.root, textvariable=self.status_var, font=("Segoe UI", 9),
            fg="#8888aa", bg="#1a1a2e"
        )
        self.status.pack(pady=(0, 10))

        # Pegar desde clipboard al abrir si tiene URL de YouTube
        try:
            clip = self.root.clipboard_get()
            if "youtu" in clip:
                self.url_var.set(clip)
                self.entry.select_range(0, "end")
        except:
            pass

        self.root.mainloop()

    def transcribe(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL vacía", "Pega una URL de YouTube")
            return
        if "youtu" not in url and "youtube" not in url:
            messagebox.showwarning("URL inválida", "Eso no parece una URL de YouTube")
            return

        self.btn.config(state="disabled", text="Transcribiendo...")
        self.status_var.set("⏳ Procesando...")

        def run():
            cmd = [sys.executable, SCRIPT, url]
            if self.force_var.get():
                cmd.append("--force-audio")

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", cwd=SCRIPT_DIR
                )

                # Buscar archivo guardado en la salida
                output = result.stdout
                saved_line = [l for l in output.split("\n") if "Guardado en:" in l]

                if result.returncode == 0 and saved_line:
                    filepath = saved_line[0].split("Guardado en:")[-1].strip()
                    self.root.after(0, lambda: self.done(filepath))
                else:
                    error = result.stderr or result.stdout or "Error desconocido"
                    self.root.after(0, lambda: self.error(error[:300]))

            except Exception as e:
                self.root.after(0, lambda: self.error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def done(self, filepath):
        self.btn.config(state="normal", text="Transcribir")
        self.status_var.set(f"✅ Guardado: {os.path.basename(filepath)}")
        self.url_var.set("")

        if messagebox.askyesno("Transcripción lista", f"¿Abrir el archivo?\n\n{filepath}"):
            os.startfile(filepath)

    def error(self, msg):
        self.btn.config(state="normal", text="Transcribir")
        self.status_var.set("❌ Error")
        messagebox.showerror("Error", msg)


if __name__ == "__main__":
    Launcher()
