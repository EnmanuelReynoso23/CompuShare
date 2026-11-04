package rele

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/protocolo"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/tubo"
)

type Tarea struct {
	ID          string
	IDCliente   string
	Solicitante string
	Nodo        string
	Creada      time.Time
}

type Rele struct {
	Clave          string
	Puerto         int
	MaxConexiones  int
	MaxBytes       int64
	Rafaga         float64
	Ritmo          float64
	TareaVenceS    float64
	LatidoVenceS   float64
	TuboPuertoBase int

	mu              sync.RWMutex
	pares           map[string]*Par
	tareas          map[string]*Tarea
	salas           map[string][]*protocolo.MensajeChat
	ofertas         map[string]*Oferta
	tuboBytes       atomic.Int64
	tareasAtendidas atomic.Int64
	inicio          time.Time
}

func Nuevo(clave string, puerto int) *Rele {
	if clave == "" {
		log.Fatal("Falta FABRIC_CLAVE: el relé no arranca abierto.")
	}
	if puerto < 0 {
		puerto = 8095
	}
	return &Rele{
		Clave:          clave,
		Puerto:         puerto,
		MaxConexiones:  300,
		MaxBytes:       4 * 1024 * 1024,
		Rafaga:         120.0,
		Ritmo:          40.0,
		TareaVenceS:    900.0,
		LatidoVenceS:   60.0,
		TuboPuertoBase: 50100,
		pares:          make(map[string]*Par),
		tareas:         make(map[string]*Tarea),
		salas:          make(map[string][]*protocolo.MensajeChat),
		ofertas:        make(map[string]*Oferta),
		inicio:         time.Now(),
	}
}

func (r *Rele) tokenHex(numBytes int) string {
	b := make([]byte, numBytes)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func (r *Rele) claveCorrecta(c string) bool {
	return hmac.Equal([]byte(c), []byte(r.Clave))
}

func (r *Rele) nodos(enjambre string) []protocolo.NodoPublico {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var res []protocolo.NodoPublico
	for _, p := range r.pares {
		if p.Enjambre == enjambre && p.Rol == "nodo" {
			res = append(res, p.Publico())
		}
	}
	if res == nil {
		res = []protocolo.NodoPublico{}
	}
	return res
}

func (r *Rele) difundir(enjambre string, msg any) {
	r.mu.RLock()
	var destinatarios []*Par
	for _, p := range r.pares {
		if p.Enjambre == enjambre {
			destinatarios = append(destinatarios, p)
		}
	}
	r.mu.RUnlock()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	for _, p := range destinatarios {
		_ = p.Enviar(ctx, msg)
	}
}

func (r *Rele) avisarNodos(enjambre string) {
	r.difundir(enjambre, protocolo.NodosMsg{
		T:     "nodos",
		Nodos: r.nodos(enjambre),
	})
}

func (r *Rele) elegirNodo(enjambre, tipo, excluir, para string) *Par {
	r.mu.RLock()
	defer r.mu.RUnlock()

	if para != "" {
		p, ok := r.pares[para]
		if ok && p.Enjambre == enjambre && p.Rol == "nodo" {
			p.mu.Lock()
			_, tieneCap := p.Capacidades[tipo]
			p.mu.Unlock()
			if tieneCap {
				return p
			}
		}
		return nil
	}

	var candidato *Par
	for _, p := range r.pares {
		if p.Enjambre == enjambre && p.Rol == "nodo" && p.ID != excluir {
			p.mu.Lock()
			_, tieneCap := p.Capacidades[tipo]
			carga := p.Carga
			comp := p.Completadas
			p.mu.Unlock()

			if tieneCap {
				if candidato == nil {
					candidato = p
				} else {
					candidato.mu.Lock()
					cCarga := candidato.Carga
					cComp := candidato.Completadas
					candidato.mu.Unlock()

					if carga < cCarga || (carga == cCarga && comp < cComp) {
						candidato = p
					}
				}
			}
		}
	}
	return candidato
}

func (r *Rele) Resumen() map[string]any {
	r.mu.RLock()
	defer r.mu.RUnlock()

	enjambres := make(map[string]map[string]int)
	for _, p := range r.pares {
		p.mu.Lock()
		e, ok := enjambres[p.Enjambre]
		if !ok {
			e = map[string]int{"nodos": 0, "clientes": 0}
			enjambres[p.Enjambre] = e
		}
		if p.Rol == "nodo" {
			e["nodos"]++
		} else {
			e["clientes"]++
		}
		p.mu.Unlock()
	}

	tubos := make(map[string]map[string]any)
	for _, o := range r.ofertas {
		tubos[o.Nombre] = map[string]any{
			"puerto":   o.Puerto,
			"abiertos": o.Abiertos.Load(),
			"info":     o.Info,
		}
	}

	return map[string]any{
		"ok":               true,
		"segundos_arriba":  int(time.Since(r.inicio).Seconds()),
		"conexiones":       len(r.pares),
		"tareas_en_curso":  len(r.tareas),
		"tareas_atendidas": r.tareasAtendidas.Load(),
		"enjambres":        enjambres,
		"tubos":            tubos,
		"tubo_bytes":       r.tuboBytes.Load(),
	}
}

func (r *Rele) handleSalud(w http.ResponseWriter, req *http.Request) {
	cuerpo, err := json.Marshal(r.Resumen())
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(cuerpo)
}

func (r *Rele) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	camino := strings.Split(req.URL.Path, "?")[0]
	if camino == "/salud" {
		r.handleSalud(w, req)
		return
	}

	if camino == "/tubo" {
		r.atenderTubo(w, req)
		return
	}

	r.atenderPeer(w, req)
}

