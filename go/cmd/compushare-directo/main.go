package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/nat"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/p2p"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/senalar"
)

func parsearIceParametros(datos any) (*p2p.IceParametros, error) {
	b, err := json.Marshal(datos)
	if err != nil {
		return nil, err
	}
	var wrapper struct {
		Tipo string             `json:"tipo"`
		Ice  *p2p.IceParametros `json:"ice"`
	}
	if err := json.Unmarshal(b, &wrapper); err == nil && wrapper.Ice != nil {
		return wrapper.Ice, nil
	}
	var params p2p.IceParametros
	if err := json.Unmarshal(b, &params); err == nil && params.Ufrag != "" {
		return &params, nil
	}
	return nil, fmt.Errorf("parametros ICE no reconocidos")
}

func parsearFallbackPuerto(datos any) (int, bool) {
	b, err := json.Marshal(datos)
	if err != nil {
		return 0, false
	}
	var wrapper struct {
		Tipo   string `json:"tipo"`
		Puerto int    `json:"puerto"`
	}
	if err := json.Unmarshal(b, &wrapper); err == nil && wrapper.Tipo == "fallback_rele" {
		return wrapper.Puerto, true
	}
	return 0, false
}

func ejecutarDiagnosticoNAT(s1, s2 string) *nat.Diagnostico {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	fmt.Println("--- Diagnostico y Clasificacion de NAT (STUN RFC 5389) ---")
	diag, err := nat.Clasificar(ctx, s1, s2)
	if err != nil {
		fmt.Printf("Error clasificando NAT: %v\n", err)
		return nil
	}

	fmt.Printf("Tipo de NAT:       %s\n", diag.Tipo)
	fmt.Printf("IP Local:          %s (Puerto local: %d)\n", diag.IPLocal, diag.PuertoLocal)
	fmt.Printf("Mapeo STUN 1:      %s (%s)\n", diag.Mapeo1, diag.Servidor1)
	fmt.Printf("Mapeo STUN 2:      %s (%s)\n", diag.Mapeo2, diag.Servidor2)
	fmt.Printf("Es IP privada:     %v\n", diag.EsPrivada)
	fmt.Printf("Es CGNAT RFC6598:  %v\n", diag.EsCGNAT)
	fmt.Printf("Viabilidad P2P:    %s\n\n", diag.PerforacionP2P)
	fmt.Println(diag.DetalleDominicano)
	fmt.Println("---------------------------------------------------------")
	return diag
}

