# Informe A9: Conexion Directa P2P entre Casas y Perforacion de NAT

Fecha: 2026-11-04
Agente: A9
Rama: a9-conexion-casas
Worktree: /home/reyno/Proyectos/cs-a9

---

## 1. Contexto y Problema

Actualmente, los nodos de CompuShare se comunican mediante un relé WebSocket expuesto a través de un túnel `trycloudflare` (`fabric/rele/rele.py`, `fabric/tubo.py`, `descubrir.py`).
En las pruebas previas en Kaggle (registradas en `fabric/PRUEBA-10.md` y `PLAN.md`), la latencia de ida y vuelta (RTT) medida por este túnel fue de **237 ms con una variacion de 40 ms**.

En la inferencia distribuida de modelos con `llama.cpp` (`ggml-rpc`), la generacion de cada token requiere enviar secuencialmente las activaciones y estados intermedios entre los nodos participantes. Cuando las computadoras estan encadenadas en serie, cada palabra cruza todos los nodos. Con 237 ms de RTT por salto, la latencia de red se convierte en el factor dominante del cuello de botella (una llamada sincrona por capa/token impone un limite teorico de menos de 4 tokens por segundo para un solo nodo remoto).

El objetivo de este trabajo es:
1. Investigar tecnicas y protocolos de conexion directa punto a punto (P2P) capaces de atravesar enrutadores residenciales con NAT sin servidores intermedios de retransmision pesada.
2. Evaluar la viabilidad, dependencias, licencias y requisitos de infraestructura de cada alternativa.
3. Construir un prototipo funcional en `fabric/directo/` con canal WebRTC DataChannel (usando STUN publico) y mecanismo de degradacion automatica (fallback) al rele WebSocket actual.
4. Medir en local el costo computacional del canal directo frente a `fabric/tubo.py`.
5. Formular una recomendacion practica para CompuShare indicando los pasos pendientes para pruebas entre casas reales.

---

## 2. Investigacion: Perforacion de NAT y Alternativas P2P

### 2.1 Perforacion de NAT UDP vs TCP (STUN, ICE)

Los enrutadores domesticos tradicionales aplican traduccion de direcciones de red (NAT) y cortafuegos de estado (*stateful firewalls*). Permiten el trafico saliente hacia internet, pero descartan paquetes entrantes no solicitados salvo que exista una entrada previa en su tabla de traduccion interna (*conntrack*).

- **STUN (Session Traversal Utilities for NAT, RFC 5389 / RFC 8489)**:
  Un cliente envia una solicitud UDP/TCP a un servidor STUN con direccion IP publica conocida. El servidor responde informando la direccion IP publica y el puerto desde donde vio llegar la solicitud (*server-reflexive candidate*). Servidores STUN publicos de alta disponibilidad (como `stun.l.google.com:19302`) operan de forma gratuita y no transportan datos del usuario, solo reflejan la tupla IP:puerto.
- **ICE (Interactive Connectivity Establishment, RFC 8445)**:
  Protocolo estandarizado que orquesta la recoleccion de direcciones candidatas locales (*host*), reflexivas (*srflx* via STUN) y de relevo (*relay* via TURN). Ambos extremos intercambian sus listas de candidatos a traves de un canal de senalizacion fuera de banda y ejecutan pruebas de conectividad bidireccionales simultaneas para seleccionar el enlace directo mas eficiente.
- **UDP vs TCP Hole-Punching**:
  - *UDP Hole-Punching*: Es el metodo mas extendido y fiable. Al ser UDP un protocolo sin estado de conexion en el kernel, ambos extremos pueden enviar paquetes hacia la IP y puerto externo del otro casi simultaneamente; en cuanto ambos routers registran un paquete saliente hacia dicho destino, sus tablas de filtrado se abren permitiendo el trafico bidireccional.
  - *TCP Hole-Punching*: Mas complejo y propenso a fallos. Requiere sincronizacion precisa de paquetes SYN para lograr una "apertura simultanea" de conexion TCP (*TCP Simultaneous-Open*, RFC 793). Varios sistemas operativos y cortafuegos residenciales interceptan o descartan paquetes SYN entrantes si no coinciden exactamente con la secuencia esperada.

### 2.2 Que porcentaje de NAT domesticos lo permite segun estudios publicos

Los estudios academicos e industriales reportan los siguientes porcentajes de exito en conexion directa sin relevo:

