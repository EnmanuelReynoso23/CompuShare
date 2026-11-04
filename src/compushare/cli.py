"""CLI de CompuShare - La Computadora Compartida.
Permite iniciar el relé maestro, unirse como nodo trabajador,
consultar el estado del enjambre y probar inferencia distribuida.
"""

import asyncio
import json
import logging
import sys
from typing import Optional

# Asegurar codificación UTF-8 en consolas Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import aiohttp
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from compushare.nodo.maestro import NodoMaestro
from compushare.nodo.trabajador import NodoTrabajador, detectar_hardware

app = typer.Typer(
    name="compushare",
    help="CompuShare: Red Distribuida de Cómputo e Inferencia de Inteligencia Artificial",
    add_completion=False,
)
console = Console(highlight=False)


def configurar_logging(verbose: bool = False):
    nivel = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def servir(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Dirección IP de escucha"),
    puerto: int = typer.Option(8000, "--puerto", "-p", help="Puerto HTTP y WebSocket"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Modo detallado de depuración"),
):
    """Inicia el Relé Maestro (Orquestador + API OpenAI en /v1/chat/completions)."""
    configurar_logging(verbose)
    
    panel = Panel.fit(
        f"[bold cyan]CompuShare - Relé Maestro[/bold cyan]\n"
        f"[green]Dashboard web:[/green] http://{host}:{puerto}/\n"
        f"[green]Endpoint OpenAI:[/green] http://{host}:{puerto}/v1/chat/completions\n"
        f"[green]Modelos:[/green] http://{host}:{puerto}/v1/models\n"
        f"[green]WebSocket Workers:[/green] ws://{host}:{puerto}/ws/worker\n\n"
        f"[dim]Presiona Ctrl+C para detener el servicio.[/dim]",
        title="[bold yellow]Servidor Activo[/bold yellow]",
        border_style="cyan",
    )
    console.print(panel)

    maestro = NodoMaestro(host=host, puerto=puerto)
    try:
        asyncio.run(maestro.iniciar())
    except KeyboardInterrupt:
        console.print("\n[yellow]Deteniendo Relé Maestro...[/yellow]")
        asyncio.run(maestro.detener())
        console.print("[green]Relé detenido con éxito.[/green]")


@app.command()
def unirse(
    rele: str = typer.Option("ws://127.0.0.1:8000/ws/worker", "--rele", "-r", help="URL del Relé Maestro"),
    modelo: str = typer.Option("qwen2.5-coder", "--modelo", "-m", help="Identificador del modelo a servir"),
    backend: str = typer.Option(
        "simulado",
        "--backend",
        "-b",
        help="Motor de inferencia: 'llama.cpp', 'ollama', 'vllm' o 'simulado'",
    ),
    backend_url: Optional[str] = typer.Option(None, "--backend-url", help="URL del backend local (ej. http://127.0.0.1:8080)"),
    worker_id: Optional[str] = typer.Option(None, "--id", help="Identificador único del trabajador"),
    slots: int = typer.Option(2, "--slots", "-s", help="Cantidad de tareas concurrentes soportadas"),
    tps: float = typer.Option(30.0, "--tps", help="Tokens por segundo estimados"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Modo detallado"),
):
    """Conecta este equipo o instancia a la red CompuShare como Nodo Trabajador."""
    configurar_logging(verbose)
    gpu, vram = detectar_hardware()

    panel = Panel.fit(
        f"[bold green]Conectando Trabajador al Enjambre CompuShare[/bold green]\n"
        f"Relé destino: [cyan]{rele}[/cyan]\n"
        f"Modelo: [yellow]{modelo}[/yellow]\n"
        f"Backend: [magenta]{backend}[/magenta]\n"
        f"Hardware detectado: [bold white]{gpu}[/bold white] ({vram} MB VRAM)\n"
        f"Slots concurrentes: [white]{slots}[/white]",
        title="[bold green]Nodo Trabajador[/bold green]",
        border_style="green",
    )
    console.print(panel)

    trabajador = NodoTrabajador(
        rele_url=rele,
        modelo=modelo,
        backend=backend,
        backend_url=backend_url,
        worker_id=worker_id,
        max_slots=slots,
        tokens_per_second=tps,
    )

    try:
        asyncio.run(trabajador.conectar())
    except KeyboardInterrupt:
        console.print("\n[yellow]Desconectando trabajador...[/yellow]")
        asyncio.run(trabajador.detener())
        console.print("[green]Trabajador desconectado limpiamente.[/green]")


@app.command()
def probar(
    url: str = typer.Option("http://127.0.0.1:8000", "--url", "-u", help="URL base del Relé Maestro"),
    modelo: str = typer.Option("qwen2.5-coder", "--modelo", "-m", help="Nombre del modelo a invocar"),
    prompt: str = typer.Option("¿Qué es CompuShare y cómo funciona?", "--prompt", "-p", help="Prompt para la inferencia"),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Activar streaming token a token"),
):
    """Envía una consulta de prueba al Relé Maestro mediante la API OpenAI."""
    async def _test():
        endpoint = f"{url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": modelo,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }

        console.print(f"[bold cyan]Enviando prompt a:[/bold cyan] {endpoint}")
        console.print(f"[bold yellow]Modelo:[/bold yellow] {modelo}")
        console.print(f"[bold green]Prompt:[/bold green] {prompt}\n")
        console.print("-" * 60)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        console.print(f"[bold red]Error HTTP {resp.status}:[/bold red] {error_text}")
                        return

                    if stream:
                        async for linea in resp.content:
                            linea_str = linea.decode("utf-8").strip()
                            if not linea_str:
                                continue
                            if linea_str.startswith("data: "):
                                data_part = linea_str[6:].strip()
                                if data_part == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_part)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                    if delta:
                                        sys.stdout.write(delta)
                                        sys.stdout.flush()
                                except Exception:
                                    pass
                        sys.stdout.write("\n")
                    else:
                        data = await resp.json()
                        contenido = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        console.print(contenido)

                console.print("-" * 60)
                console.print("[bold green]Inferencia completada con éxito vía CompuShare Swarm.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Error al conectar con el relé:[/bold red] {e}")

    asyncio.run(_test())


