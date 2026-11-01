# Plan: red de IA completa y funcional (31-oct-2026)

## Dónde estamos (medido, sin adornos)

| Qué | Estado |
| --- | --- |
| Repartir preguntas entre equipos (cada uno con su modelo completo) | FUNCIONA (probado con el relé en Oracle; 31-oct se sacó de Oracle). 2 cuadernos Kaggle (Qwen3.8-27B y Qwen3.6-35B-A3B) respondiendo de verdad. |
| Dónde vive cada cosa | Web en Vercel. Relé dentro del cuaderno Kaggle `calidad`, con túnel rápido de Cloudflare; su dirección se publica en ntfy.sh bajo un tema que sale de la clave. NADA en Oracle (decisión del usuario, 31-oct). |
| Dividir UN modelo entre 2 computadoras | NO EXISTE TODAVÍA. El "ring pipeline" de p2p-neural-fabric era simulado (tensores de `Math.sin`) y se borró. |
| Búsqueda con embeddings en Kaggle | Falló por memoria en la GPU 0 (comparte con llama.cpp). Arreglado en el código; falta repetirla. |
| Velocidad en Kaggle | Sospechosa: 27B a 5,1 tok/s con primer token a 115 s; 35B-A3B a 9 tok/s con primer token a 69 s. En 2 x T4 se esperaría bastante más. Hay que leer `llama-server.log` antes de seguir. |
| Navegadores | Pueden donar un modelo pequeño completo (WebLLM). No pueden aportar "media IA": WebLLM no reparte capas. |

## Cómo se divide una IA de verdad

Un modelo es una pila de capas. Con **llama.cpp RPC** (`ggml-rpc-server` + `llama-server --rpc`) la
máquina A guarda las capas 1-N y la máquina B las capas N+1-final. Por cada token, A calcula su mitad,
manda a B el estado intermedio (unos KB) y B termina. Así un modelo que no cabe en una sola máquina
corre entre las dos.

El costo es la latencia: cada token necesita varias idas y vueltas por la red. En la misma máquina
o en LAN es casi gratis; por internet puede bajar a pocos tokens por segundo. Eso se mide, no se supone.

Tres límites que mandan en el diseño:

1. **El servidor RPC no tiene clave ni cifrado** (su README: no correrlo nunca en una red abierta).
   Solo puede escuchar en `127.0.0.1` y viajar por un túnel con autenticación.
2. **Kaggle no acepta conexiones entrantes.** Dos cuadernos se hablan por el relé, que ya vive en el
   cuaderno A y sale por el túnel de Cloudflare: el trabajador B entra con la clave y el relé hace de
   tubo TCP hacia el `llama-server` de A. Sin Oracle y sin servicios nuevos.
3. **Los pesos pesan.** La primera carga mandaría la mitad remota del modelo (decenas de GB) por el
   túnel. El trabajador baja el GGUF de Hugging Face por su cuenta y se le arma la caché de
   `ggml-rpc-server -c` desde ese archivo, para que por el túnel solo viajen los estados de cada token.
   Si eso no se puede, se mide cuánto tarda la transferencia antes de seguir.

## Fases

Cada fase termina con una medición escrita en `kaggle/resultados/<fecha>/INFORME.md`. Si una fase
no da, se dice y se decide antes de seguir.

### F0. Cerrar lo que ya existe (1 sesión)

- Leer `llama-server.log` de los dos nodos (`lanzar.py --bajar`) y corregir la velocidad. Sospechas:
  capas en CPU, calentamiento contado como primer token, las dos pruebas compitiendo por la GPU.
- Repetir la búsqueda con lotes de 8 (`PRUEBAS=busqueda`) para tener los aciertos en los puestos 1 y 5.
- Repo PRIVADO en GitHub (`synrag-fabric`) y subir la rama `red-fabric`.
- Rotar la clave del relé: HECHO 31-oct (clave nueva en `~/.config/synrag/fabric.env`).
- Borrar los 6 cuadernos del 90 %, si lo confirmas.

### F1. Dividir dentro de un solo cuaderno (prueba del mecanismo, sin red)

- En un cuaderno con 2 x T4: `ggml-rpc-server` en la GPU 1 (`CUDA_VISIBLE_DEVICES=1`, `-H 127.0.0.1`)
  y `llama-server --rpc 127.0.0.1:50052` en la GPU 0.