func (r *Rele) atenderTubo(w http.ResponseWriter, req *http.Request) {
	opts := &websocket.AcceptOptions{
		CompressionMode: websocket.CompressionDisabled,
		OriginPatterns:  []string{"*"},
	}
	ws, err := websocket.Accept(w, req, opts)
	if err != nil {
		return
	}
	defer ws.Close(websocket.StatusNormalClosure, "")
	ws.SetReadLimit(r.MaxBytes)

	ctxTimeout, cancel := context.WithTimeout(req.Context(), 10*time.Second)
	var primero map[string]any
	err = wsjson.Read(ctxTimeout, ws, &primero)
	cancel()
	if err != nil {
		_ = ws.Close(websocket.StatusPolicyViolation, "falta hola")
		return
	}

	clave := fmt.Sprintf("%v", primero["clave"])
	if !r.claveCorrecta(clave) {
		_ = ws.Close(websocket.StatusPolicyViolation, "clave incorrecta")
		return
	}

	tVal, _ := primero["t"].(string)
	if tVal == "ofrezco" {
		r.ofrecer(ws, primero)
	} else if tVal == "datos" {
		idVal := fmt.Sprintf("%v", primero["id"])
		var ch chan CanalizadoResult
		r.mu.RLock()
		for _, o := range r.ofertas {
			o.EspMu.Lock()
			if c, ok := o.Esperando[idVal]; ok {
				ch = c
				delete(o.Esperando, idVal)
			}
			o.EspMu.Unlock()
			if ch != nil {
				break
			}
		}
		r.mu.RUnlock()

		if ch == nil {
			_ = ws.Close(websocket.StatusPolicyViolation, "tubo desconocido")
			return
		}

		terminado := make(chan struct{})
		select {
		case ch <- CanalizadoResult{WS: ws, Terminado: terminado}:
			<-terminado
		case <-req.Context().Done():
		}
	} else {
		_ = ws.Close(websocket.StatusPolicyViolation, "tubo inválido")
	}
}

