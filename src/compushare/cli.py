import typer
from rich.console import Console

app = typer.Typer(
    name="compushare",
    help="CompuShare: La Computadora Compartida - Red distribuida de IA",
    add_completion=False,
)
console = Console()

@app.command()
def info():
    """Muestra información sobre el estado del nodo y la red CompuShare."""
    console.print("[bold cyan]CompuShare[/bold cyan] - [italic]La Computadora Compartida[/italic]")
    console.print("Estado: [yellow]Inicializando arquitectura[/yellow]")

@app.command()
def unirse(
    rele: str = typer.Option("wss://rele.compushare.local", help="URL del relé de coordinación"),
    modelo: str = typer.Option("default", help="Nombre o ruta del modelo local a compartir")
):
    """Conecta esta máquina a la red CompuShare como nodo trabajador."""
    console.print(f"[bold green]Conectando al relé:[/bold green] {rele}")
    console.print(f"Modelo asignado: {modelo}")

@app.command()
def servir(
    puerto: int = typer.Option(8000, help="Puerto para exponer la API compatible con OpenAI")
):
    """Inicia el servidor local de entrada para enviar prompts a la red CompuShare."""
    console.print(f"[bold green]Iniciando API compatible con OpenAI en el puerto {puerto}...[/bold green]")

if __name__ == "__main__":
    app()
