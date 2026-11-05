"""Interfaz de linea de comandos (CLI) de CompuShare.
Permite inspeccionar hardware local, unirse a la red como trabajador aportando computo,
iniciar el nodo maestro que sirve el modelo repartido (llama-server de llama.cpp en 127.0.0.1),
transcribir textos por la red para verificar la salida,
y calcular estimaciones de rendimiento matematico.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
import typer
from rich.console import Console
from rich.table import Table

from compushare.nodo.maestro import NodoMaestro
from compushare.nodo.trabajador import NodoTrabajador, buscar_binario
from compushare import transcribir as transcripcion
from fabric import calculo, escenarios

app = typer.Typer(
    name="compushare",
    help="CompuShare: La Computadora Compartida - Red distribuida de IA",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _principal(detalle: bool = typer.Option(True, "--detalle/--silencio", help="Mostrar el registro de lo que hace cada nodo")) -> None:
    """CompuShare: La Computadora Compartida - Red distribuida de IA"""
    # Sin esto los log.info de fabric y de los nodos (tubos elegidos, comando de llama-server) no salian.
    logging.basicConfig(level=logging.INFO if detalle else logging.WARNING, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for ruidoso in ("websockets", "httpx", "httpcore"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    # SIGTERM (systemd, kill) pasa por el mismo camino que Ctrl+C: el finally de cada comando detiene los hijos.
    signal.signal(signal.SIGTERM, _sigterm)


def _sigterm(_num, _marco) -> None:
    raise KeyboardInterrupt


def resolver_clave(clave_param: str = "", clave_archivo: Optional[Path] = None) -> str:
    """Resuelve la clave de autenticacion sin imprimirla nunca en consola ni en logs."""
    if clave_param:
        return clave_param
    env_clave = os.environ.get("FABRIC_CLAVE")
    if env_clave:
        return env_clave
    if clave_archivo and clave_archivo.is_file():
        try:
            return clave_archivo.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    config_env = Path.home() / ".config/synrag/fabric.env"
    if config_env.is_file():
        try:
            for linea in config_env.read_text(encoding="utf-8").splitlines():
                linea = linea.strip()
                if linea.startswith("FABRIC_CLAVE="):
                    val = linea.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            pass
    return ""


def obtener_info_gpus() -> list[dict[str, Any]]:
    """Consulta informacion de GPUs locales mediante nvidia-smi."""
    gpus: list[dict[str, Any]] = []
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        for linea in r.stdout.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            partes = [p.strip() for p in linea.split(",")]
            if len(partes) >= 3:
                nombre = partes[0]
                total_mb = float(partes[1])
                libre_mb = float(partes[2])
                gpus.append(
                    {
                        "nombre": nombre,
                        "total_gb": round(total_mb / 1024.0, 2),
                        "libre_gb": round(libre_mb / 1024.0, 2),
                    }
                )
    except Exception:
        pass
    return gpus


def obtener_info_ram() -> dict[str, float]:
    """Lee memoria RAM del sistema operativo."""
    ram = {"total_gb": 0.0, "disponible_gb": 0.0}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for linea in f:
                if linea.startswith("MemTotal:"):
                    ram["total_gb"] = round(float(linea.split()[1]) / 1e6, 2)
                elif linea.startswith("MemAvailable:"):
                    ram["disponible_gb"] = round(float(linea.split()[1]) / 1e6, 2)
    except Exception:
        pass
    if ram["total_gb"] == 0 and sys.platform == "win32":
        try:
            import ctypes

            class _Estado(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            estado = _Estado()
            estado.dwLength = ctypes.sizeof(_Estado)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
                ram["total_gb"] = round(estado.ullTotalPhys / 1e9, 2)
                ram["disponible_gb"] = round(estado.ullAvailPhys / 1e9, 2)
        except Exception:
            pass
    return ram


def medir_banda_memoria(
    llama_bin: Optional[str] = None,
    modelo_ruta: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Mide ancho de banda de memoria efectivo ejecutando llama-bench bajo medir.lock."""
    try:
        bin_bench = buscar_binario("llama-bench", llama_bin)
    except FileNotFoundError:
        return None

    modelo_a_usar = modelo_ruta
    if modelo_a_usar is None or not modelo_a_usar.is_file():
        carpeta_modelos = Path.home() / ".cache/fabric-descargas/modelos"
        candidatos = list(carpeta_modelos.glob("*.gguf"))
        if candidatos:
            candidatos.sort(key=lambda p: p.stat().st_size)
            modelo_a_usar = candidatos[0]

    if modelo_a_usar is None or not modelo_a_usar.is_file():
        return None

    cmd = [
        str(bin_bench),
        "-m",
        str(modelo_a_usar),
        "-n",
        "8",
        "-p",
        "8",
        "-r",
        "1",
        "-o",
        "json",
    ]

    lock_file = "/home/reyno/Proyectos/cs-agentes/medir.lock"
    cmd_completo = ["flock", lock_file] + cmd if shutil.which("flock") and os.path.exists(os.path.dirname(lock_file)) else cmd

    try:
        r = subprocess.run(cmd_completo, capture_output=True, text=True, check=True, timeout=30)
        datos = json.loads(r.stdout)
        for item in datos:
            if item.get("n_gen", 0) > 0:
                avg_ts = float(item.get("avg_ts", 0.0))
                tam_modelo = float(item.get("model_size", 0.0))
                banda_gbs = round((avg_ts * tam_modelo) / 1e9, 2)
                return {
                    "banda_gbs": banda_gbs,
                    "tok_s": round(avg_ts, 2),
                    "modelo": modelo_a_usar.name,
                }
    except Exception:
        pass
    return None


