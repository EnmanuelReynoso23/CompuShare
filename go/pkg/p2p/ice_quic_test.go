package p2p

import (
	"context"
	"io"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/quic-go/quic-go"
)

func TestICEWithQUIC(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	agentA, err := ice.NewAgent(&ice.AgentConfig{
		NetworkTypes:    []ice.NetworkType{ice.NetworkTypeUDP4},
		IncludeLoopback: true,
	})
	if err != nil {
		t.Fatalf("agentA error: %v", err)
	}
	defer agentA.Close()

	agentB, err := ice.NewAgent(&ice.AgentConfig{
		NetworkTypes:    []ice.NetworkType{ice.NetworkTypeUDP4},
		IncludeLoopback: true,
	})
	if err != nil {
		t.Fatalf("agentB error: %v", err)
	}
	defer agentB.Close()

	ufragA, pwdA, err := agentA.GetLocalUserCredentials()
	if err != nil {
		t.Fatalf("agentA creds: %v", err)
	}
	ufragB, pwdB, err := agentB.GetLocalUserCredentials()
	if err != nil {
		t.Fatalf("agentB creds: %v", err)
	}

	var mu sync.Mutex
	err = agentA.OnCandidate(func(c ice.Candidate) {
		if c != nil {
			mu.Lock()
			_ = agentB.AddRemoteCandidate(c)
			mu.Unlock()
		}
	})
	if err != nil {
		t.Fatalf("onCandidate A: %v", err)
	}

	err = agentB.OnCandidate(func(c ice.Candidate) {
		if c != nil {
			mu.Lock()
			_ = agentA.AddRemoteCandidate(c)
			mu.Unlock()
		}
	})
	if err != nil {
		t.Fatalf("onCandidate B: %v", err)
	}

	if err := agentA.GatherCandidates(); err != nil {
		t.Fatalf("gather A: %v", err)
	}
	if err := agentB.GatherCandidates(); err != nil {
		t.Fatalf("gather B: %v", err)
	}

	type connResult struct {
		conn *ice.Conn
		err  error
	}
	resChanA := make(chan connResult, 1)
	resChanB := make(chan connResult, 1)

	go func() {
		c, err := agentA.Dial(ctx, ufragB, pwdB)
		resChanA <- connResult{conn: c, err: err}
	}()

	go func() {
		c, err := agentB.Accept(ctx, ufragA, pwdA)
		resChanB <- connResult{conn: c, err: err}
	}()

	resA := <-resChanA
	if resA.err != nil {
		t.Fatalf("agentA Dial error: %v", resA.err)
	}
	defer resA.conn.Close()

	resB := <-resChanB
	if resB.err != nil {
		t.Fatalf("agentB Accept error: %v", resB.err)
	}
	defer resB.conn.Close()

	tlsConf, err := GenerarTLSConfig()
	if err != nil {
		t.Fatalf("tls config: %v", err)
	}

	quicConf := &quic.Config{
		MaxIdleTimeout: 10 * time.Second,
	}

	pConnB := &icePacketConn{conn: resB.conn}
	trB := &quic.Transport{Conn: pConnB}
	defer trB.Close()

	listener, err := trB.Listen(tlsConf, quicConf)
	if err != nil {
		t.Fatalf("quic listen: %v", err)
	}
	defer listener.Close()

	pConnA := &icePacketConn{conn: resA.conn}
	trA := &quic.Transport{Conn: pConnA}
	defer trA.Close()

	clientTLS := tlsConf.Clone()
	clientTLS.InsecureSkipVerify = true

	quicConnChan := make(chan *quic.Conn, 1)
	go func() {
		qConn, err := listener.Accept(ctx)
		if err != nil {
			t.Errorf("listener accept: %v", err)
			return
		}
		quicConnChan <- qConn
	}()

	qConnA, err := trA.Dial(ctx, resA.conn.RemoteAddr(), clientTLS, quicConf)
	if err != nil {
		t.Fatalf("quic dial: %v", err)
	}
	defer qConnA.CloseWithError(0, "done")

	qConnB := <-quicConnChan
	defer qConnB.CloseWithError(0, "done")

	streamChan := make(chan *quic.Stream, 1)
	go func() {
		str, err := qConnB.AcceptStream(ctx)
		if err != nil {
			t.Errorf("accept stream: %v", err)
			return
		}
		streamChan <- str
	}()

	streamA, err := qConnA.OpenStreamSync(ctx)
	if err != nil {
		t.Fatalf("open stream sync: %v", err)
	}
	defer streamA.Close()

	msg := []byte("HOLA COMPUSHARE QUIC SOBRE ICE")
	if _, err := streamA.Write(msg); err != nil {
		t.Fatalf("stream write: %v", err)
	}

	streamB := <-streamChan
	defer streamB.Close()

	buf := make([]byte, len(msg))
	if _, err := io.ReadFull(streamB, buf); err != nil {
		t.Fatalf("stream read: %v", err)
	}

	if string(buf) != string(msg) {
		t.Fatalf("mismatch: got %q, want %q", string(buf), string(msg))
	}
	t.Logf("Exito: datos transmitidos correctamente sobre QUIC encapsulado en Pion ICE")
}
