package rele

import (
	"net"
	"sync"
	"sync/atomic"

	"github.com/coder/websocket"
)

type CanalizadoResult struct {
	WS        *websocket.Conn
	Terminado chan struct{}
}

type Oferta struct {
	Nombre    string
	Control   *websocket.Conn
	ControlMu sync.Mutex
	Puerto    int
	Listener  net.Listener
	Abiertos  atomic.Int64
	Esperando map[string]chan CanalizadoResult
	EspMu     sync.Mutex
	Info      map[string]any
}

func NuevaOferta(nombre string, control *websocket.Conn, puerto int, listener net.Listener, info map[string]any) *Oferta {
	if info == nil {
		info = make(map[string]any)
	}
	return &Oferta{
		Nombre:    nombre,
		Control:   control,
		Puerto:    puerto,
		Listener:  listener,
		Esperando: make(map[string]chan CanalizadoResult),
		Info:      info,
	}
}

func (o *Oferta) Cerrar() {
	if o.Listener != nil {
		_ = o.Listener.Close()
	}
	o.EspMu.Lock()
	for _, ch := range o.Esperando {
		close(ch)
	}
	o.Esperando = make(map[string]chan CanalizadoResult)
	o.EspMu.Unlock()
}
