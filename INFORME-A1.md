# Informe del Agente A1: Simulador de Red TCP en Espacio de Usuario

- Fecha: 2026-11-04
- Worktree: `/home/reyno/Proyectos/cs-a1`
- Rama: `a1-simulador-red`
- Módulos creados: `fabric/simulador/red.py`, `fabric/simulador/__init__.py`, `fabric/simulador/medir_costo.py`
- Pruebas creadas: `fabric/pruebas/test_simulador_red.py`
- Archivo de datos: `fabric/simulador/costo_proxy.json`

---

## 1. Qué se hizo

Se implementó el simulador de enlace de red TCP en espacio de usuario (`fabric/simulador/red.py`), permitiendo medir y evaluar la red de CompuShare en una misma computadora sin privilegios de administrador ni alterar la pila de red del sistema operativo.

### Características técnicas del enlace (`Enlace`):
1. **Latencia de ida**: retardo configurable en milisegundos aplicado independientemente a cada sentido del tráfico.
2. **Variación (jitter)**: distribución normal recortada en 0 (`random.gauss(latencia, variacion)` acotada inferiormente por 0).
3. **Ancho de banda**: cubeta de fichas (token bucket / virtual pacing) independiente por sentido, dimensionada en Mb/s ($1\text{ Mb/s} = 125.000\text{ bytes/s}$), que restringe el ritmo de transmisión sin distorsión por ráfagas artificiales.
4. **Pérdida de paquetes**: modelada como retraso de retransmisión (1 RTO), reflejando que TCP garantiza entrega confiable y libre de pérdidas a nivel de aplicación. Cuando un paquete se marca como perdido, sufre un retardo adicional igual a 1 RTO, bloqueando la entrega de bytes subsiguientes (Head-of-Line blocking) para garantizar estrictamente el orden del flujo de bytes.
5. **Ruta rápida sin retrasos**: cuando latencia, variación y pérdida son 0 y el ancho de banda no está limitado, se utiliza un canal de transferencia directa optimizado con mínimo overhead.
6. **Perfiles predefinidos de `fabric/PRUEBA-10.md`**:
   - `misma ciudad`: 10 ms latencia, 2 ms variación, 100 Mb/s.
   - `mismo país`: 40 ms latencia, 10 ms variación, 50 Mb/s.
   - `otro continente`: 120 ms latencia, 20 ms variación, 20 Mb/s.
   - `cloudflare medido`: 237 ms latencia, 40 ms variación, sin límite fijo (medir).
   - Admite resolución de alias con o sin tildes (`mismo pais`, `pais`, `ciudad`, `continente`, `cloudflare`).
7. **Interfaz dual**:
   - Línea de comandos: `python -m fabric.simulador.red --escuchar 127.0.0.1:PUERTO --destino 127.0.0.1:PUERTO --latencia-ms 40 --variacion-ms 10 --mbps 50 --perdida 0.5`.
   - Clase importable `Enlace`: compatible con context managers asíncronos (`async with Enlace(...) as enlace:`) para que el futuro `escenario.py` gestione múltiples enlaces concurrentes en un único proceso.

---

## 2. Cifras medidas

Todas las mediciones se realizaron en la máquina local usando el entorno virtual `/home/reyno/Proyectos/CompuShare/.venv/bin/python`.

### A. Costo propio del proxy sin retrasos (overhead inherente)

- Fecha de medición: 2026-11-04T13:49:48.636092+00:00
- Comando ejecutado:
  ```bash
  /home/reyno/Proyectos/CompuShare/.venv/bin/python fabric/simulador/medir_costo.py
  ```
- Archivo de resultado: `fabric/simulador/costo_proxy.json`
- Plataforma: Linux 7.2.8-2-cachyos (x86_64), Python 3.12.15

| Métrica | Conexión directa TCP local | A través de Enlace (sin retrasos) | Costo propio (diferencia) |
|---|---|---|---|
| RTT promedio (500 pings de 64 bytes) | 0.0215 ms | 0.0264 ms | **+0.0049 ms** (extra de ida y vuelta) |
| RTT mínimo | 0.0182 ms | 0.0238 ms | +0.0056 ms |
| RTT máximo | 0.0528 ms | 0.0486 ms | -0.0042 ms |
| Caudal máximo (eco de 100 MB) | 1098.83 MB/s | **472.64 MB/s** | 43.01 % del caudal directo |
| Duración eco 100 MB | 0.0910 s | 0.2116 s | +0.1206 s |