func (r *Rele) ofrecer(ws *websocket.Conn, primero map[string]any) {
	nombre, _ := primero["nombre"].(string)
	if !protocolo.EnjambreValido.MatchString(nombre) {
		_ = ws.Close(websocket.StatusPolicyViolation, "nombre inválido")
		return
	}

	info := make(map[string]any)
	if rawInfo, ok := primero["info"].(map[string]any); ok {
		cont := 0
		for k, v := range rawInfo {
			if cont >= 10 {
				break
			}
			kLimpio := k
			if len(kLimpio) > 30 {
				kLimpio = kLimpio[:30]
			}
			switch val := v.(type) {
			case int, int64, float64, string:
				info[kLimpio] = val
				cont++
			}
		}
	}

	r.mu.Lock()
	if vieja, ok := r.ofertas[nombre]; ok {
		vieja.Cerrar()
		delete(r.ofertas, nombre)
	}

	usados := make(map[int]struct{})
	for _, o := range r.ofertas {
		usados[o.Puerto] = struct{}{}
	}

	puertoElegido := -1
	var listener net.Listener
	for p := r.TuboPuertoBase; p < r.TuboPuertoBase+100; p++ {
		if _, used := usados[p]; used {
			continue
		}
		ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", p))
		if err == nil {
			puertoElegido = p
			listener = ln
			break
		}
	}

	if puertoElegido == -1 || listener == nil {
		r.mu.Unlock()
		_ = ws.Close(websocket.StatusInternalError, "sin puertos de tubo")
		return
	}

	oferta := NuevaOferta(nombre, ws, puertoElegido, listener, info)
	r.ofertas[nombre] = oferta
	r.mu.Unlock()

	log.Printf("tubo %s ofrecido en 127.0.0.1:%d", nombre, puertoElegido)

	ctx := context.Background()
	oferta.ControlMu.Lock()
	err := wsjson.Write(ctx, ws, protocolo.Listo{
		T:      "listo",
		Puerto: puertoElegido,
	})
	oferta.ControlMu.Unlock()
	if err != nil {
		r.retirarOferta(nombre, oferta)
		return
	}

	go r.escucharTuboTCP(oferta)

	for {
		var dummy map[string]any
		if err := wsjson.Read(ctx, ws, &dummy); err != nil {
			break
		}
	}

	r.retirarOferta(nombre, oferta)
}

func (r *Rele) retirarOferta(nombre string, oferta *Oferta) {
	r.mu.Lock()
	oferta.Cerrar()
	if r.ofertas[nombre] == oferta {
		delete(r.ofertas, nombre)
	}
	r.mu.Unlock()
	log.Printf("tubo %s retirado", nombre)
}

func (r *Rele) escucharTuboTCP(oferta *Oferta) {
	for {
		conn, err := oferta.Listener.Accept()
		if err != nil {
			return
		}
		if tc, ok := conn.(*net.TCPConn); ok {
			_ = tc.SetNoDelay(true)
		}
		go r.abrirTubo(oferta, conn)
	}
}

func (r *Rele) abrirTubo(oferta *Oferta, tcpConn net.Conn) {
	ident := r.tokenHex(8)
	ch := make(chan CanalizadoResult, 1)

	oferta.EspMu.Lock()
	oferta.Esperando[ident] = ch
	oferta.EspMu.Unlock()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	oferta.ControlMu.Lock()
	err := wsjson.Write(ctx, oferta.Control, protocolo.Abre{
		T:  "abre",
		ID: ident,
	})
	oferta.ControlMu.Unlock()

	if err != nil {
		oferta.EspMu.Lock()
		delete(oferta.Esperando, ident)
		oferta.EspMu.Unlock()
		_ = tcpConn.Close()
		return
	}

	var res CanalizadoResult
	select {
	case res = <-ch:
		if res.WS == nil {
			_ = tcpConn.Close()
			return
		}
	case <-ctx.Done():
		oferta.EspMu.Lock()
		delete(oferta.Esperando, ident)
		oferta.EspMu.Unlock()
		_ = tcpConn.Close()
		return
	}

	oferta.Abiertos.Add(1)
	defer func() {
		oferta.Abiertos.Add(-1)
		close(res.Terminado)
	}()

	tubo.Canalizar(context.Background(), res.WS, tcpConn, &r.tuboBytes)
}

