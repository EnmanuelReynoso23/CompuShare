package tubo

import (
	"context"
	"fmt"
	"log"
	"net"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/descubrir"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/protocolo"
)

type Tubo struct {
	Rele         string
	Clave        string
	Nombre       string
	Puerto       int
	Host         string
	Info         map[string]any
	PuertoRemoto int
	Bytes        atomic.Int64
	Conexiones   atomic.Int64

	mu    sync.Mutex
	listo chan struct{}
}

func New(rele, clave, nombre string, puerto int, host string, info map[string]any) *Tubo {
	if host == "" {
		host = "127.0.0.1"
	}
	if info == nil {
		info = make(map[string]any)
	}
	return &Tubo{
		Rele:   rele,
		Clave:  clave,
		Nombre: nombre,
		Puerto: puerto,
		Host:   host,
		Info:   info,
		listo:  make(chan struct{}),
	}
}

// CanalListo devuelve un canal que se cierra cuando el relé asigna puerto al tubo.
func (t *Tubo) CanalListo() <-chan struct{} {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.listo
}

func (t *Tubo) marcarListo(puerto int) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.PuertoRemoto = puerto
	select {
	case <-t.listo:
	default:
		close(t.listo)
	}
}

func (t *Tubo) reiniciarListo() {
	t.mu.Lock()
	defer t.mu.Unlock()
	select {
	case <-t.listo:
		t.listo = make(chan struct{})
	default:
	}
}

func (t *Tubo) urlRelé() (string, error) {
	url, err := descubrir.Rele(t.Rele, t.Clave, 600)
	if err != nil {
		return "", err
	}
	return strings.TrimRight(url, "/") + "/tubo", nil
}

// Vivir mantiene el tubo conectado al relé con reconexión automática.
func (t *Tubo) Vivir(ctx context.Context) error {
	espera := 1.0
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		err := t.unaSesion(ctx)
		if err != nil {
			log.Printf("tubo caído (%v); reintento en %.0f s", err, espera)
		} else {
			espera = 1.0
		}

		t.reiniciarListo()

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(espera * float64(time.Second))):
		}

		espera = espera * 2.0
		if espera > 30.0 {
			espera = 30.0
		}
	}
}

func (t *Tubo) unaSesion(ctx context.Context) error {
	url, err := t.urlRelé()
	if err != nil {
		return err
	}

	dialOpts := &websocket.DialOptions{
		CompressionMode: websocket.CompressionDisabled,
	}

	c, _, err := websocket.Dial(ctx, url, dialOpts)
	if err != nil {
		return err
	}
	defer c.Close(websocket.StatusNormalClosure, "")
	c.SetReadLimit(protocolo.MaxMensajeBytes)

	ofrezco := protocolo.Ofrezco{
		T:      "ofrezco",
		Clave:  t.Clave,
		Nombre: t.Nombre,
		Info:   t.Info,
	}
	if err := wsjson.Write(ctx, c, ofrezco); err != nil {
		return err
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 20*time.Second)
	var hola protocolo.Listo
	err = wsjson.Read(ctxTimeout, c, &hola)
	cancel()
	if err != nil {
		return fmt.Errorf("timeout o error esperando respuesta listo: %w", err)
	}
	if hola.T != "listo" {
		return fmt.Errorf("el relé no aceptó el tubo: %v", hola)
	}

	t.marcarListo(hola.Puerto)
	log.Printf("tubo %s -> %s:%d listo (en el relé: 127.0.0.1:%d)", t.Nombre, t.Host, t.Puerto, t.PuertoRemoto)

	for {
		var m protocolo.Abre
		if err := wsjson.Read(ctx, c, &m); err != nil {
			return err
		}
		if m.T == "abre" && m.ID != "" {
			go t.unir(ctx, url, m.ID)
		}
	}
}

func (t *Tubo) unir(ctx context.Context, url string, ident string) {
	destino := net.JoinHostPort(t.Host, strconv.Itoa(t.Puerto))
	tcpConn, err := net.Dial("tcp", destino)
	if err != nil {
		log.Printf("no hay nada escuchando en %s (%v)", destino, err)
		return
	}
	if tc, ok := tcpConn.(*net.TCPConn); ok {
		_ = tc.SetNoDelay(true)
	}
	defer tcpConn.Close()

	t.Conexiones.Add(1)

	dialOpts := &websocket.DialOptions{
		CompressionMode: websocket.CompressionDisabled,
	}
	ws, _, err := websocket.Dial(ctx, url, dialOpts)
	if err != nil {
		log.Printf("error conectando websocket de datos para tubo %s: %v", ident, err)
		return
	}
	defer ws.Close(websocket.StatusNormalClosure, "")
	ws.SetReadLimit(protocolo.MaxMensajeBytes)

	datos := protocolo.Datos{
		T:     "datos",
		Clave: t.Clave,
		ID:    ident,
	}
	if err := wsjson.Write(ctx, ws, datos); err != nil {
		log.Printf("error enviando datos para tubo %s: %v", ident, err)
		return
	}

	Canalizar(ctx, ws, tcpConn, &t.Bytes)
}
