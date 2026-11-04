package p2p

import (
	"context"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/quic-go/quic-go"
)

type MetricasPuente struct {
	BytesEnviados  int64
	BytesRecibidos int64
	Conexiones     int64
}

func PuenteBidireccional(rw1 io.ReadWriteCloser, rw2 io.ReadWriteCloser, m *MetricasPuente) {
	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		buf := make([]byte, 64*1024)
		for {
			n, err := rw1.Read(buf)
			if n > 0 {
				if m != nil {
					atomic.AddInt64(&m.BytesEnviados, int64(n))
				}
				if _, wErr := rw2.Write(buf[:n]); wErr != nil {
					break
				}
			}
			if err != nil {
				break
			}
		}
		_ = rw2.Close()
	}()

	go func() {
		defer wg.Done()
		buf := make([]byte, 64*1024)
		for {
			n, err := rw2.Read(buf)
			if n > 0 {
				if m != nil {
					atomic.AddInt64(&m.BytesRecibidos, int64(n))
				}
				if _, wErr := rw1.Write(buf[:n]); wErr != nil {
					break
				}
			}
			if err != nil {
				break
			}
		}
		_ = rw1.Close()
	}()

	wg.Wait()
}

func IniciarPuenteMaestro(ctx context.Context, listenAddr string, canal *CanalP2P, m *MetricasPuente) (net.Listener, error) {
	ln, err := net.Listen("tcp", listenAddr)
	if err != nil {
		return nil, fmt.Errorf("error escuchando en %s: %w", listenAddr, err)
	}

	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()

	go func() {
		for {
			tcpConn, err := ln.Accept()
			if err != nil {
				return
			}
			if m != nil {
				atomic.AddInt64(&m.Conexiones, 1)
			}

			go func(c net.Conn) {
				defer c.Close()
				stream, err := canal.QuicConn.OpenStreamSync(ctx)
				if err != nil {
					return
				}
				defer stream.Close()
				PuenteBidireccional(c, stream, m)
			}(tcpConn)
		}
	}()

	return ln, nil
}

func IniciarPuenteTrabajador(ctx context.Context, targetAddr string, canal *CanalP2P, m *MetricasPuente) error {
	for {
		stream, err := canal.QuicConn.AcceptStream(ctx)
		if err != nil {
			return err
		}
		if m != nil {
			atomic.AddInt64(&m.Conexiones, 1)
		}

		go func(s *quic.Stream) {
			defer s.Close()
			tcpConn, err := net.DialTimeout("tcp", targetAddr, 3*time.Second)
			if err != nil {
				return
			}
			defer tcpConn.Close()
			PuenteBidireccional(s, tcpConn, m)
		}(stream)
	}
}