@app.command()
def info(
    llama_bin: Optional[str] = typer.Option(None, help="Directorio con binarios de llama.cpp"),
    modelo: Optional[Path] = typer.Option(None, help="Modelo GGUF para medir ancho de banda con llama-bench"),
    medir_banda: bool = typer.Option(True, help="Ejecutar medicion de ancho de banda si llama-bench esta disponible"),
):
    """Muestra equipos de esta maquina (GPU, VRAM, RAM, ancho de banda) y modelos compatibles."""
    console.print("[bold]Informacion de Recursos Locales - CompuShare[/bold]\n")

    gpus = obtener_info_gpus()
    ram = obtener_info_ram()

    tabla_hw = Table(title="Hardware Local", show_header=True, header_style="bold")
    tabla_hw.add_column("Componente", style="cyan")
    tabla_hw.add_column("Detalle")
    tabla_hw.add_column("Capacidad Total")
    tabla_hw.add_column("Disponible / Libre")

    if gpus:
        for i, g in enumerate(gpus):
            tabla_hw.add_row(f"GPU {i}", g["nombre"], f"{g['total_gb']} GB VRAM", f"{g['libre_gb']} GB VRAM")
    else:
        tabla_hw.add_row("GPU", "No se detecto GPU NVIDIA via nvidia-smi", "0.0 GB", "0.0 GB")

    tabla_hw.add_row(
        "RAM",
        "Memoria del Sistema",
        f"{ram['total_gb']} GB" if ram["total_gb"] > 0 else "No detectado",
        f"{ram['disponible_gb']} GB" if ram["disponible_gb"] > 0 else "No detectado",
    )

    console.print(tabla_hw)
    console.print()

    vram_total = sum(g["total_gb"] for g in gpus)
    mem_total_local = vram_total if vram_total > 0 else ram["disponible_gb"]

    if medir_banda:
        res_banda = medir_banda_memoria(llama_bin, modelo)
        if res_banda:
            console.print(
                f"[bold green]Ancho de banda de memoria medido:[/bold green] {res_banda['banda_gbs']} GB/s "
                f"(a {res_banda['tok_s']} tok/s evaluado con {res_banda['modelo']})\n"
            )
        else:
            console.print("[yellow]Medicion de ancho de banda con llama-bench no realizada (modelo o binario ausente)[/yellow]\n")

    modelos_estandar = [
        calculo.Modelo("Qwen3.5-0.8B Q8_0", 0.81, capas=24, d_modelo=1024),
        calculo.QWEN35_9B_Q8,
        calculo.QWEN38_27B_Q4,
        calculo.QWEN36_35B_A3B_Q4,
    ]

    tabla_mod = Table(title="Compatibilidad de Modelos (fabric/calculo.py)", show_header=True, header_style="bold")
    tabla_mod.add_column("Modelo", style="cyan", no_wrap=True)
    tabla_mod.add_column("Pesos GGUF")
    tabla_mod.add_column("Memoria Requerida (4K ctx)")
    tabla_mod.add_column("¿Cabe en VRAM?")
    tabla_mod.add_column("¿Cabe en RAM/CPU?")
    tabla_mod.add_column("¿Requiere Red?")

    for m in modelos_estandar:
        req = calculo.memoria_necesaria_gb(m, contexto=4096, flujos=1)
        cabe_vram = "[green]Si[/green]" if vram_total >= req else "[red]No[/red]"
        cabe_ram = "[green]Si[/green]" if ram["disponible_gb"] >= req else "[red]No[/red]"
        requiere_red = "[red]Si[/red]" if (vram_total < req and ram["disponible_gb"] < req) else "[green]No[/green]"

        tabla_mod.add_row(
            m.nombre,
            f"{m.pesos_gb:.2f} GB",
            f"{req:.2f} GB",
            cabe_vram,
            cabe_ram,
            requiere_red,
        )

    console.print(tabla_mod)


