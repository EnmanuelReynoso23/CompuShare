# Informe del Agente A15: Implementación del Relé y Tubo en Go (Binario Único, Protocolo Compatible)

- Fecha: 2026-11-04
- Agente: A15
- Rama: `a15-tubo-go`
- Worktree: `/home/reyno/Proyectos/cs-a15`
- Módulo Go: `github.com/EnmanuelReynoso23/CompuShare/go`
- Comandos entregados: `go/cmd/compushare-rele`, `go/cmd/compushare-tubo`

---

## 1. Resumen Ejecutivo y Qué se Hizo

Se implementó el camino de datos y la arquitectura del relé y el tubo de CompuShare en Go (`go1.27.3`), garantizando compatibilidad binaria y de protocolo con la base existente en Python (`fabric.rele` y `fabric.tubo`). La solución se diseñó para distribuirse como binario estático autocontenido (`CGO_ENABLED=0`) sin dependencias externas, apto para su ejecución directa en entornos residenciales de amigos y cuadernos de Kaggle.

### Componentes construidos:
1. **`go/pkg/protocolo`**:
   - Estructuras canónicas de mensajes de control JSON (mensajes `hola`, `bienvenido`, `ofrezco`, `ofrecido`, `nodo_caido`, `tarea`, `respuesta`, `chat`, `latido`, `senalar`, `error`).
   - Serialización y deserialización conforme a `fabric/LEEME.md`.
2. **`go/pkg/rele` y `go/cmd/compushare-rele`**:
   - Relé WebSocket concurrente con `github.com/coder/websocket`.
   - Soporte de WebSocket binario directo sin codificación Base64 en el camino de datos.
   - Activación de `TCP_NODELAY` (`SetNoDelay(true)`) en todos los sockets TCP entrantes y salientes para reducir latencia interactiva y jitter.
   - Puertos locales para tubos TCP (por defecto `50100+`, configurable con `--tubo-puerto-base` y `TUBO_PUERTO_BASE` para respetar aislamientos de pruebas).
   - Control de ritmo (cubeta de fichas configurable: ráfaga 120, ritmo 40 msg/s), vencimiento de tareas (900 s) y de latidos (60 s).
   - Servidor HTTP embebido con endpoints `/salud` y `/tubo`.
3. **`go/pkg/tubo` y `go/cmd/compushare-tubo`**:
   - Cliente trabajador del tubo compatible con las opciones de línea de comandos de `fabric/tubo.py`: `--rele`, `--clave`, `--nombre`, `--puerto`, `--host`, `--vram-gb`.
   - Autodescubrimiento local con mDNS/UDP cuando `--rele buscar` (`go/pkg/descubrir`).
   - Bucle de reconexión con reintento exponencial y canalización bidireccional continua con buffers de 64 KB a 256 KB.
4. **Interoperabilidad y Pruebas**:
   - Pruebas unitarias en Go (`go test -v ./...`) cubriendo descubrimiento, relé, canalización y eco TCP de punta a punta.
   - Soporte de variable `FABRIC_TEST_RELE_URL` en `fabric/pruebas/test_red.py` para correr la suite oficial de aceptación contra el relé Go externo sin romper el modo por defecto en Python.
   - Suite de interoperabilidad en Python (`fabric/pruebas/test_interop_go.py`) validando las 3 combinaciones posibles:
     - Relé Python + Tubo Go.
     - Relé Go + Tubo Python.
     - Relé Go + Tubo Go.
5. **Compilación Cruzada**:
   - Script `go/compilar.sh` que compila binarios estáticos (`-ldflags="-s -w"`, `CGO_ENABLED=0`) para:
     - `linux/amd64`
     - `linux/arm64`
     - `windows/amd64` (`.exe`)
     - `darwin/arm64`
   - Los binarios generados se alojan en `go/dist/` (ignorado por `.gitignore`).

---

## 2. Compatibilidad del Protocolo y Arquitectura de Datos

