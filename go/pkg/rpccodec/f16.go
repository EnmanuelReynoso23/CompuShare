package rpccodec

import "math"

// F32ToF16 convierte un float32 a IEEE 754 binario16 con redondeo al más cercano y
// empates al par. Los valores fuera de rango dan infinito; NaN se conserva como NaN.
func F32ToF16(f float32) uint16 {
	b := math.Float32bits(f)
	signo := uint16(b>>16) & 0x8000
	exp := int((b >> 23) & 0xff)
	mant := b & 0x7fffff

	if exp == 0xff {
		if mant != 0 {
			return signo | 0x7e00
		}
		return signo | 0x7c00
	}
	if exp == 0 {
		// float32 subnormal: muy por debajo del rango de float16.
		return signo
	}

	e := exp - 127
	if e > 15 {
		return signo | 0x7c00
	}
	if e < -25 {
		return signo
	}

	mant |= 0x800000 // significando de 24 bits con el bit implícito
	if e >= -14 {
		// Normal en float16: el bit implícito cae en el bit 10 de medio y se suma al exponente.
		const s = 13
		medio := mant >> s
		resto := mant & (1<<s - 1)
		mitad := uint32(1) << (s - 1)
		if resto > mitad || (resto == mitad && medio&1 == 1) {
			medio++
		}
		return signo | uint16(uint32(e+14)<<10+medio)
	}

	// Subnormal en float16: unidades de 2^-24.
	s := uint(-1 - e)
	medio := mant >> s
	resto := mant & (1<<s - 1)
	mitad := uint32(1) << (s - 1)
	if resto > mitad || (resto == mitad && medio&1 == 1) {
		medio++
	}
	return signo | uint16(medio)
}

// F16ToF32 convierte un float16 (IEEE 754 binario16) a float32. Es exacta.
func F16ToF32(h uint16) float32 {
	signo := uint32(h&0x8000) << 16
	exp := uint32(h>>10) & 0x1f
	mant := uint32(h & 0x3ff)
	switch exp {
	case 0:
		v := float32(mant) * (1.0 / (1 << 24))
		if signo != 0 {
			v = -v
		}
		return v
	case 0x1f:
		return math.Float32frombits(signo | 0x7f800000 | mant<<13)
	}
	return math.Float32frombits(signo | (exp+112)<<23 | mant<<13)
}