func main() {
	var (
		rol             string
		releURL         string
		clave           string
		enjambre        string
		idPropio        string
		nombrePropio    string
		parID           string
		escuchar        string
		destino         string
		stunList        string
		timeoutDirecto  time.Duration
		forzarRele      bool
		diagnosticoNAT  bool
		soloNAT         bool
		medir           bool
		medirMB         int
		repeticiones    int
		servidorSTUN1   string
		servidorSTUN2   string
	)

	flag.StringVar(&rol, "rol", "maestro", "Rol: maestro (ofrece puerto local) o trabajador (conecta con ggml-rpc-server)")
	flag.StringVar(&releURL, "rele", "ws://127.0.0.1:53000", "URL del rele WebSocket (/ para senalizacion, /tubo para fallback)")
	flag.StringVar(&clave, "clave", "compushare-clave", "Clave secreta compartida")
	flag.StringVar(&enjambre, "enjambre", "compushare", "Nombre del enjambre")
	flag.StringVar(&idPropio, "id", "", "ID propio (vacio = generado por el rele)")
	flag.StringVar(&nombrePropio, "nombre", "", "Nombre del nodo")
	flag.StringVar(&parID, "par", "", "ID del par remoto con quien conectar (en modo maestro)")
	flag.StringVar(&escuchar, "escuchar", "127.0.0.1:53100", "Direccion local donde escuchar (modo maestro)")
	flag.StringVar(&destino, "destino", "127.0.0.1:53052", "Direccion del ggml-rpc-server destino (modo trabajador)")
	flag.StringVar(&stunList, "stun", "stun.l.google.com:19302,stun1.l.google.com:19302", "Servidores STUN separados por coma")
	flag.DurationVar(&timeoutDirecto, "timeout-directo", 3500*time.Millisecond, "Tiempo maximo para perforacion directa antes de caer al rele")
	flag.BoolVar(&forzarRele, "forzar-rele", false, "Forzar caida inmediata al rele omitiendo P2P")
	flag.BoolVar(&diagnosticoNAT, "diagnostico-nat", false, "Ejecutar clasificacion de NAT antes de conectar")
	flag.BoolVar(&soloNAT, "solo-nat", false, "Solo ejecutar clasificacion de NAT y terminar")
	flag.BoolVar(&medir, "medir", false, "Modo medicion de rendimiento (establecimiento, MB/s y RTT)")
	flag.IntVar(&medirMB, "medir-mb", 5, "Volumen en MB para medir caudal")
	flag.IntVar(&repeticiones, "repeticiones", 3, "Numero de repeticiones en modo medicion")
	flag.StringVar(&servidorSTUN1, "stun1", "stun.l.google.com:19302", "Servidor STUN 1 para clasificacion")
	flag.StringVar(&servidorSTUN2, "stun2", "stun1.l.google.com:19302", "Servidor STUN 2 para clasificacion")
	flag.Parse()

	if soloNAT {
		ejecutarDiagnosticoNAT(servidorSTUN1, servidorSTUN2)
		return
	}

	if diagnosticoNAT {
		ejecutarDiagnosticoNAT(servidorSTUN1, servidorSTUN2)
	}

	stuns := strings.Split(stunList, ",")
	for i := range stuns {
		stuns[i] = strings.TrimSpace(stuns[i])
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("Senal recibida, cerrando compushare-directo...")
		cancel()
	}()

	if medir {
		ejecutarModoMedir(ctx, stuns, timeoutDirecto, medirMB, repeticiones)
		return
	}

	switch strings.ToLower(rol) {
	case "maestro", "cliente":
		err := correrMaestro(ctx, releURL, clave, enjambre, idPropio, nombrePropio, parID, escuchar, stuns, timeoutDirecto, forzarRele)
		if err != nil && err != context.Canceled {
			log.Fatalf("Error en maestro: %v", err)
		}
	case "trabajador", "nodo":
		err := correrTrabajador(ctx, releURL, clave, enjambre, idPropio, nombrePropio, destino, stuns, timeoutDirecto, forzarRele)
		if err != nil && err != context.Canceled {
			log.Fatalf("Error en trabajador: %v", err)
		}
	default:
		log.Fatalf("Rol '%s' desconocido. Usa 'maestro' o 'trabajador'", rol)
	}
}

