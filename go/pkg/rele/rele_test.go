package rele_test

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/protocolo"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/rele"
)

func iniciarReleTest(t *testing.T, clave string) (*rele.Rele, context.CancelFunc) {
	ctx, cancel := context.WithCancel(context.Background())
	r := rele.Nuevo(clave, 0)
	listo := make(chan struct{})
	go func() {
		_ = r.Iniciar(ctx, listo)
	}()
	select {
	case <-listo:
	case <-time.After(5 * time.Second):
		cancel()
		t.Fatal("timeout iniciando relé de prueba")
	}
	return r, cancel
}

func TestSalud(t *testing.T) {
	clave := "clave-test-salud"
	r, cancel := iniciarReleTest(t, clave)
	defer cancel()

	resp, err := http.Get(fmt.Sprintf("http://127.0.0.1:%d/salud", r.Puerto))
	if err != nil {
		t.Fatalf("error en GET /salud: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("código de estado esperado 200, obtenido %d", resp.StatusCode)
	}

	cuerpo, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("error leyendo cuerpo: %v", err)
	}

	var res map[string]any
	if err := json.Unmarshal(cuerpo, &res); err != nil {
		t.Fatalf("error parseando JSON de salud: %v", err)
	}

	if res["ok"] != true {
		t.Fatalf("resumen no indica ok: %v", res)
	}
}

func TestClaveIncorrecta(t *testing.T) {
	clave := "clave-correcta"
	r, cancel := iniciarReleTest(t, clave)
	defer cancel()

	ctx, cancelCtx := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelCtx()

	ws, _, err := websocket.Dial(ctx, fmt.Sprintf("ws://127.0.0.1:%d", r.Puerto), nil)
	if err != nil {
		t.Fatalf("error conectando websocket: %v", err)
	}
	defer ws.Close(websocket.StatusNormalClosure, "")

	hola := protocolo.Hola{
		T:        "hola",
		Clave:    "clave-mala",
		Enjambre: "pruebas",
		Rol:      "cliente",
	}
	if err := wsjson.Write(ctx, ws, hola); err != nil {
		t.Fatalf("error enviando hola: %v", err)
	}

	var m protocolo.Rechazo
	err = wsjson.Read(ctx, ws, &m)
	if err == nil {
		if m.T != "rechazo" || m.Motivo != "clave incorrecta" {
			t.Fatalf("respuesta de rechazo inesperada: %+v", m)
		}
	}
}

func TestTareaEcoDePuntaAPunta(t *testing.T) {
	clave := "clave-eco-test"
	r, cancel := iniciarReleTest(t, clave)
	defer cancel()

	ctx, cancelCtx := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancelCtx()

	releURL := fmt.Sprintf("ws://127.0.0.1:%d", r.Puerto)

	// 1. Conectar nodo
	wsNodo, _, err := websocket.Dial(ctx, releURL, nil)
	if err != nil {
		t.Fatalf("error conectando nodo: %v", err)
	}
	defer wsNodo.Close(websocket.StatusNormalClosure, "")

	holaNodo := protocolo.Hola{
		T:           "hola",
		Clave:       clave,
		Enjambre:    "pruebas",
		Rol:         "nodo",
		Nombre:      "nodo-1",
		Capacidades: []string{"eco"},
	}
	if err := wsjson.Write(ctx, wsNodo, holaNodo); err != nil {
		t.Fatalf("error enviando hola de nodo: %v", err)
	}
	var bienvenidoNodo protocolo.Bienvenido
	if err := wsjson.Read(ctx, wsNodo, &bienvenidoNodo); err != nil {
		t.Fatalf("error esperando bienvenido de nodo: %v", err)
	}

	// 2. Conectar cliente
	wsCliente, _, err := websocket.Dial(ctx, releURL, nil)
	if err != nil {
		t.Fatalf("error conectando cliente: %v", err)
	}
	defer wsCliente.Close(websocket.StatusNormalClosure, "")

	holaCliente := protocolo.Hola{
		T:        "hola",
		Clave:    clave,
		Enjambre: "pruebas",
		Rol:      "cliente",
	}
	if err := wsjson.Write(ctx, wsCliente, holaCliente); err != nil {
		t.Fatalf("error enviando hola de cliente: %v", err)
	}
	var bienvenidoCliente protocolo.Bienvenido
	if err := wsjson.Read(ctx, wsCliente, &bienvenidoCliente); err != nil {
		t.Fatalf("error esperando bienvenido de cliente: %v", err)
	}

	// 3. Cliente pide tarea eco
	tarea := protocolo.TareaCliente{
		T:     "tarea",
		ID:    "t-001",
		Tipo:  "eco",
		Datos: map[string]any{"mensaje": "hola mundo"},
	}
	if err := wsjson.Write(ctx, wsCliente, tarea); err != nil {
		t.Fatalf("error pidiendo tarea: %v", err)
	}

	// 4. Nodo recibe tarea (descartando difusión de nodos previa si la hubiera)
	var tareaRecibida protocolo.TareaNodo
	for {
		var crudo json.RawMessage
		if err := wsjson.Read(ctx, wsNodo, &crudo); err != nil {
			t.Fatalf("error leyendo en nodo: %v", err)
		}
		var base protocolo.MensajeBase
		_ = json.Unmarshal(crudo, &base)
		if base.T == "tarea" {
			if err := json.Unmarshal(crudo, &tareaRecibida); err != nil {
				t.Fatalf("error deserializando tarea: %v", err)
			}
			break
		}
	}
	if tareaRecibida.Tipo != "eco" {
		t.Fatalf("tipo de tarea inesperado: %s", tareaRecibida.Tipo)
	}

	// 5. Nodo responde resultado
	respNodo := protocolo.DevolucionNodo{
		T:     "resultado",
		ID:    tareaRecibida.ID,
		Datos: tareaRecibida.Datos,
	}
	if err := wsjson.Write(ctx, wsNodo, respNodo); err != nil {
		t.Fatalf("error enviando resultado desde nodo: %v", err)
	}

	// 6. Cliente recibe resultado
	var resCliente protocolo.DevolucionCliente
	for {
		var crudo json.RawMessage
		if err := wsjson.Read(ctx, wsCliente, &crudo); err != nil {
			t.Fatalf("error recibiendo resultado en cliente: %v", err)
		}
		var base protocolo.MensajeBase
		_ = json.Unmarshal(crudo, &base)
		if base.T == "resultado" || base.T == "error" {
			if err := json.Unmarshal(crudo, &resCliente); err != nil {
				t.Fatalf("error deserializando resultado cliente: %v", err)
			}
			break
		}
	}
	if resCliente.T != "resultado" || resCliente.ID != "t-001" {
		t.Fatalf("resultado de cliente inesperado: %+v", resCliente)
	}
}