- Verificar que el binario CUDA ya compilado trae `ggml-rpc-server`; si no, compilar con `-DGGML_RPC=ON`.
- Medir contra el reparto normal (`--split-mode layer` en las dos GPU locales). La diferencia es el
  costo propio del RPC.
- Éxito: misma respuesta y una caída de velocidad medida y pequeña.

### F2. Tubo TCP por el relé (sin Oracle)

- Mensajes nuevos del relé: `tubo_abrir` / marcos binarios / `tubo_cerrar`, solo entre pares del mismo
  enjambre y con la clave. En el cuaderno A el relé escucha en `127.0.0.1:50052` y pasa los bytes al
  trabajador B, que los entrega a su `ggml-rpc-server` en `127.0.0.1`. Nada RPC queda público.
- Pruebas automáticas: tubo de punta a punta en la misma máquina (eco TCP y un RPC de verdad con un
  modelo diminuto).
- Medir la ida y vuelta Kaggle -> Cloudflare -> Kaggle y el ancho de banda real del túnel.
- Caché del trabajador armada desde su propio GGUF (verificar el formato de la caché de llama.cpp).

### F3. Una IA en dos computadoras (el objetivo)

- Cuaderno A (maestro, 2 x T4) + cuaderno B (trabajador, 2 x T4 con un `ggml-rpc-server` por GPU):
  4 x T4 = 60 GB, dos máquinas físicas distintas.
- Modelo que NO quepa en una sola (30 GB): GGUF de ~40-50 GB. Se elige el día de la prueba,
  verificado en Hugging Face (tamaño real del archivo).
- `--tensor-split` según la VRAM libre de cada GPU; caché `-c` en el trabajador.
- Éxito: el modelo carga, responde bien a la prueba de revisión de código y se miden tok/s y primer
  token. Se publican tal cual, aunque sean lentos.

### F4. Meterlo en la red

- El maestro entra al relé como un nodo `generar` más, con `info.equipos=2` (y los nombres). La web,
  el chat y `preguntar_red` lo usan sin cambios.
- Coordinación por el relé: capacidad nueva `capas` para los trabajadores; el maestro pide "dame un
  trabajador con X GB libres" y el relé le pasa el puerto del túnel.
- `lanzar.py --dividido MODELO` levanta maestro y trabajadores juntos.
- La web dibuja el enlace maestro-trabajador en la vista Red.

### F5. Más computadoras

- Tu PC (CachyOS, 4 GB de VRAM) como trabajador por el mismo relé: aporta pocas capas; sirve para
  probar con una máquina que no es de Kaggle.
- Script `fabric-nodo` para cualquier Linux con GPU: baja llama.cpp, abre el túnel y entra al relé.

### F6. Que no se caiga

- Si un trabajador se cae, el maestro deja de poder generar: el relé lo saca de la lista, devuelve
  `nodo_desconectado` y el maestro se reinicia con los trabajadores que queden (o con un modelo más
  chico que quepa solo).
- Latidos también en el túnel; reinicio automático; vencimiento de tareas ya existe (15 min).
- Pruebas automáticas: maestro + trabajador RPC en la misma máquina dentro de CI (modelo diminuto).

### F7. Abrirlo (opcional)

- Hoy el enjambre es privado (clave compartida). Para que cualquiera done: claves por invitado,
  límite por persona y moderación de la sala. Solo si lo decides.

## Qué significa "completo y funcional"

- [ ] Un modelo que no cabe en una máquina responde, repartido entre 2 computadoras distintas.
- [ ] Ese modelo aparece en la web y en `preguntar_red` como un nodo más.
- [ ] Velocidad y aciertos medidos y escritos (también si son malos).
- [ ] Nada inseguro expuesto: RPC solo por el relé con clave; nada en Oracle.
- [ ] Se levanta con un comando (`lanzar.py --dividido`) y se recupera si un equipo se cae.
- [ ] Repo privado en GitHub con `LEEME.md` y este plan al día; web publicada en Vercel.

## Decisiones tuyas

Decidido 31-oct: web en Vercel, relé en Kaggle, nada en Oracle (relé y bloque de Caddy ya quitados).

1. ¿Enjambre privado (solo con clave) o abierto a donantes (F7)?
2. ¿Borro los 6 cuadernos del 90 %?
3. Límite conocido: el relé vive mientras corra el cuaderno `calidad` (Kaggle corta a las 12 h y da
   30 h de GPU por semana). Fuera de esas horas la web dice "la red está apagada".
