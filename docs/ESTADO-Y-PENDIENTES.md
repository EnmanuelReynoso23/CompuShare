# Computadora compartida (CompuShare): estado y pendientes

## Actualización del 4-nov-2026 (noche)

Medido en local (Qwen3.5-0.8B, llama.cpp 11193) y en los registros de Kaggle del 4-nov:

- **El patrón de la tabla de salida estaba mal.** `-ot (token_embd|output)\.weight=...` no tenía anclas y
  también atrapaba `blk.N.attn_output.weight` de cada capa de atención completa. Esas proyecciones quedaban en
  el maestro y cada token rebotaba maestro-trabajador una vez más por cada una. Con un proxy que lee el
  protocolo RPC, por enlace y por token: antes 707 KB y 4 idas y vueltas; con `^(token_embd|output)\.weight$`
  13,9 KB y 1, y llama.cpp reutiliza el grafo (comando 16) en vez de mandarlo entero. Con 5 trabajadores a
  50 ms simulados, mismo trozo y misma salida: sin especulativa de 1,10 a 2,54 tok/s; con `ngram-mod` de
  3,08 a 4,67 tok/s. **Toda medición anterior hecha con el patrón sin anclas quedó afectada** (entre ellas las
  de A2 y A7 de esta tabla).
- **El "ping" informado no era el ping.** `medir_rtt` abría una conexión nueva por medición: medía abrir el tubo
  por el relé más el saludo HELLO (664 ms en Kaggle el 4-nov, 237 ms el 31-oct). Ahora `medir_enlace` da
  `conexion_ms` y `rtt_ms` (DEVICE_COUNT sobre la conexión abierta) por separado.
- **La caída del trozo 43 en Kaggle no fue del trabajador.** Su `ggml-rpc-server` siguió vivo hasta el final
  (`rpc_server_vivo: true`). A las 00:32:33 UTC se cortaron las conexiones WebSocket por el túnel de Cloudflare
  sin cuadro de cierre; el tubo se reconectó al segundo, pero el maestro no sabía recuperarse y marcó como
  fallados los 50 trozos restantes.
- **Recuperación agregada.** El trabajador relanza `ggml-rpc-server` si se cae; el maestro relanza `llama-server`
  con los tubos vivos (`--reinicios`); `transcribir` espera, repite el trozo y no da por fallados los que faltan
  (`--retomar` salta lo ya hecho). Prueba local matando el `ggml-rpc-server` de un trabajador a mitad: el
  trabajador lo relanzó en 2 s, el maestro tuvo `llama-server` listo 4 s después y salieron 6 de 6 trozos sin errores.

- **Kaggle con los arreglos (4-nov, 21:35).** Qwen3.5-9B en 2 cuadernos: La metamorfosis entera, 93 de 93 trozos
  en 27,4 min, 0 errores, 90 exactos (los trozos 1, 3 y 35 cambian comillas tipográficas por rectas), 18,99 tok/s
  de generación (antes 6,46), 233,2 tok/s de lectura (antes 47,4), 89,7 % de aciertos. Ida y vuelta por el tubo
  abierto 37,1 ms; abrir el tubo 667,1 ms. No hubo cortes, así que la recuperación no se ejercitó en Kaggle.
  Métricas en `fabric/kaggle/resultados/20261104-metamorfosis`.

Pendiente: el ancho de banda con especulativa (18.087 MB en la transcripción, unos 633 KB por token generado,
11,0 MB/s). En local se vio que llama.cpp lee el estado recurrente entero de cada capa del trabajador al menos una
vez por petición y vuelve a mandar el grafo cuando cambia el tamaño del lote.

## Resumen

CompuShare junta varias computadoras para correr UN modelo que no cabe en ninguna sola, para que SynRAG le pregunte a la red. Al 4-nov el plan (fases F0-F9) está escrito, 11 de 16 tareas de agentes están entregadas y la meta inmediata es juntar todo en `main` y hacer la prueba final en Kaggle (F9).

