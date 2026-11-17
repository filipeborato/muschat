"""Shared acoustic conversions and microtonal harmonic calculations."""

import math


def midicents_to_hz(midicents: float) -> float:
    return 440.0 * (2 ** ((midicents - 6900) / 1200))


def hz_to_midicents(frequency: float) -> float:
    return 6900 + 1200 * math.log2(frequency / 440)


def quantization_step(microtone: int) -> int:
    return 100 // (2**microtone)


def quantize(midicents: float, microtone: int) -> int:
    step = quantization_step(microtone)
    return round(midicents / step) * step


def partials(fundamental: float, count: int, microtone: int) -> list[int]:
    return [
        quantize(fundamental + 1200 * math.log2(n), microtone)
        for n in range(1, count + 1)
    ]