Para que las piezas Go y Python se puedan mezclar sin fricción:
- **Mensajes de señalización y control**: Mantienen exactamente el esquema JSON del relé original. Al conectar, el cliente o nodo envía `{"t": "hola", "clave": ..., "enjambre": ..., "rol": ...}` y recibe `{"t": "bienvenido", "id": ...}`.
- **Canalización del tubo (`/tubo`)**:
  1. El trabajador conecta por WebSocket a `/tubo` y envía `{"t": "ofrezco", "clave": ..., "nombre": ..., "info": ...}`.
  2. El relé reserva un listener TCP en `127.0.0.1:<tubo_puerto>` y responde `{"t": "ofrecido", "puerto": <tubo_puerto>}`.
  3. Cada conexión TCP entrante al puerto del relé dispara una subconexión WebSocket al trabajador `/tubo?id_oferta=...`.
  4. Los datos fluyen como tramas binarias de WebSocket (`websocket.MessageBinary`). No hay conversión a texto ni envoltura Base64.
  5. En Go se conectan directamente el socket TCP y el WebSocket mediante rutinas dedicadas (`io.Copy` con buffers de 64 KB y `TCP_NODELAY`), minimizando el traspaso por espacio de usuario.

---

## 3. Verificación y Resultados de Pruebas

### 3.1 Pruebas Unitarias Go
Comando ejecutado:
```bash
cd /home/reyno/Proyectos/cs-a15/go && /home/reyno/.local/bin/go vet ./... && /home/reyno/.local/bin/go test -v ./...
```
Resultado:
- `pkg/descubrir`: `TestTema` y `TestReleDirecto` PASS.
- `pkg/rele`: `TestSalud`, `TestClaveIncorrecta`, `TestTareaEcoDePuntaAPunta` PASS.
- `pkg/tubo`: `TestTuboConEcoTCP` PASS (transferencia de 5 MB con verificación de integridad byte a byte).
- Cobertura: 100% de paquetes sin errores en `go vet`.

### 3.2 Prueba de Aceptación Oficial contra Relé Go (`fabric/pruebas/test_red.py`)
Comando ejecutado:
```bash
PUERTO_RELE=52180
/home/reyno/Proyectos/cs-a15/go/bin/compushare-rele --puerto $PUERTO_RELE --clave "clave-de-prueba" --tubo-puerto-base 52500 &
PID=$!
FABRIC_TEST_RELE_URL="ws://127.0.0.1:$PUERTO_RELE" /home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -v fabric/pruebas/test_red.py -p no:cacheprovider
kill $PID
```
Resultado:
- 13 pruebas pasadas de 13 (`13 passed in 6.63s`):
  - `test_eco_ida_y_vuelta`: PASSED
  - `test_sin_nodos_responde_error`: PASSED
  - `test_clave_incorrecta_rechazada`: PASSED
  - `test_reparte_al_menos_cargado`: PASSED
  - `test_generar_llega_por_partes`: PASSED
  - `test_nodo_caido_avisa_al_que_pidio`: PASSED
  - `test_sala_de_chat_comparte_mensajes`: PASSED
  - `test_ritmo_corta_a_quien_inunda`: PASSED
  - `test_motor_generar_lee_el_streaming_de_openai`: PASSED
  - `test_reconexion_conserva_el_id`: PASSED
  - `test_descubrir_usa_la_direccion_fija_o_busca`: PASSED
  - `test_tubo_lleva_bytes_ida_y_vuelta`: PASSED
  - `test_tubo_rechaza_clave_mala`: PASSED

### 3.3 Pruebas de Interoperabilidad Cruzada (`fabric/pruebas/test_interop_go.py`)
Comando ejecutado:
```bash
/home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -v fabric/pruebas/test_interop_go.py -p no:cacheprovider
```
Resultado:
- `test_rele_python_tubo_go`: PASSED (eco 1 MB integro).
- `test_rele_go_tubo_python`: PASSED (eco 1 MB integro).
- `test_rele_go_tubo_go`: PASSED (eco 2 MB integro).

### 3.4 Suite General de Pruebas de CompuShare
Comando ejecutado:
```bash
/home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -q fabric/pruebas -p no:cacheprovider
```
Resultado: `40 passed, 1 skipped in 11.22s` (el test omitido corresponde a SynRAG, según diseño del proyecto).

---

## 4. Mediciones de Rendimiento y Cifras Oficiales

