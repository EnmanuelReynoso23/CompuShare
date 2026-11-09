# CompuShare: The Shared Computer

One open language model, split by layers across several computers connected over the internet.
Free software end to end: llama.cpp does the computing, and CompuShare connects the machines,
chooses which ones to use and measures the result.

Español: [README.es.md](README.es.md)

> We do not need data centers controlled by a few. The power is already spread across millions
> of machines; together they can run what none of them can run alone.

MIT License.

---

## How it works

```
 Your PC (master)                              A friend's laptop (worker)
 llama-server --rpc ...  ── tunnel ──> RELAY <── tunnel ──  ggml-rpc-server (lends its GPU or CPU)
   first layers + output                                      the remaining layers
   listens on 127.0.0.1
```

- **Relay** (`fabric/rele/rele.py`): a WebSocket meeting point. It opens *tunnels*: TCP connections
  inside the WebSocket, so the master can reach a worker's `ggml-rpc-server` without opening ports on
  the worker's router.
- **Worker** (`compushare unirse`): starts `ggml-rpc-server` on 127.0.0.1 only and offers it through a tunnel.
- **Master** (`compushare servir`): waits for workers, measures each one's ping (`fabric/reparto.py`),
  picks the fewest and closest ones, and starts `llama-server --rpc` with what it needs to work over
  the internet:
  - the output table stays on the master (traffic per token drops from 993 KB to 4.1 KB), with an
    anchored pattern so it does not drag along the output projection of every attention layer;
  - no checkpoints or prompt cache (each one cost a network round trip);
  - optional speculative decoding (`--especulativa ngram-mod` or `draft-mtp`).
- **Client** (`compushare probar`, `compushare transcribir`): talks to `llama-server` through its own
  protocol (`/apply-template` and `/completion`).
- **Failures**: if a worker's `ggml-rpc-server` crashes, the worker restarts it on the same port. If
  `llama-server` crashes, the master chooses again among the live tunnels and restarts it (`--reinicios`).
  `transcribir` waits for it to come back, repeats the chunk, and with `--retomar` skips what is already done.

Work is split by **blocks of layers**, never one layer across two machines or the experts of a MoE across
machines: over the internet that multiplies the network waits (see `INFORME-A11.md` and `docs/MATEMATICA.md`).

---

## Installation

You need llama.cpp (`llama-server` and `ggml-rpc-server` on the PATH, or pass `--llama-bin`).

```bash
git clone https://github.com/EnmanuelReynoso23/CompuShare.git
cd CompuShare
uv venv
uv pip install -e .        # or: python -m pip install -e .
```

## Usage

All nodes share a key (`--clave`, the `FABRIC_CLAVE` variable or `~/.config/synrag/fabric.env`).
The commands are in Spanish: `servir` is serve, `unirse` is join, `probar` is try and `transcribir` is transcribe.

```bash
# 1. Master: starts the relay, waits for 2 workers and splits the model between them
compushare servir --modelo Qwen3.5-0.8B-Q8_0.gguf --min-trabajadores 2 --espera-trabajadores 90 \
                  --especulativa ngram-mod

# 2. On each computer lending its hardware
compushare unirse --rele ws://MASTER-IP:59095 --nombre laptop-1 --cache

# 3. Ask something
compushare probar --prompt "In one sentence: what is a shared computer?"

# 4. Verifiable test: the model copies a book and each chunk is compared character by character
compushare transcribir libro.txt --cuantos 3 --salida transcripcion/
```

Other commands: `compushare info` (hardware on this machine) and `compushare calcular` (a prediction using
the formulas in `fabric/calculo.py`; it is a calculation, not a measurement).

---

## What has been measured

Every number in this section comes from a real run. The predictions from `fabric/calculo.py` are in
`docs/MATEMATICA.md` and are marked as predictions.

