package senalar

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

type MensajeSenal struct {
	De    string `json:"de"`
	Para  string `json:"para,omitempty"`
	Datos any    `json:"datos"`
}

type NodoInfo struct {
	ID          string         `json:"id"`
	Nombre      string         `json:"nombre"`
	Capacidades []string       `json:"capacidades"`
	Info        map[string]any `json:"info"`
	Carga       int            `json:"carga"`
	Completadas int            `json:"completadas"`
}

type Cliente struct {
	URL         string
	Clave       string
	Enjambre    string
	Rol         string
	ID          string
	Nombre      string
	Info        map[string]any
	Capacidades []string

	conn        *websocket.Conn
	mu          sync.RWMutex
	nodos       []NodoInfo
	canalSenal  chan MensajeSenal
	cerrar      chan struct{}
	cancelLoop  context.CancelFunc
}

func NuevoCliente(url, clave, enjambre, rol, id, nombre string) *Cliente {
	if enjambre == "" {
		enjambre = "compushare"
	}
	if rol == "" {
		rol = "cliente"
	}
	return &Cliente{
		URL:         url,
		Clave:       clave,
		Enjambre:    enjambre,
		Rol:         rol,
		ID:          id,
		Nombre:      nombre,
		canalSenal:  make(chan MensajeSenal, 64),
		cerrar:      make(chan struct{}),
		Info:        make(map[string]any),
		Capacidades: []string{},
	}
}

func (c *Cliente) Conectar(ctx context.Context) error {
	opts := &websocket.DialOptions{
		CompressionMode: websocket.CompressionDisabled,
	}
	conn, _, err := websocket.Dial(ctx, c.URL, opts)
	if err != nil {
		return fmt.Errorf("error conectando websocket al rele %s: %w", c.URL, err)
	}
	conn.SetReadLimit(8 * 1024 * 1024)
	c.conn = conn

	hola := map[string]any{
		"t":           "hola",
		"clave":       c.Clave,
		"enjambre":    c.Enjambre,
		"rol":         c.Rol,
		"nombre":      c.Nombre,
		"info":        c.Info,
		"capacidades": c.Capacidades,
	}
	if c.ID != "" {
		hola["id"] = c.ID
	}

	if err := wsjson.Write(ctx, conn, hola); err != nil {
		conn.Close(websocket.StatusInternalError, "error envio hola")
		return fmt.Errorf("error enviando mensaje hola: %w", err)
	}

	var resp map[string]any
	if err := wsjson.Read(ctx, conn, &resp); err != nil {
		conn.Close(websocket.StatusInternalError, "error respuesta hola")
		return fmt.Errorf("error leyendo respuesta hola: %w", err)
	}

	if resp["t"] == "rechazo" {
		conn.Close(websocket.StatusPolicyViolation, fmt.Sprint(resp["motivo"]))
		return fmt.Errorf("conexion rechazada por el rele: %v", resp["motivo"])
	}
	if resp["t"] != "bienvenido" {
		conn.Close(websocket.StatusProtocolError, "respuesta inesperada")
		return fmt.Errorf("respuesta inesperada del rele: %v", resp)
	}

	if id, ok := resp["id"].(string); ok {
		c.ID = id
	}

	c.actualizarNodos(resp["nodos"])

	loopCtx, cancel := context.WithCancel(context.Background())
	c.cancelLoop = cancel
	go c.bucleLectura(loopCtx)
	go c.bucleLatidos(loopCtx)

	return nil
}

func (c *Cliente) actualizarNodos(crudo any) {
	lista, ok := crudo.([]any)
	if !ok {
		return
	}
	var nuevos []NodoInfo
	for _, item := range lista {
		if b, err := json.Marshal(item); err == nil {
			var n NodoInfo
			if err := json.Unmarshal(b, &n); err == nil {
				nuevos = append(nuevos, n)
			}
		}
	}
	c.mu.Lock()
	c.nodos = nuevos
	c.mu.Unlock()
}

func (c *Cliente) Nodos() []NodoInfo {
	c.mu.RLock()
	defer c.mu.RUnlock()
	res := make([]NodoInfo, len(c.nodos))
	copy(res, c.nodos)
	return res
}

func (c *Cliente) CanalSenales() <-chan MensajeSenal {
	return c.canalSenal
}

func (c *Cliente) EnviarSenal(ctx context.Context, para string, datos any) error {
	c.mu.RLock()
	conn := c.conn
	c.mu.RUnlock()
	if conn == nil {
		return fmt.Errorf("no conectado al rele")
	}

	m := map[string]any{
		"t":     "senalar",
		"para":  para,
		"datos": datos,
	}
	return wsjson.Write(ctx, conn, m)
}

func (c *Cliente) bucleLectura(ctx context.Context) {
	for {
		var m map[string]any
		err := wsjson.Read(ctx, c.conn, &m)
		if err != nil {
			return
		}
		tipo, _ := m["t"].(string)
		switch tipo {
		case "senalar":
			de, _ := m["de"].(string)
			para, _ := m["para"].(string)
			select {
			case c.canalSenal <- MensajeSenal{De: de, Para: para, Datos: m["datos"]}:
			default:
			}
		case "nodos":
			c.actualizarNodos(m["nodos"])
		}
	}
}

