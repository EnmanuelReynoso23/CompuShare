package descubrir

import "testing"

func TestTema(t *testing.T) {
	t1 := Tema("clave-de-prueba")
	t2 := Tema("clave-de-prueba")
	t3 := Tema("otra-clave")

	if t1 != t2 {
		t.Fatalf("Tema debe ser determinista: %s != %s", t1, t2)
	}
	if t1 == t3 {
		t.Fatalf("Temas de distintas claves deben ser distintos: %s == %s", t1, t3)
	}
	if len(t1) != len("synrag-red-")+24 {
		t.Fatalf("Longitud de tema inesperada: %s (len %d)", t1, len(t1))
	}
}

func TestReleDirecto(t *testing.T) {
	dir, err := Rele("ws://127.0.0.1:8095", "clave", 0)
	if err != nil {
		t.Fatalf("Rele directo fallo: %v", err)
	}
	if dir != "ws://127.0.0.1:8095" {
		t.Fatalf("Esperado ws://127.0.0.1:8095, obtenido %s", dir)
	}
}