func correrMaestro(ctx context.Context, releURL, clave, enjambre, idPropio, nombrePropio, parID, escuchar string, stuns []string, timeoutDirecto time.Duration, forzarRele bool) error {
	if nombrePropio == "" {
		nombrePropio = "maestro-directo"
	}

	cli := senalar.NuevoCliente(releURL, clave, enjambre, "cliente", idPropio, nombrePropio)
	if err := cli.Conectar(ctx); err != nil {
		return fmt.Errorf("error conectando al rele: %w", err)
	}
	defer cli.Cerrar()

	log.Printf("Maestro conectado al rele con ID: %s", cli.ID)

	// Resolver parID si no fue provisto
	if parID == "" {
		log.Println("Esperando descubrimiento de nodo trabajador...")
		for parID == "" {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(500 * time.Millisecond):
				for _, n := range cli.Nodos() {
					if n.ID != cli.ID {
						parID = n.ID
						log.Printf("Nodo trabajador encontrado: %s (%s)", n.ID, n.Nombre)
						break
					}
				}
			}
		}
	}

	var modo string = "desconocido"
	var metricas p2p.MetricasPuente

	if !forzarRele {
		log.Printf("Iniciando negociacion directa P2P hacia par %s (timeout: %v)...", parID, timeoutDirecto)
		canalP2P, err := p2p.NuevoCanalP2P(stuns, true)
		if err == nil {
			defer canalP2P.Cerrar()

			paramsLocales, err := canalP2P.ObtenerParametrosLocales(ctx, "oferta")
			if err == nil {
				// Enviar oferta al par por el relé
				msg := map[string]any{
					"tipo": "oferta",
					"ice":  paramsLocales,
				}
				if err := cli.EnviarSenal(ctx, parID, msg); err == nil {
					// Esperar respuesta o fallback
					ctxDirecto, cancelDirecto := context.WithTimeout(ctx, timeoutDirecto)
					defer cancelDirecto()

					var paramsRemotos *p2p.IceParametros
					var fallbackPuerto int
					recibido := false

					for !recibido {
						select {
						case <-ctxDirecto.Done():
							log.Printf("Timeout (%v) agotado esperando senal P2P del trabajador", timeoutDirecto)
							recibido = true
						case senal := <-cli.CanalSenales():
							if senal.De == parID {
								if p, ok := parsearFallbackPuerto(senal.Datos); ok {
									fallbackPuerto = p
									recibido = true
									break
								}
								if pRem, err := parsearIceParametros(senal.Datos); err == nil {
									paramsRemotos = pRem
									recibido = true
									break
								}
							}
						}
					}

					if paramsRemotos != nil {
						// Intentar establecer QUIC sobre ICE
						if err := canalP2P.Establecer(ctxDirecto, paramsRemotos, true); err == nil {
							modo = "directo"
							log.Printf("Enlace directo P2P QUIC/ICE establecido con exito hacia %s", parID)

							ln, err := p2p.IniciarPuenteMaestro(ctx, escuchar, canalP2P, &metricas)
							if err != nil {
								return fmt.Errorf("error iniciando puente local maestro: %w", err)
							}
							defer ln.Close()

							log.Printf("Tubo directo LISTO escuchando en %s (modo: %s)", escuchar, modo)
							<-ctx.Done()
							return nil
						} else {
							log.Printf("Fallo handshake directo P2P: %v", err)
						}
					}

					if fallbackPuerto > 0 {
						log.Printf("El trabajador indico fallback al rele en puerto %d", fallbackPuerto)
						return correrPuenteHaciaPuertoRele(ctx, escuchar, fallbackPuerto, &metricas)
					}
				}
			}
		}
	}

	// Fallback automático al relé
	modo = "rele"
	log.Printf("Activando fallback al rele para maestro en %s...", escuchar)
	// Esperar que el trabajador ofrezca el tubo en el relé
	ctxEspera, cancelEspera := context.WithTimeout(ctx, 10*time.Second)
	defer cancelEspera()

	if forzarRele {
		_ = cli.EnviarSenal(ctx, parID, map[string]any{
			"tipo": "fallback_solicitud",
		})
	}

	var puertoRele int
	for puertoRele == 0 {
		select {
		case <-ctxEspera.Done():
			return fmt.Errorf("timeout esperando asignacion de puerto de rele por el trabajador")
		case senal := <-cli.CanalSenales():
			if senal.De == parID {
				if p, ok := parsearFallbackPuerto(senal.Datos); ok {
					puertoRele = p
					break
				}
			}
		case <-time.After(500 * time.Millisecond):
			if forzarRele && puertoRele == 0 {
				_ = cli.EnviarSenal(ctx, parID, map[string]any{
					"tipo": "fallback_solicitud",
				})
			}
		}
	}

	log.Printf("Puerto de rele obtenido: %d. Iniciando puente local...", puertoRele)
	return correrPuenteHaciaPuertoRele(ctx, escuchar, puertoRele, &metricas)
}

func correrPuenteHaciaPuertoRele(ctx context.Context, escuchar string, puertoRele int, m *p2p.MetricasPuente) error {
	ln, err := net.Listen("tcp", escuchar)
	if err != nil {
		return fmt.Errorf("error escuchando en %s: %w", escuchar, err)
	}
	defer ln.Close()

	log.Printf("Tubo rele LISTO escuchando en %s -> relé 127.0.0.1:%d (modo: rele)", escuchar, puertoRele)

	go func() {
		<-ctx.Done()
		ln.Close()
	}()

	for {
		c, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}

		go func(local net.Conn) {
			defer local.Close()
			releConn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", puertoRele), 3*time.Second)
			if err != nil {
				log.Printf("Error conectando a puerto del rele 127.0.0.1:%d: %v", puertoRele, err)
				return
			}
			defer releConn.Close()
			p2p.PuenteBidireccional(local, releConn, m)
		}(c)
	}
}

