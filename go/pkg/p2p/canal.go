package p2p

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"fmt"
	"math/big"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/stun/v4"
	"github.com/quic-go/quic-go"
)

type IceParametros struct {
	Tipo       string   `json:"tipo"` // "oferta" o "respuesta"
	Ufrag      string   `json:"ufrag"`
	Pwd        string   `json:"pwd"`
	Candidatos []string `json:"candidatos"`
}

type icePacketConn struct {
	conn *ice.Conn
}

func (c *icePacketConn) ReadFrom(p []byte) (int, net.Addr, error) {
	n, err := c.conn.Read(p)
	return n, c.conn.RemoteAddr(), err
}

func (c *icePacketConn) WriteTo(p []byte, addr net.Addr) (int, error) {
	return c.conn.Write(p)
}

func (c *icePacketConn) Close() error {
	return c.conn.Close()
}

func (c *icePacketConn) LocalAddr() net.Addr {
	return c.conn.LocalAddr()
}

func (c *icePacketConn) SetDeadline(t time.Time) error {
	return c.conn.SetDeadline(t)
}

func (c *icePacketConn) SetReadDeadline(t time.Time) error {
	return c.conn.SetReadDeadline(t)
}

func (c *icePacketConn) SetWriteDeadline(t time.Time) error {
	return c.conn.SetWriteDeadline(t)
}

func GenerarTLSConfig() (*tls.Config, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject: pkix.Name{
			Organization: []string{"CompuShare"},
			CommonName:   "compushare-p2p",
		},
		NotBefore:             time.Now().Add(-1 * time.Hour),
		NotAfter:              time.Now().Add(24 * 365 * time.Hour),
		KeyUsage:              x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
	}
	certDER, err := x509.CreateCertificate(rand.Reader, &template, &template, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	cert := tls.Certificate{
		Certificate: [][]byte{certDER},
		PrivateKey:  key,
	}
	return &tls.Config{
		Certificates:       []tls.Certificate{cert},
		NextProtos:         []string{"compushare-rpc-v1"},
		InsecureSkipVerify: true,
	}, nil
}

type CanalP2P struct {
	Agent       *ice.Agent
	IceConn     *ice.Conn
	QuicConn    *quic.Conn
	Transport   *quic.Transport
	Listener    *quic.Listener
	EsIniciador bool

	candidatosLocales []string
	mu                sync.Mutex
	recoleccionFin    chan struct{}
}

func NuevoCanalP2P(servidoresSTUN []string, incluirLoopback bool) (*CanalP2P, error) {
	var uris []*stun.URI
	for _, s := range servidoresSTUN {
		sLimpio := s
		if !strings.HasPrefix(sLimpio, "stun:") {
			sLimpio = "stun:" + sLimpio
		}
		if u, err := stun.ParseURI(sLimpio); err == nil {
			uris = append(uris, u)
		}
	}

	canal := &CanalP2P{
		recoleccionFin: make(chan struct{}),
	}

	cfg := &ice.AgentConfig{
		NetworkTypes:    []ice.NetworkType{ice.NetworkTypeUDP4},
		Urls:            uris,
		IncludeLoopback: incluirLoopback,
	}

	agent, err := ice.NewAgent(cfg)
	if err != nil {
		return nil, fmt.Errorf("error creando agente ICE: %w", err)
	}
	canal.Agent = agent

	var unaVez sync.Once
	err = agent.OnCandidate(func(c ice.Candidate) {
		if c != nil {
			canal.mu.Lock()
			canal.candidatosLocales = append(canal.candidatosLocales, c.Marshal())
			canal.mu.Unlock()
		} else {
			unaVez.Do(func() {
				close(canal.recoleccionFin)
			})
		}
	})
	if err != nil {
		agent.Close()
		return nil, fmt.Errorf("error registrando OnCandidate: %w", err)
	}

	return canal, nil
}

func (c *CanalP2P) ObtenerParametrosLocales(ctx context.Context, tipo string) (*IceParametros, error) {
	ufrag, pwd, err := c.Agent.GetLocalUserCredentials()
	if err != nil {
		return nil, err
	}

	if err := c.Agent.GatherCandidates(); err != nil {
		return nil, err
	}

	select {
	case <-c.recoleccionFin:
	case <-time.After(1200 * time.Millisecond):
	case <-ctx.Done():
		return nil, ctx.Err()
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	return &IceParametros{
		Tipo:       tipo,
		Ufrag:      ufrag,
		Pwd:        pwd,
		Candidatos: append([]string(nil), c.candidatosLocales...),
	}, nil
}

func (c *CanalP2P) AplicarParametrosRemotos(remoto *IceParametros) error {
	for _, cStr := range remoto.Candidatos {
		cand, err := ice.UnmarshalCandidate(cStr)
		if err == nil && cand != nil {
			_ = c.Agent.AddRemoteCandidate(cand)
		}
	}
	return nil
}

func (c *CanalP2P) Establecer(ctx context.Context, remoto *IceParametros, esIniciador bool) error {
	c.EsIniciador = esIniciador
	if err := c.AplicarParametrosRemotos(remoto); err != nil {
		return err
	}

	var iceConn *ice.Conn
	var err error

	if esIniciador {
		iceConn, err = c.Agent.Dial(ctx, remoto.Ufrag, remoto.Pwd)
	} else {
		iceConn, err = c.Agent.Accept(ctx, remoto.Ufrag, remoto.Pwd)
	}
	if err != nil {
		return fmt.Errorf("fallo perforacion ICE: %w", err)
	}
	c.IceConn = iceConn

	pConn := &icePacketConn{conn: iceConn}
	tr := &quic.Transport{Conn: pConn}
	c.Transport = tr

	tlsConf, err := GenerarTLSConfig()
	if err != nil {
		c.Cerrar()
		return fmt.Errorf("error generando TLS config: %w", err)
	}

	quicConf := &quic.Config{
		MaxIdleTimeout:                 30 * time.Second,
		KeepAlivePeriod:                5 * time.Second,
		InitialStreamReceiveWindow:     4 * 1024 * 1024,
		InitialConnectionReceiveWindow: 8 * 1024 * 1024,
		MaxStreamReceiveWindow:         16 * 1024 * 1024,
		MaxConnectionReceiveWindow:     32 * 1024 * 1024,
	}

	if esIniciador {
		// Maestro es cliente QUIC
		clientTLS := tlsConf.Clone()
		clientTLS.InsecureSkipVerify = true
		qConn, err := tr.Dial(ctx, iceConn.RemoteAddr(), clientTLS, quicConf)
		if err != nil {
			c.Cerrar()
			return fmt.Errorf("error handshake QUIC dial: %w", err)
		}
		c.QuicConn = qConn
	} else {
		// Trabajador es servidor QUIC
		listener, err := tr.Listen(tlsConf, quicConf)
		if err != nil {
			c.Cerrar()
			return fmt.Errorf("error quic listen: %w", err)
		}
		c.Listener = listener

		qConn, err := listener.Accept(ctx)
		if err != nil {
			c.Cerrar()
			return fmt.Errorf("error quic accept: %w", err)
		}
		c.QuicConn = qConn
	}

	return nil
}

func (c *CanalP2P) Cerrar() {
	if c.QuicConn != nil {
		_ = c.QuicConn.CloseWithError(0, "cerrando")
	}
	if c.Listener != nil {
		_ = c.Listener.Close()
	}
	if c.Transport != nil {
		_ = c.Transport.Close()
	}
	if c.IceConn != nil {
		_ = c.IceConn.Close()
	}
	if c.Agent != nil {
		_ = c.Agent.Close()
	}
}
