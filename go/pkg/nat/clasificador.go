package nat

import (
	"context"
	"fmt"
	"net"
	"strings"
	"time"

	"github.com/pion/stun/v4"
)

type TipoNAT string

const (
	NATAbierto             TipoNAT = "Abierto / IP Publica Directa"
	NATMapeoIndependiente  TipoNAT = "Mapeo Independiente de Extremo (EIM / Cone)"
	NATSimetrico           TipoNAT = "Mapeo Dependiente / Simetrico (Symmetric)"
	NATDesconocido         TipoNAT = "Desconocido / Error de consulta"
)

var cgnatNet = func() *net.IPNet {
	_, ipnet, _ := net.ParseCIDR("100.64.0.0/10")
	return ipnet
}()

type Diagnostico struct {
	Tipo             TipoNAT   `json:"tipo"`
	IPLocal          string    `json:"ip_local"`
	PuertoLocal      int       `json:"puerto_local"`
	Mapeo1           string    `json:"mapeo_1"`
	Mapeo2           string    `json:"mapeo_2"`
	Servidor1        string    `json:"servidor_1"`
	Servidor2        string    `json:"servidor_2"`
	EsPrivada        bool      `json:"es_ip_privada"`
	EsCGNAT          bool      `json:"es_cgnat_rfc6598"`
	PerforacionP2P   string    `json:"perforacion_p2p"`
	DetalleDominicano string   `json:"detalle_dominicano"`
}

func EsIPCGNAT(ip net.IP) bool {
	if ip == nil {
		return false
	}
	return cgnatNet.Contains(ip)
}

func EsIPPrivada(ip net.IP) bool {
	if ip == nil {
		return false
	}
	return ip.IsPrivate() || ip.IsLoopback()
}

func ObtenerIPLocalSaliente(servidorDestino string) (net.IP, error) {
	conn, err := net.DialTimeout("udp4", servidorDestino, 2*time.Second)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	localAddr := conn.LocalAddr().(*net.UDPAddr)
	return localAddr.IP, nil
}

func consultarSTUN(conn *net.UDPConn, serverAddr *net.UDPAddr, timeout time.Duration) (net.IP, int, error) {
	msg := stun.MustBuild(stun.TransactionID, stun.BindingRequest)
	if _, err := conn.WriteToUDP(msg.Raw, serverAddr); err != nil {
		return nil, 0, fmt.Errorf("error enviando request STUN a %s: %w", serverAddr, err)
	}

	_ = conn.SetReadDeadline(time.Now().Add(timeout))
	buf := make([]byte, 1500)
	for {
		n, from, err := conn.ReadFromUDP(buf)
		if err != nil {
			return nil, 0, fmt.Errorf("timeout o error leyendo respuesta STUN de %s: %w", serverAddr, err)
		}
		if !from.IP.Equal(serverAddr.IP) {
			continue
		}

		res := stun.New()
		res.Raw = buf[:n]
		if err := res.Decode(); err != nil {
			continue
		}

		var xorAddr stun.XORMappedAddress
		if err := xorAddr.GetFrom(res); err == nil {
			return xorAddr.IP, xorAddr.Port, nil
		}

		var mappedAddr stun.MappedAddress
		if err := mappedAddr.GetFrom(res); err == nil {
			return mappedAddr.IP, mappedAddr.Port, nil
		}
	}
}