@app.command()
def info(
    url: Optional[str] = typer.Option(None, "--url", "-u", help="URL del Relé Maestro para consultar estado de la red"),
):
    """Muestra información sobre el hardware local y el estado de la red CompuShare."""
    console.print("[bold cyan]CompuShare: La Computadora Compartida[/bold cyan]")
    gpu, vram = detectar_hardware()
    
    t_hw = Table(title="Hardware Local", show_header=True)
    t_hw.add_column("Propiedad", style="yellow")
    t_hw.add_column("Valor", style="white")
    t_hw.add_row("Acelerador / GPU", gpu)
    t_hw.add_row("VRAM Total", f"{vram} MB" if vram else "N/A (CPU)")
    console.print(t_hw)

    if url:
        async def _consultar_red():
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(f"{url.rstrip('/')}/api/status", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            console.print(f"\n[bold green]Estado de la Red CompuShare ({url}):[/bold green]")
                            console.print(f"Nodos activos: [cyan]{data.get('nodos_activos')}[/cyan]")
                            console.print(f"Slots totales: [cyan]{data.get('slots_totales')}[/cyan]")
                            console.print(f"Tareas completadas: [cyan]{data.get('tareas_completadas')}[/cyan]")
                            console.print(f"Modelos disponibles: [yellow]{', '.join(data.get('modelos_disponibles', [])) or 'Ninguno'}[/yellow]")
                        else:
                            console.print(f"[red]Error consultando el relé: HTTP {resp.status}[/red]")
                except Exception as e:
                    console.print(f"[red]No se pudo conectar con el relé en {url}: {e}[/red]")

        asyncio.run(_consultar_red())


if __name__ == "__main__":
    app()