@app.command()
def unirse(
    rele: str = typer.Option(
        os.environ.get("FABRIC_RELE", "ws://127.0.0.1:59095"),
        help="URL del rele de coordinacion",
    ),
    clave: str = typer.Option("", help="Clave secreta del rele (opcional si se define en entorno o archivo)"),
    clave_archivo: Optional[Path] = typer.Option(None, help="Ruta al archivo con la clave secreta"),
    nombre: str = typer.Option("", help="Nombre identificador de este trabajador"),
    puerto_rpc: int = typer.Option(59052, help="Puerto local para ggml-rpc-server en 127.0.0.1"),
    vram_gb: float = typer.Option(0.0, help="Memoria VRAM que presta (0 para autodeteccion)"),
    llama_bin: Optional[str] = typer.Option(None, help="Directorio con binarios de llama.cpp"),
    threads: int = typer.Option(4, help="Hilos de CPU para el servidor RPC (maximo 6)"),
    cache: bool = typer.Option(False, help="Habilitar cache local de archivos en ggml-rpc-server"),
    duracion: float = typer.Option(0.0, help="Segundos tras los cuales salir (0 para infinito)"),
):
    """Conecta esta maquina a la red CompuShare como trabajador de computo."""
    clave_res = resolver_clave(clave, clave_archivo)
    if not clave_res:
        err_console.print("Error: No se especifico la clave. Usa --clave, --clave-archivo o define FABRIC_CLAVE.")
        raise typer.Exit(code=1)

    console.print(f"[bold green]Uniendo equipo a la red CompuShare[/bold green]")
    console.print(f"Rele: {rele}")
    console.print(f"Servidor RPC local: 127.0.0.1:{puerto_rpc} (hilos: {min(6, max(1, threads))})")

    nodo = NodoTrabajador(
        rele_url=rele,
        clave=clave_res,
        nombre=nombre,
        puerto_rpc=puerto_rpc,
        vram_gb=vram_gb,
        llama_bin=llama_bin,
        threads=threads,
        cache=cache,
    )

    try:
        asyncio.run(nodo.conectar(duracion=duracion))
    except KeyboardInterrupt:
        console.print("\nDesconexion solicitada por el usuario.")
    finally:
        nodo.detener()
    console.print("Trabajador finalizado.")


