package senalar

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

func TestClienteSenalizacion(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	var mu syncMutex
	conexiones := make(map[string]*websocket.Conn)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close(websocket.StatusNormalClosure, "")

		var hola map[string]any
		if err := wsjson.Read(ctx, conn, &hola); err != nil {
			return
		}

		id := fmt.Sprintf("%v", hola["id"])
		if id == "" || id == "<nil>" {
			id = "peer-test"
		}

		mu.Lock()
		conexiones[id] = conn
		mu.Unlock()

		_ = wsjson.Write(ctx, conn, map[string]any{
			"t":     "bienvenido",
			"id":    id,
			"nodos": []any{},
		})

		for {
			var m map[string]any
			if err := wsjson.Read(ctx, conn, &m); err != nil {
				return
			}
			if m["t"] == "senalar" {
				para := fmt.Sprintf("%v", m["para"])
				mu.Lock()
				dest := conexiones[para]
				mu.Unlock()
				if dest != nil {
					_ = wsjson.Write(ctx, dest, map[string]any{
						"t":     "senalar",
						"de":    id,
						"datos": m["datos"],
					})
				}
			}
		}
	}))
	defer srv.Close()

	wsURL := "ws" + srv.URL[4:]

	c1 := NuevoCliente(wsURL, "clave-test", "test", "cliente", "peer-1", "Nodo1")
	if err := c1.Conectar(ctx); err != nil {
		t.Fatalf("c1 conectar: %v", err)
	}
	defer c1.Cerrar()

	c2 := NuevoCliente(wsURL, "clave-test", "test", "nodo", "peer-2", "Nodo2")
	if err := c2.Conectar(ctx); err != nil {
		t.Fatalf("c2 conectar: %v", err)
	}
	defer c2.Cerrar()

	// C1 envia señal a C2
	datos := map[string]any{"ice": "candidate1"}
	if err := c1.EnviarSenal(ctx, "peer-2", datos); err != nil {
		t.Fatalf("c1 enviar señal: %v", err)
	}

	select {
	case s := <-c2.CanalSenales():
		if s.De != "peer-1" {
			t.Errorf("de esperado peer-1, obtenido %s", s.De)
		}
		b, _ := json.Marshal(s.Datos)
		if string(b) != `{"ice":"candidate1"}` {
			t.Errorf("datos inesperados: %s", string(b))
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timeout esperando señal en c2")
	}
}

type syncMutex struct {
	m map[string]*websocket.Conn
}

func (s *syncMutex) Lock()   {}
func (s *syncMutex) Unlock() {}

func TestTuboReleTrabajador(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Levantar servicio TCP de eco
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen tcp: %v", err)
	}
	defer ln.Close()
	_, ecoPortStr, _ := net.SplitHostPort(ln.Addr().String())
	var ecoPort int
	fmt.Sscanf(ecoPortStr, "%d", &ecoPort)

	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				_, _ = io.Copy(c, c)
			}(conn)
		}
	}()

	// Simular servidor relé con endpoint /tubo
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close(websocket.StatusNormalClosure, "")

		var primero map[string]any
		if err := wsjson.Read(ctx, conn, &primero); err != nil {
			return
		}

		if primero["t"] == "ofrezco" {
			_ = wsjson.Write(ctx, conn, map[string]any{
				"t":      "listo",
				"puerto": 50100,
			})

			// Pedir abrir conexión con id "tubo-1"
			_ = wsjson.Write(ctx, conn, map[string]any{
				"t":  "abre",
				"id": "tubo-1",
			})

			// Mantener vivo
			for {
				var dummy map[string]any
				if err := wsjson.Read(ctx, conn, &dummy); err != nil {
					return
				}
			}
		} else if primero["t"] == "datos" {
			// Enviar y recibir eco sobre WebSocket binario
			payload := []byte("ECO_SOBRE_TUBO_RELE")
			if err := conn.Write(ctx, websocket.MessageBinary, payload); err != nil {
				return
			}
			_, data, err := conn.Read(ctx)
			if err != nil || string(data) != string(payload) {
				return
			}
		}
	}))
	defer srv.Close()

	wsURL := "ws" + srv.URL[4:]
	sesion, err := ArrancarTuboReleTrabajador(ctx, wsURL, "clave", "trabajador-test", "127.0.0.1", ecoPort)
	if err != nil {
		t.Fatalf("ArrancarTuboReleTrabajador: %v", err)
	}
	defer sesion.Cerrar()

	if sesion.PuertoRemoto != 50100 {
		t.Errorf("puerto remoto esperado 50100, obtenido %d", sesion.PuertoRemoto)
	}
}
