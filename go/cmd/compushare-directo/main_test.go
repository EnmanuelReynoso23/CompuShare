package main

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/p2p"
)

func TestParsearIceParametros(t *testing.T) {
	params := &p2p.IceParametros{
		Tipo:       "oferta",
		Ufrag:      "ufrag123",
		Pwd:        "pwd123",
		Candidatos: []string{"cand1", "cand2"},
	}

	// Caso 1: envoltorio con clave "ice"
	wrapper := map[string]any{
		"tipo": "oferta",
		"ice":  params,
	}
	res, err := parsearIceParametros(wrapper)
	if err != nil {
		t.Fatalf("error parseando con wrapper: %v", err)
	}
	if res.Ufrag != "ufrag123" || len(res.Candidatos) != 2 {
		t.Errorf("resultado inesperado: %+v", res)
	}

	// Caso 2: plano
	res2, err := parsearIceParametros(params)
	if err != nil {
		t.Fatalf("error parseando plano: %v", err)
	}
	if res2.Pwd != "pwd123" {
		t.Errorf("resultado inesperado: %+v", res2)
	}

	// Caso 3: inválido
	_, err = parsearIceParametros(map[string]any{"invalido": true})
	if err == nil {
		t.Errorf("se esperaba error para entrada invalida")
	}
}

func TestParsearFallbackPuerto(t *testing.T) {
	msg := map[string]any{
		"tipo":   "fallback_rele",
		"puerto": 53100,
	}
	p, ok := parsearFallbackPuerto(msg)
	if !ok || p != 53100 {
		t.Errorf("esperado 53100 y true, obtenido %d y %v", p, ok)
	}

	p2, ok2 := parsearFallbackPuerto(map[string]any{"tipo": "oferta"})
	if ok2 || p2 != 0 {
		t.Errorf("esperado false y 0, obtenido %v y %d", ok2, p2)
	}
}

func TestPuenteHaciaPuertoRele(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// 1. Simular el puerto del relé con un servicio de eco TCP
	lnRele, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen rele: %v", err)
	}
	defer lnRele.Close()
	_, portStr, _ := net.SplitHostPort(lnRele.Addr().String())
	var puertoRele int
	for _, ch := range portStr {
		puertoRele = puertoRele*10 + int(ch-'0')
	}

	go func() {
		for {
			c, err := lnRele.Accept()
			if err != nil {
				return
			}
			go func(conn net.Conn) {
				defer conn.Close()
				buf := make([]byte, 1024)
				for {
					n, err := conn.Read(buf)
					if n > 0 {
						_, _ = conn.Write(buf[:n])
					}
					if err != nil {
						break
					}
				}
			}(c)
		}
	}()

	// 2. Levantar puente maestro simulando fallback
	lnMaestro, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen maestro: %v", err)
	}
	maestroAddr := lnMaestro.Addr().String()
	lnMaestro.Close() // liberamos para que correrPuenteHaciaPuertoRele escuche ahi

	var met p2p.MetricasPuente
	go func() {
		_ = correrPuenteHaciaPuertoRele(ctx, maestroAddr, puertoRele, &met)
	}()

	time.Sleep(50 * time.Millisecond)

	// 3. Conectar cliente al puente maestro
	cli, err := net.DialTimeout("tcp", maestroAddr, 2*time.Second)
	if err != nil {
		t.Fatalf("dial maestro puente: %v", err)
	}
	defer cli.Close()

	payload := []byte("ECO_A_TRAVES_DE_PUENTE_RELE_FALLBACK")
	if _, err := cli.Write(payload); err != nil {
		t.Fatalf("write cli: %v", err)
	}

	resp := make([]byte, len(payload))
	if _, err := cli.Read(resp); err != nil {
		t.Fatalf("read cli: %v", err)
	}

	if string(resp) != string(payload) {
		t.Errorf("eco no coincide: esperado %s, obtenido %s", string(payload), string(resp))
	}
}
