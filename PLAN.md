# Plan de CompuShare (4-nov-2026)

CompuShare = varias computadoras de usuarios corriendo juntas UN modelo que no cabe en ninguna sola,
para que SynRAG pueda preguntarle a la red. Lo que ya funciona está en `~/Proyectos/synrag-fabric/fabric`;
este repo hoy es solo un esqueleto (`NodoMaestro` y `NodoTrabajador` lanzan `NotImplementedError`).

## Lo medido hasta hoy (synrag-fabric, no estimaciones)

| Qué | Cifra | Fuente |
|---|---|---|
| Un modelo dividido en 2 cuadernos de Kaggle (4 GPU), generación | 10,3-10,8 tok/s | `fabric/kaggle/resultados/20261101-2040` |
| Lo mismo preguntando por la red (relé) | 6,8-6,9 tok/s | `fabric/kaggle/resultados/20261031-dividido` |
| Ida y vuelta por el túnel de Cloudflare | 237 ms (variación 40 ms) | `fabric/PRUEBA-10.md` |
| Costo del relé en Python (local, CPU) | 7,1 -> 4,1 tok/s | `fabric/LEEME.md` |
| Carga con caché de pesos en el trabajador | 7,4 s (96,6 s con compresión) | `fabric/LEEME.md` |

## Qué está mal en lo que propuso Gemini

1. **"100 mini-IAs, cada una de un tema, 3 o 4 saltos".** En un modelo MoE los expertos no son temas: hay un
   grupo de expertos EN CADA CAPA y el enrutador elige 2-8 por cada token en cada capa. Repartir expertos
   entre computadoras haría un salto por la red por cada capa y por cada token (decenas de saltos por
   palabra), no 3-4. Lo que sí sirve de MoE: que cada nodo tenga todos los expertos de sus capas
   (como hoy) o dejar los expertos en RAM/CPU del mismo nodo (`--override-tensor exps=CPU` de llama.cpp).
2. **Dividir en N computadoras no hace más rápida UNA respuesta.** Cada palabra cruza las N en fila:
   tiempo por palabra ~ cálculo + N x ida y vuelta. Con 237 ms y 10 nodos son ~2,4 s por palabra como
   mínimo. La ganancia real está en atender muchas respuestas a la vez (flujos en paralelo), no en una.
3. **Decodificación especulativa.** Ayuda (valida varias palabras por viaje), pero lo normal es 1,5-3 veces,
   según cuántas palabras acierte el adivinador; no es 10 veces. Hay que medir la tasa de acierto.
4. **Privacidad.** Los estados intermedios que viajan entre nodos dejan reconstruir la entrada. Con nodos
   de desconocidos no se puede prometer privacidad; solo con nodos de confianza o cifrado de punta a punta
   entre los que se conocen.
5. **Nodos tramposos.** Un nodo puede devolver basura. Hace falta verificación (repetir al azar una parte
   en otro nodo) antes de abrirlo al público.

## Decisiones que necesito del usuario

- D1. ¿CompuShare absorbe el código de `synrag-fabric/fabric` (un solo repo) o queda como CLI que lo usa?
  Recomendado: absorberlo, con historial (`git subtree`), y dejar synrag-fabric archivado.
- D2. ¿Primero red de confianza (tus máquinas, Kaggle, amigos) o pública? Recomendado: confianza.
- D3. ¿Modelo objetivo? Recomendado: Qwen3.8-27B UD-Q4_K_M (16,5 GB), el de `PRUEBA-10.md`.

## Fases

| Fase | Meta | Se da por hecha cuando |
|---|---|---|
| F0 | Repo ordenado: código de fabric dentro, README sin emojis y sin "60 %+" (cifra real: -63,9 %, CLAIMS fila 41 de SynRAG) | pruebas en verde, README revisado |
| F1 | Simulador de N computadoras con latencia real (`tc netem`) en esta laptop | tabla tok/s para N = 2, 4, 6, 10 y 3 latencias |
| F2 | Relé más rápido (hoy cuesta ~40 %) | tubo local >= 6,5 tok/s frente a 7,1 directo |
| F3 | Varios flujos a la vez (`--parallel N`) | tok/s totales con 1, 4 y 8 flujos |
| F4 | Decodificación especulativa en el maestro | tasa de acierto y tok/s medidos, con y sin |
| F5 | `compushare unirse` y `servir` funcionando de verdad (envuelven `fabric/nodo`) | 2 máquinas reales se unen con un comando |
| F6 | Verificación de nodos | un nodo que devuelve basura se detecta en la prueba |
| F7 | SynRAG pregunta a la red (`preguntar_red`) desde el MCP | respuesta con fuentes por la red real |
| F8 | MoE: solo investigar y medir qué permite llama.cpp | documento con cifras, sin prometer nada |

Todo número que se publique va a un `CLAIMS.md` de CompuShare con fecha, comando y archivo de resultado.

## Agentes de Antigravity (13)

Cada uno trabaja en su propio worktree (`~/Proyectos/cs-a<N>`), rama `a<N>-<tema>`, sin push, sin emojis,
commits sin coautor de IA, NADA EN ORACLE, nunca publicar claves. Yo los lanzo en segundo plano con
`agy -p`, reviso su diff, corro sus pruebas y solo junto lo que pase.

| Agente | Tarea | Entrega | Depende de |
|---|---|---|---|
| A1 | `fabric/simulador/red.py`: proxy con latencia, variación, ancho de banda y pérdida por enlace | módulo + pruebas | - |
| A2 | `fabric/simulador/escenario.py`: levanta N `rpc-server` locales y arma la cadena `--rpc` | script + medición N=2 | A1 |
| A3 | `fabric/simulador/cuento.py`: carga de trabajo (25 capítulos) y cifras por escenario | script + INFORME.md | A2 |
| A4 | Perfilar el relé (`rele.py`, `tubo.py`): dónde se pierden los 3 tok/s | informe con perfil (py-spy) | - |
| A5 | Arreglar el cuello de botella que encuentre A4 | parche + medición antes/después | A4 |
| A6 | Medir flujos en paralelo (`--parallel`) en el simulador | tabla 1/4/8 flujos | A2 |
| A7 | Decodificación especulativa (`-md` modelo borrador y MTP) en el maestro | tasa de acierto + tok/s | A2 |
| A8 | CLI de CompuShare: `unirse`/`servir`/`info` sobre `fabric/nodo` | CLI + pruebas | D1 |
| A9 | Conexión entre casas: túnel actual vs conexión directa (perforar NAT) | informe + prototipo | - |
| A10 | Verificación de nodos: repetir al azar una capa en otro nodo y comparar | diseño + prototipo | A2 |
| A11 | MoE en llama.cpp: `--override-tensor`, reparto por capas, qué se puede y qué no | informe con cifras | - |
| A12 | README, LEEME y `CLAIMS.md` de CompuShare con solo cifras medidas | documentos | A3, A6, A7 |
| A13 | Pruebas y CI (pytest + ruff) para CompuShare | workflow en verde | A8 |

Primera tanda (sin dependencias): A1, A4, A9, A11. Al terminar A1: A2. Al terminar A2: A3, A6, A7, A10.
