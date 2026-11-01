# Plan: red de IA completa y funcional (31-oct-2026)

## Dónde estamos (medido, sin adornos)

| Qué | Estado |
| --- | --- |
| Repartir preguntas entre equipos (cada uno con su modelo completo) | FUNCIONA. Relé en Oracle + 2 cuadernos Kaggle (Qwen3.8-27B y Qwen3.6-35B-A3B) respondiendo de verdad. |
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
2. **Kaggle no acepta conexiones entrantes.** Dos cuadernos solo se hablan a través de un tercero:
   Oracle hace de punto de encuentro.
3. **Oracle es producción de AsistoYA.** La primera carga manda los pesos de la mitad remota por el
   túnel (decenas de GB). Eso se hace fuera de horario escolar y con tope de ancho de banda; después,
   la caché local del trabajador (`ggml-rpc-server -c`) evita repetirlo.

## Fases

Cada fase termina con una medición escrita en `kaggle/resultados/<fecha>/INFORME.md`. Si una fase
no da, se dice y se decide antes de seguir.

### F0. Cerrar lo que ya existe (1 sesión)

- Leer `llama-server.log` de los dos nodos (`lanzar.py --bajar`) y corregir la velocidad. Sospechas:
  capas en CPU, calentamiento contado como primer token, las dos pruebas compitiendo por la GPU.
- Repetir la búsqueda con lotes de 8 (`PRUEBAS=busqueda`) para tener los aciertos en los puestos 1 y 5.
- Repo PRIVADO en GitHub (`synrag-fabric`) y subir la rama `red-fabric`.
- Rotar la clave del relé (va dentro del código de los cuadernos).
- Borrar los 6 cuadernos del 90 %, si lo confirmas.

### F1. Dividir dentro de un solo cuaderno (prueba del mecanismo, sin red)

- En un cuaderno con 2 x T4: `ggml-rpc-server` en la GPU 1 (`CUDA_VISIBLE_DEVICES=1`, `-H 127.0.0.1`)
  y `llama-server --rpc 127.0.0.1:50052` en la GPU 0.
- Verificar que el binario CUDA ya compilado trae `ggml-rpc-server`; si no, compilar con `-DGGML_RPC=ON`.
- Medir contra el reparto normal (`--split-mode layer` en las dos GPU locales). La diferencia es el
  costo propio del RPC.
- Éxito: misma respuesta y una caída de velocidad medida y pequeña.

### F2. Túnel por Oracle

- `chisel` (TCP sobre WebSocket con usuario y clave), versión fija, detrás de Caddy en un subdominio
  propio. Los puertos RPC quedan solo en el loopback de Oracle; nada público.
- El trabajador abre `R:50052` (reverso) y el maestro lo usa como `127.0.0.1:50052`.
- Medir la ida y vuelta Kaggle -> Oracle -> Kaggle y el CPU de Oracle durante una carga de pesos.
- Tope de ancho de banda en el contenedor y horario permitido (noches y fines de semana).

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

- Tu PC (CachyOS, 4 GB de VRAM) como trabajador por el mismo túnel: aporta pocas capas; sirve para
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
- [ ] Nada inseguro expuesto: RPC solo por túnel con clave; Oracle sin afectar a AsistoYA.
- [ ] Se levanta con un comando (`lanzar.py --dividido`) y se recupera si un equipo se cae.
- [ ] Repo privado en GitHub con `LEEME.md` y este plan al día.

## Decisiones tuyas

1. ¿Dónde vive la web? En Oracle (Caddy sirve `fabric/web/dist`) o en Vercel como synrag.vercel.app.
2. ¿Enjambre privado (solo con clave) o abierto a donantes (F7)?
3. ¿Se permite pasar pesos por Oracle (F2/F3) en noches y fines de semana?
4. ¿Borro los 6 cuadernos del 90 %?