func (c *Cliente) bucleLatidos(ctx context.Context) {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.mu.RLock()
			conn := c.conn
			c.mu.RUnlock()
			if conn == nil {
				return
			}
			latidoCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			_ = wsjson.Write(latidoCtx, conn, map[string]any{"t": "latido"})
			cancel()
		}
	}
}

func (c *Cliente) Cerrar() error {
	if c.cancelLoop != nil {
		c.cancelLoop()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != nil {
		return c.conn.Close(websocket.StatusNormalClosure, "cerrando")
	}
	return nil
}

// SesionTuboRele gestiona el fallback de tubo al relé (/tubo)
type SesionTuboRele struct {
	URL          string
	Clave        string
	Nombre       string
	HostDestino  string
	PuertoDestino int
	PuertoRemoto int

	cancel context.CancelFunc
	listo  chan struct{}
}

func ArrancarTuboReleTrabajador(ctx context.Context, urlBase, clave, nombre, hostDestino string, puertoDestino int) (*SesionTuboRele, error) {
	urlTubo := strings.TrimRight(urlBase, "/") + "/tubo"
	opts := &websocket.DialOptions{
		CompressionMode: websocket.CompressionDisabled,
	}
	control, _, err := websocket.Dial(ctx, urlTubo, opts)
	if err != nil {
		return nil, fmt.Errorf("error conectando control de tubo a %s: %w", urlTubo, err)
	}
	control.SetReadLimit(8 * 1024 * 1024)

	ofrezco := map[string]any{
		"t":      "ofrezco",
		"clave":  clave,
		"nombre": nombre,
	}
	if err := wsjson.Write(ctx, control, ofrezco); err != nil {
		control.Close(websocket.StatusInternalError, "error ofrezco")
		return nil, fmt.Errorf("error enviando ofrezco: %w", err)
	}

	var resp map[string]any
	if err := wsjson.Read(ctx, control, &resp); err != nil {
		control.Close(websocket.StatusInternalError, "error respuesta")
		return nil, fmt.Errorf("error leyendo respuesta tubo: %w", err)
	}

	if resp["t"] != "listo" {
		control.Close(websocket.StatusProtocolError, "no listo")
		return nil, fmt.Errorf("el rele no acepto el tubo: %v", resp)
	}

	puertoRemoto := int(resp["puerto"].(float64))
	sesionCtx, cancel := context.WithCancel(context.Background())

	sesion := &SesionTuboRele{
		URL:           urlTubo,
		Clave:         clave,
		Nombre:        nombre,
		HostDestino:   hostDestino,
		PuertoDestino: puertoDestino,
		PuertoRemoto:  puertoRemoto,
		cancel:        cancel,
		listo:         make(chan struct{}),
	}
	close(sesion.listo)

	go func() {
		defer control.Close(websocket.StatusNormalClosure, "cerrando control")
		for {
			var m map[string]any
			if err := wsjson.Read(sesionCtx, control, &m); err != nil {
				return
			}
			if m["t"] == "abre" {
				id, _ := m["id"].(string)
				go sesion.conectarDatos(sesionCtx, id)
			}
		}
	}()

	return sesion, nil
}

func (s *SesionTuboRele) conectarDatos(ctx context.Context, id string) {
	destAddr := net.JoinHostPort(s.HostDestino, fmt.Sprintf("%d", s.PuertoDestino))
	tcpConn, err := net.DialTimeout("tcp", destAddr, 3*time.Second)
	if err != nil {
		return
	}
	defer tcpConn.Close()

	opts := &websocket.DialOptions{
		CompressionMode: websocket.CompressionDisabled,
	}
	wsConn, _, err := websocket.Dial(ctx, s.URL, opts)
	if err != nil {
		return
	}
	wsConn.SetReadLimit(64 * 1024 * 1024)
	defer wsConn.Close(websocket.StatusNormalClosure, "fin datos")

	datosMsg := map[string]any{
		"t":     "datos",
		"clave": s.Clave,
		"id":    id,
	}
	if err := wsjson.Write(ctx, wsConn, datosMsg); err != nil {
		return
	}

	puenteBidireccional(ctx, wsConn, tcpConn)
}

func puenteBidireccional(ctx context.Context, wsConn *websocket.Conn, tcpConn net.Conn) {
	var wg sync.WaitGroup
	wg.Add(2)

	// TCP -> WS
	go func() {
		defer wg.Done()
		buf := make([]byte, 64*1024)
		for {
			n, err := tcpConn.Read(buf)
			if n > 0 {
				writeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
				wErr := wsConn.Write(writeCtx, websocket.MessageBinary, buf[:n])
				cancel()
				if wErr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()

	// WS -> TCP
	go func() {
		defer wg.Done()
		for {
			msgType, data, err := wsConn.Read(ctx)
			if err != nil {
				return
			}
			if msgType == websocket.MessageBinary && len(data) > 0 {
				if _, wErr := tcpConn.Write(data); wErr != nil {
					return
				}
			}
		}
	}()

	wg.Wait()
}

func (s *SesionTuboRele) Cerrar() {
	if s.cancel != nil {
		s.cancel()
	}
}