func (r *Rele) atenderPeer(w http.ResponseWriter, req *http.Request) {
	r.mu.RLock()
	totalPares := len(r.pares)
	r.mu.RUnlock()

	opts := &websocket.AcceptOptions{
		CompressionMode: websocket.CompressionDisabled,
		OriginPatterns:  []string{"*"},
	}
	ws, err := websocket.Accept(w, req, opts)
	if err != nil {
		return
	}
	defer ws.Close(websocket.StatusNormalClosure, "")
	ws.SetReadLimit(r.MaxBytes)

	if totalPares >= r.MaxConexiones {
		_ = ws.Close(websocket.StatusCode(1013), "relé lleno")
		return
	}

	ctxTimeout, cancel := context.WithTimeout(req.Context(), 10*time.Second)
	var primero map[string]any
	err = wsjson.Read(ctxTimeout, ws, &primero)
	cancel()
	if err != nil {
		_ = ws.Close(websocket.StatusPolicyViolation, "falta hola")
		return
	}

	par, err := r.presentar(ws, primero)
	if err != nil || par == nil {
		return
	}
	defer r.despedir(par)

	ctx := context.Background()
	for {
		var crudo json.RawMessage
		if err := wsjson.Read(ctx, ws, &crudo); err != nil {
			break
		}

		if !par.GastarFicha(r.Rafaga, r.Ritmo) {
			_ = par.Enviar(ctx, protocolo.Rechazo{
				T:      "rechazo",
				Motivo: "demasiados mensajes",
			})
			_ = par.Cerrar(websocket.StatusPolicyViolation, "ritmo")
			break
		}

		var m map[string]any
		if err := json.Unmarshal(crudo, &m); err != nil {
			continue
		}

		par.mu.Lock()
		par.Visto = time.Now()
		par.mu.Unlock()

		r.despachar(par, m)
	}
}

func (r *Rele) presentar(ws *websocket.Conn, m map[string]any) (*Par, error) {
	rechazar := func(motivo string) (*Par, error) {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = wsjson.Write(ctx, ws, protocolo.Rechazo{T: "rechazo", Motivo: motivo})
		cancel()
		_ = ws.Close(websocket.StatusPolicyViolation, motivo)
		return nil, fmt.Errorf("%s", motivo)
	}

	tVal, _ := m["t"].(string)
	if tVal != "hola" {
		return rechazar("falta hola")
	}

	clave := fmt.Sprintf("%v", m["clave"])
	if !r.claveCorrecta(clave) {
		return rechazar("clave incorrecta")
	}

	enjambre := strings.ToLower(fmt.Sprintf("%v", m["enjambre"]))
	if !protocolo.EnjambreValido.MatchString(enjambre) {
		return rechazar("enjambre inválido")
	}

	rol, _ := m["rol"].(string)
	if rol != "nodo" && rol != "cliente" {
		return rechazar("rol inválido")
	}

	pedido := strings.TrimSpace(fmt.Sprintf("%v", m["id"]))
	if pedido == "<nil>" {
		pedido = ""
	}

	r.mu.Lock()
	viejo := r.pares[pedido]
	reusar := protocolo.IDValido.MatchString(pedido) && (viejo == nil || !viejo.Activo.Load())
	if reusar && viejo != nil {
		r.despedirBloqueado(viejo)
	}

	ident := pedido
	if !reusar || ident == "" {
		ident = r.tokenHex(8)
	}

	var caps []string
	if rol == "nodo" {
		if rawCaps, ok := m["capacidades"].([]any); ok {
			for _, c := range rawCaps {
				cStr := fmt.Sprintf("%v", c)
				if _, valido := protocolo.TiposDeTarea[cStr]; valido {
					caps = append(caps, cStr)
				}
			}
		}
	}

	info := make(map[string]any)
	if rawInfo, ok := m["info"].(map[string]any); ok {
		cont := 0
		for k, v := range rawInfo {
			if cont >= 12 {
				break
			}
			switch val := v.(type) {
			case string, int, int64, float64, bool:
				info[k] = val
				cont++
			}
		}
	}

	nombre := ""
	if rawNombre, ok := m["nombre"].(string); ok {
		nombre = rawNombre
		if len(nombre) > 40 {
			nombre = nombre[:40]
		}
	}

	par := NuevoPar(ident, ws, enjambre, rol, nombre, caps, info, r.Rafaga)

	mensajesSala := make([]protocolo.MensajeChat, 0)
	if sala, ok := r.salas[enjambre]; ok {
		for _, sm := range sala {
			mensajesSala = append(mensajesSala, *sm)
		}
	}
	nodosEnjambre := make([]protocolo.NodoPublico, 0)
	for _, p := range r.pares {
		if p.Enjambre == enjambre && p.Rol == "nodo" {
			nodosEnjambre = append(nodosEnjambre, p.Publico())
		}
	}
	if rol == "nodo" {
		nodosEnjambre = append(nodosEnjambre, par.Publico())
	}
	r.mu.Unlock()

	log.Printf("entra %s %s (%s) en %s %v", rol, ident, par.Nombre, enjambre, caps)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	err := par.Enviar(ctx, protocolo.Bienvenido{
		T:        "bienvenido",
		ID:       ident,
		Nodos:    nodosEnjambre,
		Mensajes: mensajesSala,
	})
	cancel()
	if err != nil {
		_ = ws.Close(websocket.StatusInternalError, "error enviando bienvenido")
		return nil, err
	}

	r.mu.Lock()
	r.pares[ident] = par
	r.mu.Unlock()

	if rol == "nodo" {
		r.avisarNodos(enjambre)
	}

	return par, nil
}