func correrTrabajador(ctx context.Context, releURL, clave, enjambre, idPropio, nombrePropio, destino string, stuns []string, timeoutDirecto time.Duration, forzarRele bool) error {
	if nombrePropio == "" {
		nombrePropio = "trabajador-directo"
	}

	hostDestino, portDestinoStr, err := net.SplitHostPort(destino)
	if err != nil {
		hostDestino = "127.0.0.1"
		portDestinoStr = destino
	}
	portDestino, _ := strconv.Atoi(portDestinoStr)
	if portDestino == 0 {
		portDestino = 53052
	}

	cli := senalar.NuevoCliente(releURL, clave, enjambre, "nodo", idPropio, nombrePropio)
	if err := cli.Conectar(ctx); err != nil {
		return fmt.Errorf("error conectando al rele: %w", err)
	}
	defer cli.Cerrar()

	log.Printf("Trabajador conectado al rele con ID: %s (destino local: %s:%d)", cli.ID, hostDestino, portDestino)

	var metricas p2p.MetricasPuente

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case senal := <-cli.CanalSenales():
			origen := senal.De
			var tipoMsg string
			if m, ok := senal.Datos.(map[string]any); ok {
				if t, ok := m["tipo"].(string); ok {
					tipoMsg = t
				}
			}

			if forzarRele || tipoMsg == "fallback_solicitud" {
				log.Println("Forzar rele activo: omitiendo intento P2P y levantando tubo de rele")
				sesion, err := senalar.ArrancarTuboReleTrabajador(ctx, releURL, clave, cli.ID, hostDestino, portDestino)
				if err != nil {
					log.Printf("Error levantando tubo rele: %v", err)
					continue
				}
				defer sesion.Cerrar()

				_ = cli.EnviarSenal(ctx, origen, map[string]any{
					"tipo":   "fallback_rele",
					"puerto": sesion.PuertoRemoto,
				})
				log.Printf("Tubo de rele activo en puerto %d", sesion.PuertoRemoto)
				<-ctx.Done()
				return nil
			}

			paramsRemotos, err := parsearIceParametros(senal.Datos)
			if err != nil {
				continue
			}

			log.Printf("Oferta P2P recibida de %s. Iniciando recoleccion ICE...", origen)
			canalP2P, err := p2p.NuevoCanalP2P(stuns, true)
			if err != nil {
				log.Printf("Error creando canal P2P: %v", err)
				continue
			}

			paramsLocales, err := canalP2P.ObtenerParametrosLocales(ctx, "respuesta")
			if err != nil {
				canalP2P.Cerrar()
				log.Printf("Error obteniendo parametros locales ICE: %v", err)
				continue
			}

			// Enviar respuesta al maestro
			_ = cli.EnviarSenal(ctx, origen, map[string]any{
				"tipo": "respuesta",
				"ice":  paramsLocales,
			})

			ctxDirecto, cancelDirecto := context.WithTimeout(ctx, timeoutDirecto)
			err = canalP2P.Establecer(ctxDirecto, paramsRemotos, false)
			cancelDirecto()

			if err == nil {
				log.Printf("Enlace directo P2P establecido con exito con maestro %s (modo: directo)", origen)
				go func() {
					target := fmt.Sprintf("%s:%d", hostDestino, portDestino)
					if err := p2p.IniciarPuenteTrabajador(ctx, target, canalP2P, &metricas); err != nil && ctx.Err() == nil {
						log.Printf("Puente trabajador P2P cerrado: %v", err)
					}
				}()
			} else {
				canalP2P.Cerrar()
				log.Printf("Fallo perforacion P2P tras timeout (%v): %v. Cayendo al rele...", timeoutDirecto, err)

				sesion, err := senalar.ArrancarTuboReleTrabajador(ctx, releURL, clave, cli.ID, hostDestino, portDestino)
				if err != nil {
					log.Printf("Error arrancando tubo de rele trabajador: %v", err)
					continue
				}
				defer sesion.Cerrar()

				_ = cli.EnviarSenal(ctx, origen, map[string]any{
					"tipo":   "fallback_rele",
					"puerto": sesion.PuertoRemoto,
				})
				log.Printf("Fallback al rele activado con exito. Puerto asignado en rele: %d", sesion.PuertoRemoto)
				<-ctx.Done()
				return nil
			}
		}
	}
}

type ResultadoMedicion struct {
	Caso                    string    `json:"caso"`
	Repeticion              int       `json:"repeticion"`
	TiempoEstablecimientoMs float64   `json:"tiempo_establecimiento_ms"`
	CaudalMBs               float64   `json:"caudal_mbs"`
	DuracionTransferenciaS  float64   `json:"duracion_transferencia_s"`
	RTTMedioMs              float64   `json:"rtt_medio_ms"`
	RTTMinMs                float64   `json:"rtt_min_ms"`
	RTTMaxMs                float64   `json:"rtt_max_ms"`
	BytesTransferidos       int       `json:"bytes_transferidos"`
	Timestamp               time.Time `json:"timestamp"`
}

