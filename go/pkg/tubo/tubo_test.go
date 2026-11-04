package tubo_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"fmt"
	"io"
	"net"
	"testing"
	"time"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/rele"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/tubo"
)

func iniciarEcoTCP(t *testing.T) (net.Listener, int) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("error abriendo listener eco: %v", err)
	}

	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				buf := make([]byte, 64*1024)
				for {
					n, err := c.Read(buf)
					if n > 0 {
						_, werr := c.Write(buf[:n])
						if werr != nil {
							break
						}
					}
					if err != nil {
						break
					}
				}
			}(conn)
		}
	}()

	puerto := ln.Addr().(*net.TCPAddr).Port
	return ln, puerto
}

func TestTuboConEcoTCP(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ecoLn, ecoPuerto := iniciarEcoTCP(t)
	defer ecoLn.Close()

	clave := "clave-test-tubo"
	r := rele.Nuevo(clave, 0)
	listoRele := make(chan struct{})
	go func() {
		if err := r.Iniciar(ctx, listoRele); err != nil {
			t.Logf("relé finalizado: %v", err)
		}
	}()

	select {
	case <-listoRele:
	case <-time.After(5 * time.Second):
		t.Fatal("timeout esperando arranque del relé")
	}

	releURL := fmt.Sprintf("ws://127.0.0.1:%d", r.Puerto)
	tb := tubo.New(releURL, clave, "trabajador-test", ecoPuerto, "127.0.0.1", map[string]any{"vram_gb": 4.0})

	go func() {
		if err := tb.Vivir(ctx); err != nil && err != context.Canceled {
			t.Logf("tubo finalizado con error: %v", err)
		}
	}()

	select {
	case <-tb.CanalListo():
	case <-time.After(5 * time.Second):
		t.Fatal("timeout esperando que el tubo esté listo")
	}

	if tb.PuertoRemoto <= 0 {
		t.Fatalf("puerto remoto inválido: %d", tb.PuertoRemoto)
	}

	conn, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", tb.PuertoRemoto))
	if err != nil {
		t.Fatalf("error conectando al puerto del tubo: %v", err)
	}
	defer conn.Close()

	carga := make([]byte, 5*1024*1024)
	if _, err := io.ReadFull(rand.Reader, carga); err != nil {
		t.Fatalf("error generando carga: %v", err)
	}

	errCh := make(chan error, 1)
	vuelta := make([]byte, len(carga))

	go func() {
		_, err := io.ReadFull(conn, vuelta)
		errCh <- err
	}()

	if _, err := conn.Write(carga); err != nil {
		t.Fatalf("error enviando carga: %v", err)
	}

	select {
	case err := <-errCh:
		if err != nil {
			t.Fatalf("error leyendo respuesta eco: %v", err)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("timeout esperando lectura de 5 MB por el tubo")
	}

	if !bytes.Equal(carga, vuelta) {
		t.Fatal("los datos que volvieron del tubo no coinciden con la carga enviada")
	}

	if tb.Bytes.Load() == 0 {
		t.Fatal("el contador de bytes del tubo debería ser mayor que 0")
	}
}