func (r *Rele) despedir(par *Par) {
	r.mu.Lock()
	r.despedirBloqueado(par)
	r.mu.Unlock()
}

func (r *Rele) despedirBloqueado(par *Par) {
	if r.pares[par.ID] != par {
		return
	}
	delete(r.pares, par.ID)
	par.Activo.Store(false)
	log.Printf("sale %s %s", par.Rol, par.ID)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	for tID, tarea := range r.tareas {
		if tarea.Nodo == par.ID {
			delete(r.tareas, tID)
			if pidio := r.pares[tarea.Solicitante]; pidio != nil {
				_ = pidio.Enviar(ctx, protocolo.DevolucionCliente{
					T:       "error",
					ID:      tarea.IDCliente,
					Nodo:    par.ID,
					Mensaje: "nodo_desconectado",
				})
			}
		} else if tarea.Solicitante == par.ID {
			delete(r.tareas, tID)
			if nodo := r.pares[tarea.Nodo]; nodo != nil {
				nodo.mu.Lock()
				if nodo.Carga > 0 {
					nodo.Carga--
				}
				nodo.mu.Unlock()
				_ = nodo.Enviar(ctx, protocolo.Cancelar{
					T:  "cancelar",
					ID: tarea.ID,
				})
			}
		}
	}

	if par.Rol == "nodo" {
		go r.avisarNodos(par.Enjambre)
	}
}

func (r *Rele) despachar(par *Par, m map[string]any) {
	tVal, _ := m["t"].(string)
	switch tVal {
	case "latido":
		if par.Rol == "nodo" {
			if cargaVal, ok := m["carga"].(float64); ok {
				nuevaCarga := int(cargaVal)
				if nuevaCarga < 0 {
					nuevaCarga = 0
				}
				par.mu.Lock()
				cambio := par.Carga != nuevaCarga
				par.Carga = nuevaCarga
				par.mu.Unlock()
				if cambio {
					r.avisarNodos(par.Enjambre)
				}
			}
		}
	case "tarea":
		r.pedirTarea(par, m)
	case "parcial", "resultado", "error":
		if par.Rol == "nodo" {
			r.devolver(par, tVal, m)
		}
	case "cancelar":
		idStr := fmt.Sprintf("%v", m["id"])
		tareaID := fmt.Sprintf("%s:%s", par.ID, idStr)

		r.mu.Lock()
		tarea, ok := r.tareas[tareaID]
		if ok {
			delete(r.tareas, tareaID)
		}
		var nodo *Par
		if ok {
			nodo = r.pares[tarea.Nodo]
		}
		r.mu.Unlock()

		if ok && nodo != nil {
			nodo.mu.Lock()
			if nodo.Carga > 0 {
				nodo.Carga--
			}
			nodo.mu.Unlock()

			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			_ = nodo.Enviar(ctx, protocolo.Cancelar{
				T:  "cancelar",
				ID: tarea.ID,
			})
			cancel()
		}
	case "mensaje":
		texto, _ := m["texto"].(string)
		r.nuevoMensaje(par, texto)
	case "responder":
		r.responderMensaje(par, m)
	}
}