func Clasificar(ctx context.Context, srv1, srv2 string) (*Diagnostico, error) {
	if srv1 == "" {
		srv1 = "stun.l.google.com:19302"
	}
	if srv2 == "" {
		srv2 = "stun1.l.google.com:19302"
	}

	srv1Limpio := strings.TrimPrefix(srv1, "stun:")
	srv2Limpio := strings.TrimPrefix(srv2, "stun:")

	addr1, err := net.ResolveUDPAddr("udp4", srv1Limpio)
	if err != nil {
		return nil, fmt.Errorf("no se pudo resolver STUN 1 (%s): %w", srv1Limpio, err)
	}
	addr2, err := net.ResolveUDPAddr("udp4", srv2Limpio)
	if err != nil {
		return nil, fmt.Errorf("no se pudo resolver STUN 2 (%s): %w", srv2Limpio, err)
	}

	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		return nil, fmt.Errorf("no se pudo abrir socket UDP local: %w", err)
	}
	defer conn.Close()

	localPort := conn.LocalAddr().(*net.UDPAddr).Port
	ipLocal, _ := ObtenerIPLocalSaliente(srv1Limpio)

	timeout := 2 * time.Second
	ip1, port1, err1 := consultarSTUN(conn, addr1, timeout)
	if err1 != nil {
		return nil, fmt.Errorf("fallo STUN servidor 1: %w", err1)
	}

	ip2, port2, err2 := consultarSTUN(conn, addr2, timeout)
	if err2 != nil {
		return nil, fmt.Errorf("fallo STUN servidor 2: %w", err2)
	}

	diag := &Diagnostico{
		IPLocal:     ipLocal.String(),
		PuertoLocal: localPort,
		Mapeo1:      fmt.Sprintf("%s:%d", ip1, port1),
		Mapeo2:      fmt.Sprintf("%s:%d", ip2, port2),
		Servidor1:   srv1Limpio,
		Servidor2:   srv2Limpio,
		EsPrivada:   EsIPPrivada(ipLocal),
		EsCGNAT:     EsIPCGNAT(ipLocal),
	}

	if ipLocal != nil && ipLocal.Equal(ip1) {
		diag.Tipo = NATAbierto
		diag.PerforacionP2P = "Excelente (sin traduccion NAT, puertos directamente accesibles)"
	} else if ip1.Equal(ip2) && port1 == port2 {
		diag.Tipo = NATMapeoIndependiente
		diag.PerforacionP2P = "Viable (EIM/Cone: mapeo estable para cualquier destino; hole-punching UDP exitoso en >85% de pares)"
	} else {
		diag.Tipo = NATSimetrico
		diag.PerforacionP2P = "Restringido (Simetrico: puerto cambia segun el destino remoto; falla si ambos extremos son simetricos, requiere relevo)"
	}

	diag.DetalleDominicano = generarAnalisisDominicano(diag)
	return diag, nil
}

func generarAnalisisDominicano(d *Diagnostico) string {
	var sb strings.Builder
	sb.WriteString("Analisis de conectividad para Republica Dominicana:\n")

	if d.EsCGNAT {
		sb.WriteString("- Tu IP local esta en el rango oficial CGNAT RFC 6598 (100.64.0.0/10).\n")
		sb.WriteString("- Proveedores como Claro Dominicana y Altice Dominicana implementan CGNAT masivo para ahorrar IPv4 publicas.\n")
	} else if d.EsPrivada {
		sb.WriteString("- Tu maquina esta detras de un router domestico residencial con IP privada (RFC 1918).\n")
		sb.WriteString("- En RD, la WAN de dicho router frequentemente recibe una IP en 100.64.0.0/10 (Doble NAT: router residencial + CGNAT del ISP).\n")
	}

	if d.Tipo == NATMapeoIndependiente {
		sb.WriteString("- Excelente noticia: Tu NAT/CGNAT utiliza Endpoint-Independent Mapping (EIM).\n")
		sb.WriteString("- El puerto externo asignado permanece igual hacia diferentes destinos remotos.\n")
		sb.WriteString("- La perforacion UDP mediante STUN/ICE FUNCIONA incluso cruzando CGNAT, siempre que el otro extremo no sea simetrico estricto.\n")
	} else if d.Tipo == NATSimetrico {
		sb.WriteString("- Advertencia: Tu NAT/CGNAT utiliza Mapeo Dependiente / Simetrico.\n")
		sb.WriteString("- En redes moviles de RD (Altice 4G/5G, Viva, Claro Movil) o conexiones residenciales compartidas, el CGNAT asigna puertos aleatorios.\n")
		sb.WriteString("- Si ambas casas tienen NAT simetrico o CGNAT estricto, la perforacion directa UDP fallara por imposibilidad de predecir el puerto.\n")
		sb.WriteString("- El sistema CompuShare activara automaticamente la degradacion transparente (fallback) al rele WebSocket en 3.5 s.\n")
	} else {
		sb.WriteString("- IP publica enrutada directamente a tu equipo sin NAT intermedio.\n")
	}

	return sb.String()
}
