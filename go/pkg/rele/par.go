package rele

import (
	"context"
	"encoding/json"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/protocolo"
)

type Par struct {
	ID          string
	WS          *websocket.Conn
	Enjambre    string
	Rol         string
	Nombre      string
	Capacidades map[string]struct{}
	Info        map[string]any
	Carga       int
	Completadas int
	Visto       time.Time
	Fichas      float64
	Rellenado   time.Time

	Activo atomic.Bool
	mu     sync.Mutex
}

func NuevoPar(id string, ws *websocket.Conn, enjambre, rol, nombre string, caps []string, info map[string]any, rafaga float64) *Par {
	capacidades := make(map[string]struct{}, len(caps))
	for _, c := range caps {
		if _, ok := protocolo.TiposDeTarea[c]; ok {
			capacidades[c] = struct{}{}
		}
	}
	if info == nil {
		info = make(map[string]any)
	}
	ahora := time.Now()
	p := &Par{
		ID:          id,
		WS:          ws,
		Enjambre:    enjambre,
		Rol:         rol,
		Nombre:      nombre,
		Capacidades: capacidades,
		Info:        info,
		Visto:       ahora,
		Fichas:      rafaga,
		Rellenado:   ahora,
	}
	p.Activo.Store(true)
	return p
}

func (p *Par) GastarFicha(rafaga, ritmo float64) bool {
	p.mu.Lock()
	defer p.mu.Unlock()

	ahora := time.Now()
	delta := ahora.Sub(p.Rellenado).Seconds()
	p.Fichas = p.Fichas + delta*ritmo
	if p.Fichas > rafaga {
		p.Fichas = rafaga
	}
	p.Rellenado = ahora

	if p.Fichas < 1.0 {
		return false
	}
	p.Fichas -= 1.0
	return true
}

func (p *Par) Enviar(ctx context.Context, msg any) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.WS.Write(ctx, websocket.MessageText, data)
}

func (p *Par) Cerrar(code websocket.StatusCode, motivo string) error {
	p.Activo.Store(false)
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.WS.Close(code, motivo)
}

func (p *Par) Publico() protocolo.NodoPublico {
	p.mu.Lock()
	defer p.mu.Unlock()

	caps := make([]string, 0, len(p.Capacidades))
	for c := range p.Capacidades {
		caps = append(caps, c)
	}
	sort.Strings(caps)

	infoCopy := make(map[string]any, len(p.Info))
	for k, v := range p.Info {
		infoCopy[k] = v
	}

	return protocolo.NodoPublico{
		ID:          p.ID,
		Nombre:      p.Nombre,
		Capacidades: caps,
		Info:        infoCopy,
		Carga:       p.Carga,
		Completadas: p.Completadas,
	}
}