func (r *Rele) pedirTarea(par *Par, m map[string]any) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	idRaw := fmt.Sprintf("%v", m["id"])
	if len(idRaw) > 48 {
		idRaw = idRaw[:48]
	}
	tipo, _ := m["tipo"].(string)

	if idRaw == "" || idRaw == "<nil>" {
		_ = par.Enviar(ctx, protocolo.DevolucionCliente{
			T:       "error",
			ID:      idRaw,
			Mensaje: "tarea_invalida",
		})
		return
	}
	if _, ok := protocolo.TiposDeTarea[tipo]; !ok {
		_ = par.Enviar(ctx, protocolo.DevolucionCliente{
			T:       "error",
			ID:      idRaw,
			Mensaje: "tarea_invalida",
		})
		return
	}

	para, _ := m["para"].(string)
	nodo := r.elegirNodo(par.Enjambre, tipo, par.ID, para)
	if nodo == nil {
		_ = par.Enviar(ctx, protocolo.DevolucionCliente{
			T:       "error",
			ID:      idRaw,
			Mensaje: "sin_nodos",
		})
		return
	}

	tID := fmt.Sprintf("%s:%s", par.ID, idRaw)
	tarea := &Tarea{
		ID:          tID,
		IDCliente:   idRaw,
		Solicitante: par.ID,
		Nodo:        nodo.ID,
		Creada:      time.Now(),
	}

	r.mu.Lock()
	r.tareas[tID] = tarea
	nodo.mu.Lock()
	nodo.Carga++
	nodo.mu.Unlock()
	r.mu.Unlock()

	_ = nodo.Enviar(ctx, protocolo.TareaNodo{
		T:     "tarea",
		ID:    tarea.ID,
		Tipo:  tipo,
		Datos: m["datos"],
		De:    par.ID,
	})

	r.avisarNodos(par.Enjambre)
}

func (r *Rele) devolver(nodo *Par, t string, m map[string]any) {
	idStr := fmt.Sprintf("%v", m["id"])

	r.mu.Lock()
	tarea, ok := r.tareas[idStr]
	if !ok || tarea.Nodo != nodo.ID {
		r.mu.Unlock()
		return
	}

	pidio := r.pares[tarea.Solicitante]

	if t != "parcial" {
		delete(r.tareas, idStr)
		nodo.mu.Lock()
		if nodo.Carga > 0 {
			nodo.Carga--
		}
		if t == "resultado" {
			nodo.Completadas++
			r.tareasAtendidas.Add(1)
		}
		nodo.mu.Unlock()
	}
	r.mu.Unlock()

	if t != "parcial" {
		r.avisarNodos(nodo.Enjambre)
	}

	if pidio != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()

		salida := protocolo.DevolucionCliente{
			T:    t,
			ID:   tarea.IDCliente,
			Nodo: nodo.ID,
		}
		if t == "error" {
			msgErr := fmt.Sprintf("%v", m["mensaje"])
			if len(msgErr) > 500 {
				msgErr = msgErr[:500]
			}
			salida.Mensaje = msgErr
		} else {
			salida.Datos = m["datos"]
		}
		_ = pidio.Enviar(ctx, salida)
	}
}

func (r *Rele) nuevoMensaje(par *Par, texto string) {
	texto = strings.TrimSpace(texto)
	if texto == "" {
		return
	}
	if len(texto) > protocolo.TextoMax {
		texto = texto[:protocolo.TextoMax]
	}

	msg := &protocolo.MensajeChat{
		ID:     r.tokenHex(6),
		Creado: time.Now().UTC().Format(time.RFC3339),
		Texto:  texto,
		De:     par.ID,
		Estado: "pendiente",
	}

	r.mu.Lock()
	sala := r.salas[par.Enjambre]
	if len(sala) >= protocolo.MensajesPorSala {
		sala = sala[1:]
	}
	r.salas[par.Enjambre] = append(sala, msg)
	r.mu.Unlock()

	r.difundir(par.Enjambre, protocolo.MensajeChatDifusion{
		T:       "mensaje",
		Mensaje: *msg,
	})
}