@app.command()
def servir(
    modelo: Path = typer.Option(..., help="Ruta al archivo GGUF del modelo a servir"),
    rele: str = typer.Option("ws://127.0.0.1:59095", help="URL del rele de coordinacion"),
    clave: str = typer.Option("", help="Clave secreta del rele (opcional si se define en entorno o archivo)"),
    clave_archivo: Optional[Path] = typer.Option(None, help="Ruta al archivo con la clave secreta"),
    puerto: int = typer.Option(59000, help="Puerto de llama-server en 127.0.0.1"),
    puerto_rele: Optional[int] = typer.Option(None, help="Puerto para el rele local (si se inicia)"),
    iniciar_rele: bool = typer.Option(True, help="Iniciar servidor de rele local si no existe"),
    llama_bin: Optional[str] = typer.Option(None, help="Directorio con binarios de llama.cpp"),
    espera_trabajadores: float = typer.Option(3.0, help="Segundos para esperar conexion de trabajadores"),
    min_trabajadores: int = typer.Option(0, help="Minimo de trabajadores requeridos para distribuir"),
    threads: int = typer.Option(4, help="Hilos de CPU para llama-server (maximo 6)"),
    ctx_size: int = typer.Option(4096, help="Tamano del contexto en tokens (por flujo)"),
    paralelo: int = typer.Option(1, help="Peticiones atendidas a la vez (flujos)"),
    especulativa: str = typer.Option("", help="Decodificacion especulativa de llama.cpp: ngram-mod, draft-mtp... (vacio = ninguna)"),
    espera_carga: float = typer.Option(900.0, help="Segundos maximos para que llama-server cargue el modelo"),
    reinicios: int = typer.Option(3, help="Veces que se relanza llama-server si se cae (por ejemplo, si se cae un trabajador)"),
    duracion: float = typer.Option(0.0, help="Segundos tras los cuales salir (0 para infinito)"),
):
    """Inicia el nodo maestro: reparte el modelo entre los trabajadores y sirve llama-server en 127.0.0.1."""
    if not modelo.is_file():
        err_console.print(f"Error: El archivo de modelo no existe: {modelo}")
        raise typer.Exit(code=1)

    clave_res = resolver_clave(clave, clave_archivo)
    if not clave_res:
        err_console.print("Error: No se especifico la clave. Usa --clave, --clave-archivo o define FABRIC_CLAVE.")
        raise typer.Exit(code=1)

    console.print("[bold green]Iniciando Nodo Maestro CompuShare[/bold green]")
    console.print(f"Modelo: {modelo}")
    console.print(f"llama-server: http://127.0.0.1:{puerto}")
    console.print(f"Rele: {rele}")

    maestro = NodoMaestro(
        modelo=modelo,
        rele_url=rele,
        clave=clave_res,
        puerto_api=puerto,
        puerto_rele=puerto_rele,
        iniciar_rele_local=iniciar_rele,
        llama_bin=llama_bin,
        espera_trabajadores=espera_trabajadores,
        min_trabajadores=min_trabajadores,
        threads=threads,
        ctx_size=ctx_size,
        paralelo=paralelo,
        especulativa=especulativa,
        espera_carga=espera_carga,
        max_reinicios=reinicios,
    )

    try:
        asyncio.run(maestro.iniciar(duracion=duracion))
    except KeyboardInterrupt:
        console.print("\nServicio detenido por el usuario.")
    finally:
        maestro.detener()
    console.print("Servidor maestro finalizado.")


@app.command()
def calcular(
    rtt_ms: float = typer.Option(40.0, help="Ida y vuelta promedio en milisegundos"),
    subida_mbps: float = typer.Option(10.0, help="Velocidad de subida en Mb/s"),
    modelo: Optional[str] = typer.Option(None, help="Filtrar por modelo especifico (27b, 35b, 9b)"),
):
    """Imprime los escenarios matematicos de fabric/escenarios.py para los parametros dados."""
    console.print(f"[bold]Calculo de Escenarios Distribuidos (RTT: {rtt_ms} ms, Subida: {subida_mbps} Mb/s)[/bold]\n")

    if rtt_ms == 40.0 and subida_mbps == 10.0 and modelo is None:
        escenarios.main()
        return

    maestro, cands = escenarios.amigos(rtt=rtt_ms, subida=subida_mbps)
    modelos = [escenarios.QWEN38_27B, escenarios.MOE_35B_A3B]
    if modelo:
        mod_l = modelo.lower()
        if "27" in mod_l:
            modelos = [escenarios.QWEN38_27B]
        elif "35" in mod_l:
            modelos = [escenarios.MOE_35B_A3B]

    hdr = "| Equipos:capas | ms calculo | ms red | ms por pasada | palabras/s por pregunta | palabras/s total | Caso |"
    sep = "|---|---|---|---|---|---|---|"

    for m in modelos:
        console.print(f"## {m.nombre} (RTT {rtt_ms} ms, subida {subida_mbps} Mb/s)\n")
        console.print(hdr)
        console.print(sep)
        for etiqueta, bpp in (
            ("grafo reenviado (315 KB, medido)", calculo.BYTES_POR_PALABRA_MEDIDO),
            ("solo activacion (grafo reutilizado)", None),
        ):
            for flujos in (1, 4, 8):
                try:
                    r = calculo.elegir_optimo(m, maestro, cands, bpp, flujos)
                    console.print(escenarios.fila(r, f" {etiqueta}, {flujos} flujos |"))
                except ValueError:
                    console.print(f"| no cabe | | | | | | {etiqueta}, {flujos} flujos |")
        console.print()