| What | Result | Where |
| --- | --- | --- |
| Qwen3.5-9B Q8_0 split across 2 Kaggle notebooks (4 T4 GPUs), through a Cloudflare tunnel (237 ms round trip) | 6.8-6.9 tok/s; with MTP 10.3-10.8 tok/s | `fabric/kaggle/resultados/20261031-dividido` |
| Output table on the master | 993 KB to 4.1 KB per token | `fabric/reparto.py` |
| 5 workers on one PC, simulated network at 50 ms ping and 50 Mb/s, Qwen3.5-0.8B copying chunk 1 of The Metamorphosis, **before** anchoring the output table pattern | no speculation 1.10 tok/s; with `ngram-mod` 3.08 tok/s | local measurement, Nov 4, 2026 |
| The same **after** anchoring it (`^(token_embd\|output)\.weight$`) | no speculation 2.54 tok/s; with `ngram-mod` 4.67 tok/s (76% hit rate); same output (99.92%) | local measurement, Nov 4, 2026 |
| What travels per link and per token (no speculation, 2 workers, a proxy that reads the RPC protocol) | before 707 KB and 4 round trips; after 13.9 KB and 1 | local measurement, Nov 4, 2026 |
| 4 chunks at once (before the fix) | 2.77 tok/s in total: worse than 1 chunk with this version of llama.cpp | local measurement, Nov 4, 2026 |
| A worker failing halfway through `transcribir` (its `ggml-rpc-server` is killed) | the worker restarts it in 2 s, the master restarts `llama-server` in 4 s; 6 of 6 chunks, 0 errors | local test, Nov 4, 2026 |
| Qwen3.5-9B on 2 Kaggle notebooks copying The Metamorphosis (before the fixes) | 42 of 93 chunks, 39 exact, 6.46 tok/s, 88.8% hit rate; the tunnel cut the WebSockets at chunk 43 and the master did not recover | `fabric/kaggle/resultados/20261104-metamorfosis-antes` |
| The same **after** the fixes (4 T4 GPUs, `ngram-mod,draft-mtp`) | **the whole book: 93 of 93 chunks in 27.4 min**, 0 errors, 90 exact (the other 3 change quotation marks), 18.99 tok/s generation, 233.2 tok/s reading, 89.7% hit rate; round trip 37.1 ms (opening the tunnel 667.1 ms); 11.0 MB/s through the tunnel | `fabric/kaggle/resultados/20261104-metamorfosis` |
| Full program (`servir` + 2 `unirse` + `transcribir`) on one PC, no network delay, Qwen3.5-0.8B | 3 chunks, 99.93% similarity, 1 exact (the small model changes quotation marks and the odd letter) | end-to-end test, Nov 4, 2026 |

The Kaggle "ping" reported earlier (237 ms on Oct 31, 664 ms on Nov 4) measured opening a tunnel plus the
HELLO handshake, not the round trip over an open connection. Since Nov 4 the two are measured separately
(`fabric/reparto.py: medir_enlace`).

Still open: with speculation about 633 KB travel per generated token (11.0 MB/s on Kaggle), too much for many
home connections. Failure recovery is tested locally, but there were no outages during the Kaggle run.

---

## Structure

```text
fabric/                 The network
  rele/                 WebSocket relay with TCP tunnels
  tubo.py               Worker side of the tunnel
  reparto.py            Choosing workers; output table on the master
  rpc_cache.py          Worker cache built from its own GGUF (weights do not cross the network)
  descubrir.py          Finding the day's relay without typing URLs
  nodo/                 Node with a whole model (generate, embed, rerank)
  calculo.py            Formulas that predict tok/s and memory
  simulador/            TCP proxy with latency, jitter and bandwidth; N workers on one PC
  kaggle/               lanzar.py uploads the notebooks; nodo_kaggle.py is what runs inside
  directo/              Prototype of a direct P2P connection (WebRTC)
  pruebas/              Tests (pytest)
src/compushare/         Package and `compushare` CLI
  cli.py                info, unirse, servir, calcular, probar, transcribir
  nodo/                 Master and worker
  transcribir.py        Copying a text over the network and verifying it character by character
go/                     Relay, tunnel and direct connection in Go (not used by the CLI yet)
docs/                   Network math, state of the art, status and open items
```

Tests: `python -m pytest fabric/pruebas`.

Documentation (in Spanish): [PLAN.md](PLAN.md), [docs/MATEMATICA.md](docs/MATEMATICA.md),
[docs/ESTADO-Y-PENDIENTES.md](docs/ESTADO-Y-PENDIENTES.md).