func (r *Rele) responderMensaje(par *Par, m map[string]any) {
	mensajeID := fmt.Sprintf("%v", m["mensaje_id"])

	r.mu.Lock()
	sala, ok := r.salas[par.Enjambre]
	if !ok {
		r.mu.Unlock()
		return
	}

	var msg *protocolo.MensajeChat
	for _, cand := range sala {
		if cand.ID == mensajeID {
			msg = cand
			break
		}
	}

	if msg == nil || msg.De != par.ID {
		r.mu.Unlock()
		return
	}

	respTexto := fmt.Sprintf("%v", m["respuesta"])
	if len(respTexto) > protocolo.TextoMax {
		respTexto = respTexto[:protocolo.TextoMax]
	}
	msg.Respuesta = respTexto

	estado, _ := m["estado"].(string)
	if estado != "error" {
		msg.Estado = "respondido"
	} else {
		msg.Estado = "error"
	}

	por := fmt.Sprintf("%v", m["por"])
	if len(por) > 48 {
		por = por[:48]
	}
	msg.Por = por

	if msVal, ok := m["ms"].(float64); ok {
		msInt := int64(msVal)
		msg.MS = &msInt
	}

	msgCopia := *msg
	r.mu.Unlock()

	r.difundir(par.Enjambre, protocolo.MensajeChatDifusion{
		T:       "mensaje",
		Mensaje: msgCopia,
	})
}

func (r *Rele) barrer(ctx context.Context) {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case ahora := <-ticker.C:
			r.limpiarVencidas(ahora)
		}
	}
}

func (r *Rele) limpiarVencidas(ahora time.Time) {
	r.mu.Lock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	for tID, tarea := range r.tareas {
		if ahora.Sub(tarea.Creada).Seconds() > r.TareaVenceS {
			delete(r.tareas, tID)
			if nodo := r.pares[tarea.Nodo]; nodo != nil {
				nodo.mu.Lock()
				if nodo.Carga > 0 {
					nodo.Carga--
				}
				nodo.mu.Unlock()
				_ = nodo.Enviar(ctx, protocolo.Cancelar{
					T:  "cancelar",
					ID: tarea.ID,
				})
			}
			if pidio := r.pares[tarea.Solicitante]; pidio != nil {
				_ = pidio.Enviar(ctx, protocolo.DevolucionCliente{
					T:       "error",
					ID:      tarea.IDCliente,
					Nodo:    tarea.Nodo,
					Mensaje: "vencida",
				})
			}
		}
	}

	var sinLatido []*Par
	for _, p := range r.pares {
		p.mu.Lock()
		visto := p.Visto
		p.mu.Unlock()
		if ahora.Sub(visto).Seconds() > r.LatidoVenceS {
			sinLatido = append(sinLatido, p)
		}
	}
	r.mu.Unlock()

	for _, p := range sinLatido {
		_ = p.Cerrar(websocket.StatusGoingAway, "sin latido")
	}
}

// Iniciar arranca el servidor HTTP y WebSocket del relé.
func (r *Rele) Iniciar(ctx context.Context, listo chan struct{}) error {
	addr := fmt.Sprintf("0.0.0.0:%d", r.Puerto)
	srv := &http.Server{
		Addr:    addr,
		Handler: r,
	}

	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	defer ln.Close()

	if tcpLn, ok := ln.(*net.TCPListener); ok {
		r.Puerto = tcpLn.Addr().(*net.TCPAddr).Port
	}

	log.Printf("relé escuchando en :%d", r.Puerto)
	if listo != nil {
		close(listo)
	}

	go r.barrer(ctx)

	go func() {
		<-ctx.Done()
		_ = srv.Shutdown(context.Background())
		r.mu.Lock()
		for _, o := range r.ofertas {
			o.Cerrar()
		}
		for _, p := range r.pares {
			_ = p.Cerrar(websocket.StatusNormalClosure, "servidor apagado")
		}
		r.mu.Unlock()
	}()

	err = srv.Serve(ln)
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}
