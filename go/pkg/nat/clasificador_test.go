package nat

import (
	"context"
	"net"
	"testing"
	"time"
)

func TestClasificadorIPs(t *testing.T) {
	cgnatIP := net.ParseIP("100.64.1.25")
	if !EsIPCGNAT(cgnatIP) {
		t.Errorf("100.64.1.25 debe ser reconocido como CGNAT")
	}

	cgnatIPMax := net.ParseIP("100.127.255.254")
	if !EsIPCGNAT(cgnatIPMax) {
		t.Errorf("100.127.255.254 debe ser reconocido como CGNAT")
	}

	publicIP := net.ParseIP("179.52.12.34")
	if EsIPCGNAT(publicIP) {
		t.Errorf("179.52.12.34 NO debe ser reconocido como CGNAT")
	}

	privIP := net.ParseIP("192.168.1.10")
	if !EsIPPrivada(privIP) {
		t.Errorf("192.168.1.10 debe ser reconocida como privada")
	}
}

func TestClasificarSTUNPublico(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	diag, err := Clasificar(ctx, "stun.l.google.com:19302", "stun1.l.google.com:19302")
	if err != nil {
		t.Logf("Aviso: prueba STUN remota no pudo completarse en este entorno (posible ausencia de salida UDP exterior): %v", err)
		return
	}

	t.Logf("Resultado NAT: %s", diag.Tipo)
	t.Logf("IP Local: %s, Mapeo 1: %s, Mapeo 2: %s", diag.IPLocal, diag.Mapeo1, diag.Mapeo2)
	t.Logf("Perforacion P2P: %s", diag.PerforacionP2P)

	if diag.Tipo == "" {
		t.Errorf("El tipo de NAT no puede estar vacio")
	}
}
