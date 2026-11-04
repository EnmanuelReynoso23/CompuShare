package p2p

import (
	"bytes"
	"context"
	"crypto/rand"
	"io"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
)

func TestPuenteIntegridadTCP(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// 1. Servicio de eco TCP de destino (simulando ggml-rpc-server)
	lnEco, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen eco: %v", err)
	}
	defer lnEco.Close()
	ecoAddr := lnEco.Addr().String()

	go func() {
		for {
			c, err := lnEco.Accept()
			if err != nil {
				return
			}
			go func(conn net.Conn) {
				defer conn.Close()
				_, _ = io.Copy(conn, conn)
			}(c)
		}
	}()

	// 2. Establecer canal P2P entre A (Maestro) y B (Trabajador)
	canalA, err := NuevoCanalP2P(nil, true)
	if err != nil {
		t.Fatalf("canalA: %v", err)
	}
	defer canalA.Cerrar()

	canalB, err := NuevoCanalP2P(nil, true)
	if err != nil {
		t.Fatalf("canalB: %v", err)
	}
	defer canalB.Cerrar()

	var mu sync.Mutex
	_ = canalA.Agent.OnCandidate(func(c ice.Candidate) {
		if c != nil {
			mu.Lock()
			_ = canalB.Agent.AddRemoteCandidate(c)
			mu.Unlock()
		}
	})
	_ = canalB.Agent.OnCandidate(func(c ice.Candidate) {
		if c != nil {
			mu.Lock()
			_ = canalA.Agent.AddRemoteCandidate(c)
			mu.Unlock()
		}
	})

	paramsA, err := canalA.ObtenerParametrosLocales(ctx, "oferta")
	if err != nil {
		t.Fatalf("paramsA: %v", err)
	}
	paramsB, err := canalB.ObtenerParametrosLocales(ctx, "respuesta")
	if err != nil {
		t.Fatalf("paramsB: %v", err)
	}

	errChan := make(chan error, 2)
	go func() {
		errChan <- canalB.Establecer(ctx, paramsA, false) // Trabajador (servidor QUIC)
	}()
	go func() {
		errChan <- canalA.Establecer(ctx, paramsB, true) // Maestro (cliente QUIC)
	}()

	for i := 0; i < 2; i++ {
		if err := <-errChan; err != nil {
			t.Fatalf("establecer canal err: %v", err)
		}
	}

	// 3. Iniciar puentes
	var met TrabajadorMetricas
	go func() {
		_ = IniciarPuenteTrabajador(ctx, ecoAddr, canalB, nil)
	}()

	lnMaestro, err := IniciarPuenteMaestro(ctx, "127.0.0.1:0", canalA, nil)
	if err != nil {
		t.Fatalf("puente maestro: %v", err)
	}
	defer lnMaestro.Close()
	maestroAddr := lnMaestro.Addr().String()

	// 4. Cliente conecta a maestroAddr y envía 1 MB
	cliConn, err := net.DialTimeout("tcp", maestroAddr, 3*time.Second)
	if err != nil {
		t.Fatalf("dial maestro: %v", err)
	}
	defer cliConn.Close()

	volumen := 1024 * 1024 // 1 MB
	carga := make([]byte, volumen)
	_, _ = rand.Read(carga)

	go func() {
		_, _ = cliConn.Write(carga)
	}()

	recibido := make([]byte, volumen)
	if _, err := io.ReadFull(cliConn, recibido); err != nil {
		t.Fatalf("read full: %v", err)
	}

	if !bytes.Equal(carga, recibido) {
		t.Fatalf("fallo de integridad: los datos recibidos difieren del original")
	}

	_ = met
	t.Logf("Exito: 1 MB transferido y verificado intacto a traves del puente TCP <-> QUIC/ICE")
}

func TestPuenteSenalizacionSecuencial(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// 1. Servicio eco TCP
	lnEco, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen eco: %v", err)
	}
	defer lnEco.Close()
	ecoAddr := lnEco.Addr().String()

	go func() {
		for {
			c, err := lnEco.Accept()
			if err != nil {
				return
			}
			go func(conn net.Conn) {
				defer conn.Close()
				_, _ = io.Copy(conn, conn)
			}(c)
		}
	}()

	// 2. Canales P2P sin callbacks cruzados: solo intercambio de params (simulando señalización vía JSON)
	canalA, err := NuevoCanalP2P(nil, true)
	if err != nil {
		t.Fatalf("canalA: %v", err)
	}
	defer canalA.Cerrar()

	canalB, err := NuevoCanalP2P(nil, true)
	if err != nil {
		t.Fatalf("canalB: %v", err)
	}
	defer canalB.Cerrar()

	paramsA, err := canalA.ObtenerParametrosLocales(ctx, "oferta")
	if err != nil {
		t.Fatalf("paramsA: %v", err)
	}
	paramsB, err := canalB.ObtenerParametrosLocales(ctx, "respuesta")
	if err != nil {
		t.Fatalf("paramsB: %v", err)
	}

	errChan := make(chan error, 2)
	go func() {
		errChan <- canalB.Establecer(ctx, paramsA, false) // Trabajador
	}()
	go func() {
		errChan <- canalA.Establecer(ctx, paramsB, true) // Maestro
	}()

	for i := 0; i < 2; i++ {
		if err := <-errChan; err != nil {
			t.Fatalf("establecer canal err: %v", err)
		}
	}

	// 3. Iniciar puentes y probar transferencia
	go func() {
		_ = IniciarPuenteTrabajador(ctx, ecoAddr, canalB, nil)
	}()

	lnMaestro, err := IniciarPuenteMaestro(ctx, "127.0.0.1:0", canalA, nil)
	if err != nil {
		t.Fatalf("puente maestro: %v", err)
	}
	defer lnMaestro.Close()

	cliConn, err := net.DialTimeout("tcp", lnMaestro.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial maestro: %v", err)
	}
	defer cliConn.Close()

	mensaje := []byte("HOLA_DESDE_P2P_SECUENCIAL_SIN_ONCANDIDATE")
	if _, err := cliConn.Write(mensaje); err != nil {
		t.Fatalf("write: %v", err)
	}

	buf := make([]byte, len(mensaje))
	if _, err := io.ReadFull(cliConn, buf); err != nil {
		t.Fatalf("read full: %v", err)
	}

	if !bytes.Equal(mensaje, buf) {
		t.Fatalf("datos recibidos no coinciden")
	}
}

type TrabajadorMetricas struct{}
