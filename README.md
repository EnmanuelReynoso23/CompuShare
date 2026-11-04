# CompuShare: La Computadora Compartida

Un solo modelo de lenguaje abierto, repartido por capas entre varias computadoras conectadas por internet.
Software libre de punta a punta: llama.cpp hace el cálculo y CompuShare conecta las máquinas,
elige cuáles usar y mide.

> No necesitamos centros de datos controlados por unos pocos. El poder ya está repartido en millones
> de máquinas; juntas pueden correr lo que ninguna corre sola.

Licencia MIT.

---

## Cómo funciona

```
 Tu PC (maestro)                             Laptop de un amigo (trabajador)
 llama-server --rpc ...  ── tubo ──> RELÉ <── tubo ──  ggml-rpc-server (presta su GPU o CPU)
   primeras capas + salida                              el resto de las capas
   escucha en 127.0.0.1
```

- **Relé** (`fabric/rele/rele.py`): punto de encuentro por WebSocket. Abre *tubos*: túneles TCP dentro
  del WebSocket, para que el maestro llegue al `ggml-rpc-server` de un trabajador sin abrir puertos en su router.
- **Trabajador** (`compushare unirse`): arranca `ggml-rpc-server` solo en 127.0.0.1 y lo ofrece por un tubo.
- **Maestro** (`compushare servir`): espera a los trabajadores, mide el ping de cada uno
  (`fabric/reparto.py`), elige los menos posibles y más cercanos, y arranca `llama-server --rpc` con lo que
  hace falta para que funcione por internet:
  - la tabla de salida se queda en el maestro (lo que viaja por palabra baja de 993 KB a 4,1 KB);
  - sin puntos de control ni caché de prompts (cada uno costaba una ida y vuelta por la red);
  - decodificación especulativa opcional (`--especulativa ngram-mod` o `draft-mtp`).
- **Cliente** (`compushare probar`, `compushare transcribir`): habla con `llama-server` por su protocolo
  propio (`/apply-template` y `/completion`).

Se reparte por **bloques de capas**, nunca cada capa entre dos equipos ni los expertos de un MoE entre
equipos: por internet eso multiplica las esperas de red (ver `INFORME-A11.md` y `docs/MATEMATICA.md`).

---

## Instalación

Hace falta llama.cpp (`llama-server` y `ggml-rpc-server` en el PATH, o pasar `--llama-bin`).

```bash
git clone https://github.com/EnmanuelReynoso23/CompuShare.git
cd CompuShare
uv venv
uv pip install -e .        # o: python -m pip install -e .
```

## Uso

Todos los nodos comparten una clave (`--clave`, la variable `FABRIC_CLAVE` o `~/.config/synrag/fabric.env`).

```bash
# 1. Maestro: levanta el relé, espera 2 trabajadores y reparte el modelo entre ellos
compushare servir --modelo Qwen3.5-0.8B-Q8_0.gguf --min-trabajadores 2 --espera-trabajadores 90 \
                  --especulativa ngram-mod

# 2. En cada computadora que presta su equipo
compushare unirse --rele ws://IP-DEL-MAESTRO:59095 --nombre laptop-1 --cache

# 3. Preguntar
compushare probar --prompt "En una frase: qué es una computadora compartida?"

# 4. Prueba verificable: el modelo copia un libro y cada trozo se compara letra por letra
compushare transcribir libro.txt --cuantos 3 --salida transcripcion/
```

Otros comandos: `compushare info` (equipos de esta máquina) y `compushare calcular` (predicción con las
fórmulas de `fabric/calculo.py`; es cálculo, no medición).

---

## Lo que está medido

Toda cifra de esta sección viene de una corrida real. Las predicciones de `fabric/calculo.py` están en
`docs/MATEMATICA.md` y se marcan como tales.

| Qué | Resultado | Dónde |
| --- | --- | --- |
| Qwen3.5-9B Q8_0 repartido en 2 cuadernos de Kaggle (4 GPU T4), por túnel de Cloudflare (237 ms ida y vuelta) | 6,8-6,9 tok/s; con MTP 10,3-10,8 tok/s | `fabric/kaggle/resultados/20261031-dividido` |
| Tabla de salida en el maestro | 993 KB a 4,1 KB por palabra | `fabric/reparto.py` |
| 5 trabajadores en una PC, red simulada a 50 ms de ping y 50 Mb/s, Qwen3.5-0.8B copiando un trozo de La metamorfosis | sin especulativa 1,1 tok/s; con `ngram-mod` 4,9 tok/s (91 % de aciertos); copia exacta | medición local del 4-nov-2026 |
| Lo mismo con 4 trozos a la vez | 2,77 tok/s en total: peor que 1 trozo con esta versión de llama.cpp | medición local del 4-nov-2026 |
| Programa completo (`servir` + 2 `unirse` + `transcribir`) en una PC, sin retraso de red, Qwen3.5-0.8B | 3 trozos, 99,93 % de parecido, 1 exacto (el modelo chico cambia comillas y alguna letra) | prueba de punta a punta del 4-nov-2026 |

Pendiente: la misma transcripción en Kaggle con el modelo de 9B (`TRANSCRIBIR=1 python fabric/kaggle/lanzar.py --dividido`).

---

## Estructura

```text
fabric/                 La red
  rele/                 Relé WebSocket con tubos TCP
  tubo.py               Lado trabajador del tubo
  reparto.py            Elegir trabajadores; tabla de salida en el maestro
  rpc_cache.py          Caché del trabajador armada desde su propio GGUF (los pesos no cruzan la red)
  descubrir.py          Encontrar el relé del día sin escribir URLs
  nodo/                 Nodo con modelo entero (generar, embeber, reordenar)
  calculo.py            Fórmulas que predicen tok/s y memoria
  simulador/            Proxy TCP con latencia, variación y ancho de banda; N trabajadores en una PC
  kaggle/               lanzar.py sube los cuadernos; nodo_kaggle.py es lo que corre dentro
  directo/              Prototipo de conexión directa P2P (WebRTC)
  pruebas/              Pruebas (pytest)
src/compushare/         Paquete y CLI `compushare`
  cli.py                info, unirse, servir, calcular, probar, transcribir
  nodo/                 Maestro y trabajador
  transcribir.py        Copiar un texto por la red y verificarlo letra por letra
go/                     Relé, tubo y conexión directa en Go (todavía no los usa la CLI)
docs/                   Matemática de la red, estado del arte, estado y pendientes
```

Pruebas: `python -m pytest fabric/pruebas`.

Documentación: [PLAN.md](PLAN.md), [docs/MATEMATICA.md](docs/MATEMATICA.md),
[docs/ESTADO-Y-PENDIENTES.md](docs/ESTADO-Y-PENDIENTES.md).