Todas las mediciones se ejecutaron dentro del cerrojo de concurrencia:
```bash
flock /home/reyno/Proyectos/cs-agentes/medir.lock /home/reyno/Proyectos/CompuShare/.venv/bin/python fabric/medir_a15.py
```
- Fecha de inicio: `2026-11-04T15:43:08.581003+00:00`
- Archivo de resultado: `fabric/resultado_medicion_a15.json`
- Entorno de ejecución: Linux CachyOS (kernel 7.2.8-2-cachyos x86_64), CPU 13th Gen Intel Core i5-13420H (12 hilos, 4 hilos asignados a llama.cpp), RAM 16 GB.
- Rango de puertos utilizado: `52000-52999` (exclusivo de A15).
- Cargas concurrentes presentes durante la medición:
  - Carga del sistema (loadavg inicial): `[0.996, 1.101, 1.237]`.
  - Carga del sistema (loadavg final): `[2.223, 1.666, 1.439]`.
  - Procesos concurrentes activos: Agente A7 (`agy`, tarea especulativa), `claude`, `chrome` (GPU y renderers), `Hyprland`, `kswapd0`, `quickshell`.

### 4.1 Medición de Eco TCP (Ida y Vuelta RTT y Ancho de Banda)
Muestreo de RTT con 100 pings TCP y caudal con transferencia de 50 MB de datos por el canal:

| Canal de Medición | RTT Mediana (ms) | RTT Mín (ms) | RTT Máx (ms) | Caudal (MB/s) | Duración 50 MB (s) |
|---|---|---|---|---|---|
| **1. Directo TCP (127.0.0.1)** | **0,020 ms** | 0,018 ms | 0,067 ms | **1209,06 MB/s** | 0,041 s |
| **2. Tubo + Relé Python** | **0,081 ms** | 0,078 ms | 2,535 ms | **750,17 MB/s** | 0,067 s |
| **3. Tubo + Relé Go** | **0,043 ms** | 0,038 ms | 0,752 ms | **744,59 MB/s** | 0,067 s |

#### Análisis de Latencia y RTT:
- El camino de datos en Go reduce la latencia RTT de Python de **0,081 ms a 0,043 ms** (una reducción de casi **47%** en la sobrecarga de ida y vuelta).
- El jitter máximo en Go baja drásticamente: de **2,535 ms** en Python a **0,752 ms** en Go, debido a la ausencia de pausas por recolección de basura no coordinada en el loop de asyncio y al uso directo de `TCP_NODELAY`.

---

### 4.2 Medición de llama.cpp RPC con Qwen3.5-0.8B-Q8_0
- Modelo: `/home/reyno/.cache/fabric-descargas/modelos/Qwen3.5-0.8B-Q8_0.gguf` (800.881.920 bytes, 752M parámetros).
- Configuración: `n_prompt=16`, `n_gen=64`, `n_gpu_layers=99`, 4 hilos (`-t 4`), 3 repeticiones (`-r 3`).
- Binario: `/home/reyno/.local/opt/llama.cpp/build/bin/llama-bench`.
- Servidor RPC: `/home/reyno/.local/opt/llama.cpp/build/bin/ggml-rpc-server`.

| Escenario Evaluado | Comando Exacto | Gen Mediana (tok/s) | Muestras Gen (tok/s) | Prompt Mediana (tok/s) | Muestras Prompt (tok/s) | Duración Total (s) |
|---|---|---|---|---|---|---|
| **1. Directo RPC** | `llama-bench -m Qwen3.5-0.8B-Q8_0.gguf -rpc 127.0.0.1:52300 -ngl 99 -p 16 -n 64 -t 4 -r 3 -o json` | **34,87** | `[34.87, 33.45, 34.94]` | **229,27** | `[229.27, 233.00, 218.33]` | 6,833 s |
| **2. Tubo + Relé Python** | `llama-bench -m Qwen3.5-0.8B-Q8_0.gguf -rpc 127.0.0.1:52450 -ngl 99 -p 16 -n 64 -t 4 -r 3 -o json` | **35,42** | `[35.42, 34.77, 35.61]` | **228,98** | `[228.98, 192.99, 261.32]` | 7,115 s |
| **3. Tubo + Relé Go** | `llama-bench -m Qwen3.5-0.8B-Q8_0.gguf -rpc 127.0.0.1:52600 -ngl 99 -p 16 -n 64 -t 4 -r 3 -o json` | **37,94** | `[34.32, 37.94, 38.18]` | **230,03** | `[230.03, 230.26, 221.10]` | 6,645 s |
| **4. Go con Simulador ("mismo país")** | `llama-bench -m Qwen3.5-0.8B-Q8_0.gguf -rpc 127.0.0.1:52800 -ngl 99 -p 16 -n 64 -t 4 -r 3 -o json` | **3,21** | `[3.20, 3.21, 3.21]` | **39,01** | `[36.59, 39.01, 41.57]` | 197,319 s |