- Decisiones tomadas el 4-nov: un solo repo (fabric absorbido), red de confianza primero, modelo objetivo Qwen3.8-27B UD-Q4_K_M (16,5 GB), Kaggle permitido.
- Hallazgo más fuerte del día: dejar la tabla de salida en el maestro baja de 993 KB a 4,1 KB por palabra (64 veces menos).
- Lo que no funciona como lo propuso Gemini: repartir expertos MoE por temas, esperar que más nodos aceleren UNA respuesta, y prometer 10x con especulativa (se midió 1,18x en red de amigos).
- El código de `main` va 33 commits por delante de GitHub: nada de lo de hoy estaba subido.

---

## Dónde está todo

Todo vive en la laptop (CachyOS); el repo público [EnmanuelReynoso23/CompuShare](https://github.com/EnmanuelReynoso23/CompuShare) contiene ahora todas las ramas y commits sincronizados.

| Qué | Ruta | Nota |
| --- | --- | --- |
| Repo principal | `~/Proyectos/CompuShare` | rama `main`; `PLAN.md` con fases F0-F9 y tabla de agentes A1-A13 |
| Plan y fases | `~/Proyectos/CompuShare/PLAN.md` | F0-F9 y tabla de agentes A1-A13 |
| Matemática calibrada | `~/Proyectos/CompuShare/docs/MATEMATICA.md` | modelo contra Kaggle: 156 ms calculados vs 147-150 medidos |
| Estado del arte | `~/Proyectos/CompuShare/docs/ESTADO-DEL-ARTE.md` | Petals, exo, prima.cpp, dllama, ggml-rpc |
| Código de red (ex synrag-fabric) | `~/Proyectos/CompuShare/fabric/` | traído con `git subtree`, historial completo |
| CLI | `~/Proyectos/CompuShare/src/compushare` | esqueleto en `main`; versión real en rama `a8-cli` |
| Worktrees de agentes | `~/Proyectos/cs-a1` ... `~/Proyectos/cs-a16` | uno por agente, rama `a<N>-<tema>` |
| Tareas, reglas y logs | `~/Proyectos/cs-agentes` | `REGLAS.md`, `a<N>.md`, `a<N>.log`, `cola.sh` |
| llama.cpp con RPC (CPU) | `~/.local/opt/llama.cpp/build/bin` | `rpc-server`, `llama-server` |
| Entorno Python | `~/Proyectos/CompuShare/.venv` | creado con `uv` |
| Registros de `llama-server` | `~/.cache/compushare/registros` | los escribe la CLI de A8 |

### Ramas y su estado

| Rama | Agente | En `main` |
| --- | --- | --- |
| `a1-simulador-red` | A1 simulador de red | sí |
| `a4-perfil-rele` | A4 perfil del relé | sí |
| `a9-conexion-casas` | A9 conexión directa (aiortc) | sí |
| `a10-verificacion` | A10 (tabla de salida) | sí |
| `a11-moe` | A11 MoE | sí |
| `a14-estado-del-arte` | A14 estado del arte | sí |
| `a2-escenario` | A2 escenarios N nodos | no (1 commit) |
| `a7-especulativa` | A7 especulativa MTP | no (1 commit) |
| `a15-tubo-go` | A15 relé y tubo en Go | no (1 commit) |
| `a16-directo-go` | A16 directo P2P en Go | no (1 commit) |
| `a8-cli` | A8 CLI real | no (commit WIP `7ffc9cb`) |

---

## Lo hecho y lo medido

Todas las cifras son medidas (no estimadas); cada una sale del informe `INFORME-A<N>.md` de su worktree.

| Fecha | Trabajo | Resultado clave |
| --- | --- | --- |
| 4-nov | A7 especulativa MTP | mejor k = 4: 410,5 ms por palabra (2,44 tok/s), 1,18x frente a sin especulativa; mover MTP al maestro con `-ot` lo empeora (946,7 ms) |
| 4-nov | A15 relé y tubo en Go | ida y vuelta 0,081 ms (Python) a 0,043 ms (Go); 37,94 tok/s en local frente a 35,42 con el tubo Python |
| 4-nov | A2 escenarios N nodos | N=2: 27,8 tok/s sin retraso, 5,66 en misma ciudad (20 ms), 2,31 en mismo país (80 ms); 1098,5 KB por palabra |
| 4-nov | A16 directo P2P en Go | ICE + QUIC, clasificador de NAT, modo tubo `compushare-directo` |
| 4-nov | Tabla de salida en el maestro | 993 KB a 4,1 KB por palabra (64x); unos 800 ms menos por palabra con 10 Mb/s; ya en el nodo de Kaggle |
| 4-nov | A4 perfil del relé | el tubo pierde 24,7 % (28,33 a 21,33 tok/s); causa: 8-14 mensajes de ida y vuelta por palabra en RPC |
| 4-nov | A11 MoE (OLMoE-1B-7B) | repartir por expertos = un salto por capa: 0,26 tok/s en OLMoE con 237 ms; local 20,25 tok/s |
| 4-nov | A14 estado del arte | fórmula de pipelining calibrada contra Kaggle, Petals, prima.cpp y dllama |
| 4-nov | A9 conexión directa (aiortc + STUN) | prototipo WebRTC con caída al relé |
| 4-nov | A1 simulador de red | `fabric/simulador/red.py`: latencia, variación, ancho de banda y pérdida por enlace, sin root |
| 4-nov | A8 CLI (WIP) | 55 pruebas en verde; prueba de punta a punta con trabajador: 26,3 tok/s con Qwen3.5-0.8B |
| 31-oct | Kaggle, Qwen3.5-9B Q8_0 en 2 cuadernos | 147-150 ms por palabra; 10,3-10,8 tok/s con MTP; 6,8-6,9 tok/s por el relé |
| 31-oct | Túnel de Cloudflare | 237 ms de ida y vuelta (una medición) |

---

## Arquitectura y decisiones

El modelo se reparte por capas con llama.cpp RPC: cada palabra recorre los nodos en fila, así que el tiempo por palabra es cálculo + N x ida y vuelta. La ganancia está en atender muchas respuestas a la vez, no en acelerar una.

Camino de una palabra:

1. El maestro corre `llama-server` con `--rpc` y se queda con la tabla de salida (`fabric.reparto.salida_en_el_maestro()`).
2. La activación sale por el tubo (`fabric/tubo.py`, o `compushare-tubo` en Go).
3. Cruza por el relé WebSocket (`fabric/rele/rele.py` tras un túnel `trycloudflare`) o por conexión directa (`compushare-directo`, ICE + QUIC; caída al relé si falla).
4. En cada trabajador, `rpc-server` calcula sus capas y devuelve el resultado al siguiente nodo.

### Prioridades según `docs/MATEMATICA.md`

Entre amigos (27B Q4, 40 ms, 10 Mb/s de subida): 2,8 palabras/s hoy; 7,2 si solo viaja la activación; unas 20 con especulativa sin viajes extra.

1. Bajar bytes por palabra (siguiente: activación en f16 y reutilizar el grafo).
2. Especulativa sin viajes extra.
3. Varios flujos a la vez (`--parallel N`).
4. Elegir los nodos con `fabric/calculo.elegir_optimo`.
5. MoE repartido por capas, nunca por expertos.

### Decisiones tomadas (4-nov)

| Decisión | Elegido |
| --- | --- |
| D1. Repos | Un solo repo: fabric absorbido con historial; synrag-fabric queda archivado |
| D2. Red | Primero de confianza: tus máquinas, Kaggle y amigos |
| D3. Modelo | Qwen3.8-27B UD-Q4_K_M (16,5 GB) |
| Kaggle | Permitido para pruebas |
| Privacidad | No se promete con nodos desconocidos: los estados intermedios dejan reconstruir la entrada |
| Números públicos | Solo en `CLAIMS.md` con fecha, comando y archivo de resultado |

---

## Pendientes exactos y decisiones abiertas

### Tareas en curso y agentes pendientes
1. **A8 (`compushare` CLI)**:
   - Rama: `a8-cli` con commit WIP `7ffc9cb`. 55 pruebas en verde.
   - Resuelto: Fuga de memoria en pruebas (no más OOM por 16,5 GB en RAM), redirección de salida de `llama-server` a `~/.cache/compushare/registros` para evitar bloqueo de buffers de tuberías, `SIGTERM` y `PR_SET_PDEATHSIG` para prevenir hijos huérfanos.
   - Pendiente: Confirmar apagado limpio (`SIGTERM`/`SIGKILL`), repetir y registrar medición punta a punta con trabajador (había dado 26,3 tok/s con Qwen3.5-0.8B) y escribir `INFORME-A8.md`.
2. **A10 (Verificación de nodos)**:
   - Pendiente de implementación tras interrupción previa por OOM.
   - Objetivo: Verificación probabilística muestreando capas al azar en otro nodo para detectar manipulaciones o fallas.
3. **Integración a `main`**:
   - Ramas terminadas listas para juntar: `a2-escenario`, `a7-especulativa`, `a15-tubo-go`, `a16-directo-go`.
   - Rama `a8-cli` tras validación final.
4. **Fase F9 (Prueba de escala en Kaggle)**:
   - Qwen3.8-27B dividido entre cuadernos (2x T4).
   - Inyección de perfiles de red del simulador A1 (misma ciudad 20 ms, mismo país 80 ms, intercontinental, Cloudflare).
   - Tabla comparativa de tok/s reales frente a la proyección de `docs/MATEMATICA.md`.

### Decisiones técnicas abiertas
- **Transporte por defecto**: Determinar si el tubo en Go (A15, 0,043 ms RTT vs 0,081 ms Python; 37,94 vs 35,42 tok/s) sustituye al tubo Python como opción principal en el empaquetado.
- **Estrategia P2P**: Criterio de fallback automático de ICE + QUIC (`compushare-directo`) al relé WebSocket cuando ambos extremos estén tras NAT simétrico.
- **Distribución de binarios**: Estrategia de descarga o compilación de binarios Go para entornos efímeros (Kaggle, Colab) sin Go instalado.

---

## Cómo retomarlo en otra máquina

### 1. Clonar el repositorio y ramas
```bash
git clone https://github.com/EnmanuelReynoso23/CompuShare.git
cd CompuShare
git fetch origin --all
```

### 2. Entorno Python y dependencias
```bash
# Crear entorno virtual con uv o python venv
uv venv .venv
source .venv/bin/activate

# Instalar dependencias base y de desarrollo
uv pip install -e ".[dev]"

# Opcional: soporte para conexión directa P2P (WebRTC)
uv pip install -e ".[directo]"
```

### 3. Binarios nativos necesarios
- **llama.cpp con RPC**:
  ```bash
  git clone https://github.com/ggerganov/llama.cpp
  cd llama.cpp
  cmake -B build -DGGML_RPC=ON -DGGML_CUDA=OFF  # usar CUDA/ROCm si hay GPU
  cmake --build build --config Release -j$(nproc)
  # Asegurar que rpc-server y llama-server estén en el PATH
  ```
- **Binarios en Go (opcional, para máxima velocidad de tubo y P2P)**:
  ```bash
  cd fabric/go
  go build -o ../../bin/compushare-tubo ./cmd/tubo
  go build -o ../../bin/compushare-directo ./cmd/directo
  ```

### 4. Ejecutar pruebas unitarias
```bash
pytest fabric/pruebas/
```

### 5. Levantar prueba básica dividida
```bash
# Terminal 1: Iniciar servidor RPC en el trabajador
rpc-server -H 0.0.0.0 -p 50052

# Terminal 2: Iniciar servidor maestro con capas divididas
llama-server -m /ruta/al/modelo.gguf --rpc 127.0.0.1:50052 -p 8080 --host 0.0.0.0
```

### 6. Simular escenarios de red entre casas
```bash
# Ejemplo: 2 nodos simulando latencia de la misma ciudad (20 ms)
python -m fabric.simulador.escenario --nodos 2 --perfil misma_ciudad
```