**Conclusión del costo propio**:
El proxy en espacio de usuario añade únicamente **0.0049 ms (~5 microsegundos)** de latencia de ida y vuelta, y alcanza un caudal máximo de **472.64 MB/s**, ampliamente suficiente para emular enlaces de hasta varios gigabits por segundo sin introducir cuellos de botella ajenos a la simulación.

---

### B. Validación de requerimientos en pruebas unitarias

- Comando ejecutado:
  ```bash
  /home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -v fabric/pruebas/test_simulador_red.py -p no:cacheprovider
  ```
- Resultado: 8 pruebas en verde en 1.53 s.

1. **Latencia de ida y vuelta (+-15 % de 2x latencia configurada)**:
   - Configuración: `latencia_ms = 40.0`, `variacion_ms = 0.0`.
   - RTT teórico esperado: $2 \times 40.0 = 80.0\text{ ms}$.
   - Rango permitido (+-15 %): $[68.0\text{ ms}, 92.0\text{ ms}]$.
   - RTT promedio medido (5 pings): **81.95 ms** (+2.44 % de desviación respecto al valor teórico).
   - Resultado: superado dentro de tolerancia.

2. **Ancho de banda (+-15 % del valor configurado)**:
   - Configuración: `mbps = 20.0`, transferencia eco de 1.250.000 bytes (10 Mb).
   - Tiempo teórico esperado: $10\text{ Mb} / 20\text{ Mb/s} = 0.500\text{ s}$.
   - Rango permitido (+-15 %): $[17.0\text{ Mb/s}, 23.0\text{ Mb/s}]$.
   - Caudal medido: **19.84 Mb/s** (-0.80 % de desviación respecto a 20.0 Mb/s).
   - Resultado: superado dentro de tolerancia.

3. **Pérdida con entrega completa y orden estricto de bytes**:
   - Configuración: `latencia_ms = 5.0`, `variacion_ms = 1.0`, `mbps = 50.0`, `perdida = 10.0 %`, `rto_ms = 20.0`.
   - Carga: 60.000 bytes deterministas sin patrón nulo.
   - Bytes enviados: 60.000.
   - Bytes recibidos: 60.000 (100.0 % recibidos, sin omisiones ni truncamientos).
   - Integridad: secuencia recibida idéntica byte a byte a la original (`recibidos == originales`).
   - Paquetes perdidos registrados: > 0 (retransmitidos tras RTO de 20 ms).
   - Resultado: superado.

4. **Cierre limpio**:
   - Se validaron múltiples conexiones consecutivas, desconexión abrupta por parte del cliente y parada global con `await enlace.detener()`.
   - Se comprobó la ausencia de tareas asyncio pendientes en segundo plano (`len(_tareas_conexiones) == 0`) y el cierre completo del socket de escucha del servidor.
   - Resultado: superado.

---

### C. Estado global de la suite de pruebas del proyecto

- Comando ejecutado:
  ```bash
  /home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -q fabric/pruebas -p no:cacheprovider
  ```
- Resultado:
  ```
  ..........................                                               [100%]
  26 passed, 1 skipped in 8.21s
  ```
- Todas las pruebas previas del repositorio junto con las nuevas pruebas de red quedaron en verde.

---

## 3. Qué no funcionó y cómo se solucionó

1. **Advertencia de runpy al invocar como módulo**: Al ejecutar `python -m fabric.simulador.red`, la importación eagerly en `fabric/simulador/__init__.py` provocaba un `RuntimeWarning` de Python (`found in sys.modules prior to execution`). Se resolvió implementando importación bajo demanda en `__getattr__` dentro de `fabric/simulador/__init__.py`.
2. **Cierre de socket en el servidor de eco para pruebas**: Durante la primera versión del fixture `servidor_eco`, el bucle de atención no cerraba explícitamente el `writer` en el bloque `finally` tras recibir EOF, lo que provocaba que `await servidor.wait_closed()` esperara al cliente remoto. Se solucionó rastreando las tareas de conexión activas y forzando el cierre ordenado de sockets en la finalización del generador asíncrono.

---

## 4. Qué quedó pendiente

No queda nada pendiente respecto a la tarea asignada para el agente A1.
El módulo `fabric/simulador/red.py` y la clase `Enlace` se encuentran listos para ser consumidos por el agente A2 en la creación de `fabric/simulador/escenario.py`.