1. **Bryan Ford, Pyda Srisuresh y Dan Kegel (USENIX 2005)**:
   *Documento: "Peer-to-Peer Communication Across Network Address Translators" (https://www.usenix.org/legacy/event/usenix05/tech/general/full_papers/ford/ford.pdf / arXiv:cs/0503028)*.
   - Evaluacion sobre diversos enrutadores y NATs residenciales:
     - Perforacion UDP: **82% de exito**.
     - Perforacion TCP: **64% de exito**.
   - Los fallos principales correspondieron a NATs de tipo simetrico (*Symmetric NAT*), donde el router asigna un puerto externo diferente para cada direccion IP de destino remota.

2. **Dennis Trautwein et al. (arXiv:2510.27500 / arXiv:2210.15570, campana de medicion a gran escala con libp2p DCUtR)**:
   *Documento: "Challenging Tribal Knowledge — Large Scale Measurement Campaign on Decentralized NAT Traversal" (https://arxiv.org/abs/2510.27500)*.
   - Analizo mas de 4,4 millones de intentos de perforacion directa sobre 85.000 redes distintas en 167 paises utilizando DCUtR (*Direct Connection Upgrade through Relay*):
     - Tasa de exito condicional en la fase de perforacion: **70% (+- 7,1%)**.
     - Sincronizacion RTT: Desmintio la creencia popular de que TCP es inviable frente a UDP cuando hay sincronizacion estricta por RTT: tanto TCP como QUIC alcanzaron tasas de exito comparables (~70%).
     - Eficiencia: El **97,6%** de las perforaciones exitosas se concretaron en el **primer intento**.

3. **Tailscale (David Anderson, 2020)**:
   *Articulo tecnico: "How NAT traversal works" (https://tailscale.com/blog/how-nat-traversal-works/)*.
   - Tailscale reporta que mas del **90% (entre 90% y 95%)** de las conexiones entre dispositivos detras de NAT se establecen de forma **directa punto a punto**.
   - Logran esta tasa superior combinando: UDP hole punching sobre WireGuard, descubrimiento de mapeos UPnP/NAT-PMP/PCP en routers domesticos, sondeo en paralelo y tecnicas de prediccion de puertos para NATs semi-simetricos. Para el 5-10% restante donde ambos extremos estan tras NAT simetrico estricto o cortafuegos corporativos, utilizan su red de servidores DERP (*Designated Encrypted Relay for Packets*).

4. **Estadisticas de la industria WebRTC (Callstats.io / Telecomunicaciones Web)**:
   - En llamadas residenciales P2P, entre el **75% y el 85%** de las sesiones logran enlace directo mediante candidatos *host* y *srflx* (STUN publico).
   - Unicamente entre el **15% y 25%** requiere retransmision completa por servidor TURN, porcentaje que sube a mas del 80% solo en redes corporativas con inspeccion profunda de paquetes (DPI) o bloqueo de trafico UDP saliente.

### 2.3 Comparativa de Tecnologias Evaluadas

| Tecnologia | Sirve para flujo continuo ggml-rpc | Dependencias principales | Licencia | Requiere servidor propio | Evaluacion para CompuShare |
|---|---|---|---|---|---|
| **WebRTC Data Channels (`aiortc`)** | Si (SCTP confiable y ordenado sobre DTLS/UDP). Soporta transmision binaria continua. | `aiortc`, `aioice`, `cryptography`, `pyee`, `pylibsrtp` (instalable con `uv`). | BSD-3-Clause | **No**. STUN publico gratuito (`google`, `cloudflare`). La senalizacion SDP ligera (unos cientos de bytes) usa el rele WebSocket existente. | **Opcion recomendada para prototipo en Python puro.** No requiere root, ni servidores propios, ni infraestructura en Oracle. |
| **QUIC (`aioquic`)** | Si (streams multiplexados confiables y con control de congestion sobre UDP). | `aioquic`, `pylsqpack`, `cryptography`, `pyopenssl`. | BSD-3-Clause | **Parcial**. No requiere servidor propio para la transferencia, pero el estandar QUIC (RFC 9000) no define el mecanismo de perforacion P2P ni ICE/STUN; requiere implementar senalizacion y coordinacion manual de paquetes UDP en el mismo puerto. | Excelente protocolo de transporte, pero carece de un marco ICE integrado para intercambio automatico de candidatos STUN en Python. |
| **Tailscale / Headscale** | Si (crea interfaces WireGuard virtuales; `ggml-rpc` se conecta por sockets TCP normales a maxima velocidad de red local). | Requiere binario de sistema `tailscaled` en Go y permisos para tuneles de red (`/dev/net/tun` o modo userspace SOCKS5/proxy). | Tailscale cliente: BSD-3-Clause (plataforma SaaS con plan gratuito hasta 100 maquinas). Headscale: BSD-3-Clause (servidor autohospedado). | Tailscale SaaS: **No**. Headscale: **Si** (requiere servidor con IP publica propia; no podemos usar Oracle). | **Opcion recomendada si los participantes pueden instalar un paquete del sistema.** Ofrece el mayor rendimiento bruto al estar en kernel/Go. |
| **libp2p** | Si (canales de datos sobre Yamux/QUIC). | En Python (`py-libp2p`): incompleto y desactualizado. En Go (`go-libp2p`) o Rust (`rust-libp2p`): maduro. | MIT / Apache-2.0 | **Si / Parcial**. Requiere nodos publicos de retransmision *Circuit Relay v2* para coordinar el protocolo DCUtR. | Descartado en Python por falta de soporte estable para DCUtR y perforacion QUIC en `py-libp2p`. |

---

## 3. Prototipo Implementado: `fabric/directo/`

Se implemento una solucion hibrida completa en `fabric/directo/` con canal directo WebRTC Data Channels y degradacion transparente al rele WebSocket:

### 3.1 Estructura del modulo

- `fabric/directo/__init__.py`: Exporta `CanalWebRTC` y `TuboHibrido`.
- `fabric/directo/canal.py`:
  - Implementa la clase `CanalWebRTC` utilizando `aiortc.RTCPeerConnection` y `RTCConfiguration`.
  - Configura por defecto servidores STUN publicos de Google (`stun:stun.l.google.com:19302`).
  - Proporciona metodos asincronos para creacion de oferta con recoleccion completa de candidatos ICE (`obtener_oferta()`), generacion de respuesta (`responder_oferta()`) y aplicacion de respuesta remota (`aplicar_respuesta()`).
  - Implementa el puente bidireccional asincrono `puente_tcp(lector, escritor)` entre sockets TCP de `asyncio` y el canal `RTCDataChannel`, con control de congestion por contrapresion (*backpressure*) monitoreando `channel.bufferedAmount` para evitar desbordamiento de memoria.
- `fabric/directo/tubo_hibrido.py`:
  - Implementa la clase `TuboHibrido`.
  - Intenta primero negociar la conexion directa P2P mediante `CanalWebRTC`.
  - Si el establecimiento directo agota el tiempo limite (`timeout_directo`, por defecto 3,5 s), falla la recoleccion ICE o se produce cualquier error de red, registra la advertencia y conmuta de inmediato al rele WebSocket existente (`fabric.tubo.Tubo`).
  - Expone la propiedad `modo` ("directo" o "rele") para observabilidad del estado de red.
- `fabric/directo/medir.py`:
  - Script ejecutable para pruebas de rendimiento comparativas entre TCP directo local, el rele WebSocket actual (`tubo.py`) y el canal directo WebRTC (`aiortc` + STUN).
- `fabric/pruebas/test_directo.py`:
  - Cuatro pruebas unitarias y de integracion:
    1. `test_canal_directo_p2p_datos`: Intercambio de 512 KB directamente sobre DataChannel con recoleccion de candidatos STUN.
    2. `test_puente_tcp_directo_integridad`: Servicio de eco TCP atravesando el canal WebRTC P2P con 1 MB de datos aleatorios, verificando integridad exacta byte a byte.
    3. `test_tubo_hibrido_fallback_a_rele`: Verificacion de fallback automatico a `fabric.tubo.Tubo` ante fallo o forzado de conexion directa, transmitiendo 1 MB integro a traves del rele.
    4. `test_canal_directo_timeout_espera`: Verificacion de que la falta de senalizacion eleva `TimeoutError` sin congelar el proceso.

### 3.2 Advertencia tecnica: Por que la prueba local no prueba NAT

Las pruebas locales (en `127.0.0.1` o interfaces de red locales) verifican la correcta negociacion SDP, la maquina de estados de ICE, la creacion del DataChannel y la fidelidad del puente TCP.
**Sin embargo, la prueba local no prueba la perforacion de NAT**:
- En entorno local, la biblioteca ICE selecciona candidatos de tipo `host` (direcciones de interfaces locales como `127.0.0.1` o `192.168.x.x`). Los paquetes UDP viajan por la interfaz loopback o el conmutador de red local sin atravesar ninguna tabla de mapeo de puertos externa, sin traduccion de direcciones de red (NAT), sin temporizadores de expiracion de conexiones UDP y sin cortafuegos de filtrado conntrack de ningun router de operadora (ISP).
- Para probar la perforacion real de NAT se requiere que los dos extremos esten fisicamente detras de routers residenciales diferentes o conexiones moviles distintas (CGNAT).

---

## 4. Mediciones Locales: Canal Directo vs `fabric/tubo.py`

### 4.1 Metodologia y Comando Exacto

Se midio el desempeno en esta maquina con un volumen identico de 5,0 MB (5.242.880 bytes de datos aleatorios binarios via `os.urandom`) transferidos en modalidad de ida y vuelta (cliente -> canal -> servicio de eco TCP -> canal -> cliente, totalizando 10,0 MB de trafico neto cursado), ademas de 20 muestras consecutivas de latencia RTT (ida y vuelta de un paquete minimo).

Comando exacto ejecutado:
```bash
PYTHONPATH=. /home/reyno/Proyectos/CompuShare/.venv/bin/python -m fabric.directo.medir --mb 5 --rtt-muestras 20
```

Archivo de resultado generado:
`fabric/directo/resultado_medicion.json`

### 4.2 Cifras Medidas

Fecha de medicion: `2026-11-04T13:42:55.635182+00:00`
Entorno: Linux CachyOS (x86_64), Python 3.12.15, `aiortc 1.15.0`, `websockets 17.4`.

| Canal evaluado | Caudal util (MB/s) | Tiempo transferencia 5 MB (s) | RTT medio (ms) | RTT min (ms) | RTT max (ms) | Tiempo negociacion / conexion (ms) |
|---|---|---|---|---|---|---|
| **1. TCP Directo Local (referencia kernel)** | **662,43 MB/s** | 0,0075 s | 0,028 ms | 0,020 ms | 0,074 ms | 0,23 ms |
| **2. Relé WebSocket (`tubo.py`)** | **326,48 MB/s** | 0,0153 s | 0,102 ms | 0,063 ms | 0,775 ms | 1,35 ms |
| **3. Directo WebRTC DataChannel (`aiortc` + STUN)** | **6,39 MB/s** | 0,7830 s | 0,491 ms | 0,423 ms | 0,879 ms | 1135,34 ms |

### 4.3 Analisis e Interpretacion de los Resultados

1. **Caudal bruto en local**:
   En entorno puramente local, `fabric.tubo.Tubo` (WebSocket sobre TCP sin cifrar) es significativamente mas rapido en ancho de banda bruto (326 MB/s frente a 6,39 MB/s de `aiortc`).
   *Causa tecnica*: `aiortc` implementa en espacio de usuario (Python + CFFI) la pila completa de WebRTC: protocolo SCTP, segmentacion y reensamblado de tramas, y cifrado simetrico DTLS (AES-GCM via OpenSSL/cryptography) sobre cada paquete UDP individual. Por el contrario, en loopback local el WebSocket viaja sobre TCP directo sin cifrar donde el kernel de Linux realiza copias de memoria en RAM a velocidad casi de bus.

2. **Impacto real en internet entre dos casas (Latencia vs Caudal)**:
   A pesar de la menor tasa de transferencia bruta de `aiortc` en Python, en el escenario real de internet entre dos casas la ecuacion se invierte a favor del canal directo debido al factor critico de **latencia de ida y vuelta (RTT)**:
   - Con el rele WebSocket actual (tunel Cloudflare -> Kaggle -> tunel Cloudflare): la latencia RTT medida es de **237 ms**.
   - Con conexion directa P2P (UDP directo entre dos casas por internet residencial): la latencia RTT tipica es de:
     - Misma ciudad: **10 a 20 ms** (reduccion de latencia de **91% a 95%**).
     - Mismo pais: **30 a 50 ms** (reduccion de latencia de **78% a 87%**).
     - Diferentes continentes: **100 a 140 ms** (reduccion de ~50%).
   - Para la inferencia distribuida de `llama.cpp` (`ggml-rpc`), el trafico por cada token generado no es de gigabytes continuos, sino de activaciones intermedias (algunos cientos de kilobytes a un par de megabytes) transmitidas de forma estrictamente sincrona capa por capa. Con 237 ms de RTT, el sistema no puede superar ~4 tokens/segundo por nodo. Con 20 ms de RTT directo, la red permite teoricamente hasta 50 intercambios sincronos por segundo.

---

## 5. Pruebas Automatizadas

Se ejecuto el conjunto completo de pruebas del repositorio para garantizar que las modificaciones y dependencias nuevas preserven la integridad del proyecto.

Comando ejecutado:
```bash
/home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -q fabric/pruebas -p no:cacheprovider
```

Resultado:
- **22 pruebas pasadas, 1 omitida** en 11,53 segundos.
- Todas las pruebas previas se mantuvieron en verde.
- Las 4 pruebas nuevas de `fabric/pruebas/test_directo.py` pasaron satisfactoriamente.

---

## 6. Recomendacion Concreta para CompuShare

Se recomiendan dos fases sucesivas de evolucion:

### Fase 1: Integracion Inmediata (Prototipo Python sin Servidores Nuevos)
- Adoptar la arquitectura de `TuboHibrido`:
  1. Utilizar el rele WebSocket actual de Cloudflare exclusivamente como **canal de senalizacion** (para intercambiar las ofertas/respuestas SDP de WebRTC de unos pocos cientos de bytes una sola vez al inicio).
  2. Usar servidores STUN publicos gratuitos de Google (`stun:stun.l.google.com:19302`) y Cloudflare para descubrir candidatos reflexivos sin costo de servidor.
  3. Si la conexion directa P2P se abre (esperable en ~80% de conexiones domesticas), transferir el flujo RPC de `llama.cpp` de forma directa por el DataChannel.
  4. Si la perforacion falla (tras 3,5 segundos), degradar al rele actual sin interrumpir al usuario.
- Ventaja: No requiere que el usuario instale nada fuera de Python ni requiere servidores propios en Oracle.

### Fase 2: Opcion de Maximo Rendimiento (Modo Red Nativa / Tailscale)
- Para usuarios que deseen velocidad maxima de inferencia (reduciendo la sobrecarga de DTLS en Python userspace):
  - Permitir a los nodos conectarse mediante una red mesh WireGuard (Tailscale en modo userspace o interfaz de red).
  - Al operar en Go/C y a nivel de sockets TCP directos, se combinan las dos ventajas: perforacion de NAT superior al 90% y caudal de cientos de MB/s con latencias de 10-30 ms entre casas.

---

## 7. Que Falta para Probarlo entre Dos Casas Reales

Para llevar a cabo la primera prueba real entre dos ubicaciones residenciales distintas:

1. **Cablear la senalizacion SDP en el protocolo de `rele.py`**:
   - Agregar en el protocolo JSON del rele dos mensajes ligeros: `{"t": "sdp_oferta", "de": ..., "para": ..., "sdp": ...}` y `{"t": "sdp_respuesta", ...}`. El rele unicamente reenvia estos JSON entre el maestro y el trabajador para que puedan negociar WebRTC sin necesidad de canales externos como ntfy.
2. **Disponer de dos maquinas fisicas en conexiones de internet separadas**:
   - Maquina A: Laptop local en conexion de fibra optica residencial.
   - Maquina B: Otra computadora en otra casa o conectada mediante red movil 4G/5G compartida (tethering) para simular un NAT externo con direccion IP publica distinta.
3. **Herramienta de diagnostico de NAT previa**:
   - Ejecutar una rutina de deteccion de tipo de NAT (Full Cone, Restricted Cone, Port Restricted, Symmetric) enviando paquetes a dos IPs de STUN diferentes, para advertir al usuario antes de iniciar la inferencia si su router requiere tecnicas especiales.
4. **Prueba de extremo a extremo con `ggml-rpc-server`**:
   - Levantar `ggml-rpc-server` en la Casa B, conectar `TuboHibrido`, iniciar `llama-server --rpc 127.0.0.1:<puerto>` en la Casa A y comparar la generacion real en tokens/segundo frente al rele de Cloudflare.

---

## 8. Que Quedo Pendiente y Que No Funciono

1. **Lo que no funciono**:
   - `aiortc` en la prueba local inicial no activaba el evento `on("open")` en el extremo receptor si la vinculacion del canal ocurria cuando `readyState` ya habia pasado a `"open"`. Se corrigio inspeccionando `readyState == "open"` de forma sincrona en la vinculacion del DataChannel.
   - Rendimiento bruto de `aiortc` en local: el throughput de transferencia en loopback quedo en 6,39 MB/s frente a los 326 MB/s del WebSocket nativo, debido a la implementacion en Python/CFFI de la criptografia DTLS y la maquina de estados SCTP.
2. **Lo que quedo pendiente**:
   - Implementar el reenvio de senalizacion SDP dentro de `fabric/rele/rele.py` para que los nodos no requieran senalizacion manual ni canales ad-hoc.
   - Probar en un escenario real con dos direcciones IP publicas de diferentes proveedores de internet (ISP) para validar la tasa de apertura de puertos con NATs simetricos y CGNAT movil.