@app.command()
def probar(
    url: str = typer.Option("http://127.0.0.1:59000", "--url", "-u", help="URL de llama-server (el del maestro)"),
    prompt: str = typer.Option("Explica en dos frases que es la computacion distribuida.", "--prompt", "-p", help="Pregunta"),
    max_tokens: int = typer.Option(256, help="Maximo de tokens a generar"),
    pensar: bool = typer.Option(False, "--pensar/--sin-pensar", help="Dejar que el modelo razone antes de contestar"),
):
    """Hace una pregunta al maestro y muestra la respuesta mientras se genera."""
    from fabric.nodo import motores

    console.print(f"[bold]Preguntando a[/bold] {url}\n")

    async def caso() -> dict[str, Any]:
        motor = motores.Generar(url, max_tokens=max_tokens)

        async def avance(texto: str) -> None:
            sys.stdout.write(texto)
            sys.stdout.flush()

        try:
            return await motor.correr({"prompt": prompt, "pensar": pensar, "temperatura": 0.3}, avance)
        finally:
            await motor.http.aclose()

    try:
        r = asyncio.run(caso())
    except Exception as e:
        err_console.print(f"\nError al hablar con llama-server: {e}")
        raise typer.Exit(code=1)
    sys.stdout.write("\n")
    console.print(
        f"\n{r['tokens_entrada']} tokens de entrada, {r['tokens_salida']} de salida, "
        f"{r['tokens_por_segundo']} tok/s, primer token a los {r['primer_token_s']} s, total {r['segundos']} s"
    )


@app.command()
def transcribir(
    archivo: Path = typer.Argument(..., help="Texto a transcribir (por ejemplo un libro en .txt)"),
    url: str = typer.Option("http://127.0.0.1:59000", "--url", "-u", help="URL de llama-server (el del maestro)"),
    flujos: int = typer.Option(1, help="Trozos a la vez (el maestro debe tener --paralelo igual o mayor)"),
    chars: int = typer.Option(1500, help="Caracteres por trozo"),
    desde: int = typer.Option(0, help="Primer trozo"),
    cuantos: int = typer.Option(0, help="Cuantos trozos (0 = todos)"),
    salida: Path = typer.Option(Path("transcripcion"), help="Carpeta donde guardar transcripcion.json y el texto copiado"),
    espera_caida: float = typer.Option(900.0, help="Si llama-server deja de responder, segundos a esperar a que el maestro lo relance (0 = no esperar)"),
    retomar: bool = typer.Option(False, help="Saltar los trozos ya bien hechos en --salida"),
):
    """El modelo de la red copia el texto trozo a trozo; cada trozo se compara letra por letra con el original."""
    if not archivo.is_file():
        err_console.print(f"Error: no existe {archivo}")
        raise typer.Exit(code=1)
    textos = transcripcion.trozos_de_archivo(archivo, chars)
    textos = textos[desde: desde + cuantos] if cuantos else textos[desde:]
    console.print(f"[bold]{len(textos)} trozos[/bold] ({sum(map(len, textos))} caracteres), {flujos} a la vez, contra {url}")

    def avance(i: int, f: dict[str, Any]) -> None:
        if "error" in f:
            console.print(f"  trozo {i + 1}: [red]error[/red] {f['error']}")
            return
        marca = "[green]exacto[/green]" if f["exacto"] else f"[yellow]parecido {f['parecido']}[/yellow]"
        console.print(f"  trozo {i + 1}: {marca}, {f['gen_n']} tokens a {(f['gen_tok_s'] or 0):.1f} tok/s, {f['pared_s']} s")

    def recuperar() -> bool:
        console.print(f"  [yellow]llama-server no responde; esperando hasta {espera_caida:.0f} s a que vuelva[/yellow]")
        return transcripcion.esperar_servidor(url, espera_caida)

    res = transcripcion.transcribir(url, textos, flujos=flujos, salida_dir=salida, al_terminar_trozo=avance,
                                    recuperar=recuperar if espera_caida > 0 else None, retomar=retomar)
    tabla = Table(title="Transcripcion (medido)", show_header=False)
    for k, v in res.items():
        tabla.add_row(k, str(v))
    console.print(tabla)
    console.print(f"Guardado en {salida.resolve()}")


if __name__ == "__main__":
    app()