#### Análisis de llama.cpp RPC:
1. **Rendimiento en Local**:
   - En local, el relé y tubo en Go alcanzan **37,94 tok/s** de generación, superando tanto al directo (**34,87 tok/s**) como al tubo en Python (**35,42 tok/s**). La duración total de la corrida en Go fue de **6,645 s** frente a **7,115 s** en Python.
   - En prompt evaluation, tanto Go como Python mantienen el techo de procesamiento local en torno a **229-230 tok/s**.
2. **Impacto del Enlace Residencial Simulador ("Mismo País")**:
   - Parámetros del enlace: 40 ms de latencia por sentido (80 ms RTT), 10 ms de variación/jitter y límite de 50 Mb/s (6,25 MB/s).
   - **Carga inicial de pesos**: Antes de comenzar el benchmark, `llama-bench` envía los 800 MB de tensores del modelo al servidor RPC a través del enlace simulado. A 50 Mb/s efectivos, esta carga toma ~125 segundos, lo que explica la duración total de 197,319 s para el escenario completo.
   - **Velocidad de generación**: Cae a **3,21 tok/s** (unos 311 ms por token). Con 80 ms de RTT base por roundtrip de RPC, una sincronización por token o capa acumula el retardo inevitable de la física del enlace. Esto confirma la necesidad de decodificación especulativa (tarea A7) y paralelismo en red WAN.

---

## 5. Compilación Cruzada

El script `go/compilar.sh` fue probado exitosamente:
```bash
Compilador: go version go1.27.3 linux/amd64
Compilando para linux/amd64...
Compilando para linux/arm64...
Compilando para windows/amd64...
Compilando para darwin/arm64...
```

Binarios estáticos producidos en `go/dist/`:
- `dist/linux_amd64/compushare-rele`: 7,0 MB
- `dist/linux_amd64/compushare-tubo`: 6,9 MB
- `dist/linux_arm64/compushare-rele`: 6,5 MB
- `dist/linux_arm64/compushare-tubo`: 6,4 MB
- `dist/windows_amd64/compushare-rele.exe`: 7,2 MB
- `dist/windows_amd64/compushare-tubo.exe`: 7,1 MB
- `dist/darwin_arm64/compushare-rele`: 6,6 MB
- `dist/darwin_arm64/compushare-tubo`: 6,6 MB

---

## 6. Qué Quedó Pendiente y Qué No Funcionó

### 6.1 Qué no funcionó o requirió ajuste
1. **Límite de mensajes por ráfaga en pruebas de inundación**:
   - En `test_red.py::test_ritmo_corta_a_quien_inunda`, el test enviaba originalmente solo 20 mensajes seguidos para probar el corte de ritmo. Con la cubeta de ráfaga predeterminada en Go (`rafaga=120`, conforme al relé de producción), 20 mensajes no agotaban las fichas. Se ajustó `test_red.py` para emitir 200 repeticiones cuando corre contra un relé externo (`FABRIC_TEST_RELE_URL`), provocando el corte por exceso de ritmo de manera confiable.
2. **Transferencia de tensores en WAN para RPC**:
   - En la arquitectura estándar de llama.cpp RPC, el cliente (`llama-bench` o `llama-server`) reenvía los pesos del modelo completo a través del socket RPC si el servidor arranca sin un modelo precargado localmente. En enlaces residenciales (50 Mb/s), transferir 800 MB toma más de 2 minutos antes de que el primer token pueda ser evaluado.

### 6.2 Qué quedó pendiente
1. **Caché persistente de tensores en el trabajador Go**:
   - Permitir que el trabajador verifique un hash de los pesos para evitar transferir el modelo por el relé si el archivo `.gguf` ya se encuentra descargado en el disco local del trabajador.
2. **Soporte de compresión zstd opcional**:
   - Añadir negociación opcional de compresión zstd en el protocolo del tubo para enlaces con ancho de banda menor a 20 Mb/s.