func ejecutarModoMedir(ctx context.Context, stuns []string, timeoutDirecto time.Duration, mb int, repeticiones int) {
	fmt.Printf("=== COMPUSHARE-DIRECTO: Medicion Interna P2P ICE+QUIC (%d MB, %d repeticiones) ===\n", mb, repeticiones)

	// Iniciar servidor eco TCP
	lnEco, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatalf("listen eco: %v", err)
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

	var resultados []ResultadoMedicion

	for rep := 1; rep <= repeticiones; rep++ {
		tInicioEst := time.Now()

		canalA, err := p2p.NuevoCanalP2P(stuns, true)
		if err != nil {
			log.Fatalf("canalA: %v", err)
		}
		canalB, err := p2p.NuevoCanalP2P(stuns, true)
		if err != nil {
			log.Fatalf("canalB: %v", err)
		}

		paramsA, err := canalA.ObtenerParametrosLocales(ctx, "oferta")
		if err != nil {
			log.Fatalf("paramsA: %v", err)
		}
		paramsB, err := canalB.ObtenerParametrosLocales(ctx, "respuesta")
		if err != nil {
			log.Fatalf("paramsB: %v", err)
		}

		errCh := make(chan error, 2)
		go func() { errCh <- canalB.Establecer(ctx, paramsA, false) }()
		go func() { errCh <- canalA.Establecer(ctx, paramsB, true) }()

		for i := 0; i < 2; i++ {
			if err := <-errCh; err != nil {
				log.Fatalf("establecer: %v", err)
			}
		}

		tEstablecimiento := time.Since(tInicioEst).Seconds() * 1000.0

		// Iniciar puentes
		go func() { _ = p2p.IniciarPuenteTrabajador(ctx, ecoAddr, canalB, nil) }()
		lnMaestro, err := p2p.IniciarPuenteMaestro(ctx, "127.0.0.1:0", canalA, nil)
		if err != nil {
			log.Fatalf("puente maestro: %v", err)
		}
		maestroAddr := lnMaestro.Addr().String()

		// Conectar cliente
		cliConn, err := net.DialTimeout("tcp", maestroAddr, 3*time.Second)
		if err != nil {
			log.Fatalf("dial maestro: %v", err)
		}

		// Medir RTT (20 muestras de 64 bytes)
		pingPayload := make([]byte, 64)
		_, _ = rand.Read(pingPayload)
		respBuf := make([]byte, 64)

		var rttMin, rttMax, rttTotal float64
		rttMin = 1e9

		muestrasRTT := 20
		for i := 0; i < muestrasRTT; i++ {
			t0 := time.Now()
			_, _ = cliConn.Write(pingPayload)
			_, _ = io.ReadFull(cliConn, respBuf)
			rtt := time.Since(t0).Seconds() * 1000.0
			rttTotal += rtt
			if rtt < rttMin {
				rttMin = rtt
			}
			if rtt > rttMax {
				rttMax = rtt
			}
		}
		rttMedio := rttTotal / float64(muestrasRTT)

		// Medir Caudal útil
		volumen := mb * 1024 * 1024
		carga := make([]byte, volumen)
		_, _ = rand.Read(carga)
		recibido := make([]byte, volumen)

		t0Trans := time.Now()
		go func() {
			_, _ = cliConn.Write(carga)
		}()
		_, err = io.ReadFull(cliConn, recibido)
		tTransferencia := time.Since(t0Trans).Seconds()
		if err != nil {
			log.Fatalf("error leyendo carga: %v", err)
		}

		caudalMBs := float64(volumen) / (1024.0 * 1024.0 * tTransferencia)

		cliConn.Close()
		lnMaestro.Close()
		canalA.Cerrar()
		canalB.Cerrar()

		res := ResultadoMedicion{
			Caso:                    "P2P Go (ICE + QUIC)",
			Repeticion:              rep,
			TiempoEstablecimientoMs: tEstablecimiento,
			CaudalMBs:               caudalMBs,
			DuracionTransferenciaS:  tTransferencia,
			RTTMedioMs:              rttMedio,
			RTTMinMs:                rttMin,
			RTTMaxMs:                rttMax,
			BytesTransferidos:       volumen,
			Timestamp:               time.Now(),
		}
		resultados = append(resultados, res)

		fmt.Printf("Repeticion %d/%d: Establecimiento: %.2f ms | Caudal: %.2f MB/s (%.4f s) | RTT: %.3f ms (min: %.3f, max: %.3f)\n",
			rep, repeticiones, tEstablecimiento, caudalMBs, tTransferencia, rttMedio, rttMin, rttMax)
	}

	bJson, _ := json.MarshalIndent(resultados, "", "  ")
	_ = os.WriteFile("medicion_directo_go.json", bJson, 0644)
	fmt.Println("Resultados guardados en medicion_directo_go.json")
}
