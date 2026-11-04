package descubrir

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

const NTFY = "https://ntfy.sh"

var clienteHTTP = &http.Client{
	Timeout: 8 * time.Second,
}

// Tema calcula el nombre del tema de ntfy.sh para la clave dada.
func Tema(clave string) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("synrag-red:%s", clave)))
	res := hex.EncodeToString(h[:])
	if len(res) > 24 {
		res = res[:24]
	}
	return "synrag-red-" + res
}

// Vivo verifica si la URL responde 200 en /salud.
func Vivo(url string) bool {
	dest := strings.TrimPrefix(url, "wss://")
	dest = strings.TrimPrefix(dest, "ws://")
	proto := "https://"
	if strings.HasPrefix(url, "ws://") {
		proto = "http://"
	}
	peticion := proto + strings.TrimRight(dest, "/") + "/salud"
	resp, err := clienteHTTP.Get(peticion)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// Ultimo busca la dirección más reciente publicada en ntfy.sh que esté viva.
func Ultimo(clave string) (string, error) {
	url := fmt.Sprintf("%s/%s/json?poll=1&since=12h", NTFY, Tema(clave))
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	c := &http.Client{Timeout: 20 * time.Second}
	resp, err := c.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("ntfy devolvio estado %d", resp.StatusCode)
	}

	var urls []string
	scanner := bufio.NewScanner(resp.Body)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var m struct {
			Event   string `json:"event"`
			Message string `json:"message"`
		}
		if err := json.Unmarshal([]byte(line), &m); err == nil {
			if m.Event == "message" && strings.HasPrefix(m.Message, "wss://") {
				urls = append(urls, m.Message)
			}
		}
	}

	for i := len(urls) - 1; i >= 0; i-- {
		if Vivo(urls[i]) {
			return urls[i], nil
		}
	}
	return "", fmt.Errorf("no se encontro relé activo en ntfy.sh")
}

// Buscar sondea ntfy.sh hasta encontrar un relé o alcanzar esperaSegundos.
func Buscar(clave string, esperaSegundos float64, cadaSegundos float64) (string, error) {
	if cadaSegundos <= 0 {
		cadaSegundos = 10
	}
	limite := time.Now().Add(time.Duration(esperaSegundos * float64(time.Second)))
	for {
		url, err := Ultimo(clave)
		if err == nil && url != "" {
			return url, nil
		}
		if time.Now().After(limite) || esperaSegundos <= 0 {
			break
		}
		time.Sleep(time.Duration(cadaSegundos * float64(time.Second)))
	}
	return "", fmt.Errorf("no hay relé publicado: arranca el cuaderno que lo aloja (lanzar.py)")
}

// Rele resuelve el valor de relé: si es literal lo usa tal cual, si es 'buscar' o vacío lo consulta.
func Rele(valor, clave string, esperaSegundos float64) (string, error) {
	if valor != "" && valor != "buscar" {
		return valor, nil
	}
	return Buscar(clave, esperaSegundos, 10)
}
