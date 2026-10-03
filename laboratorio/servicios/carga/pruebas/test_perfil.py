"""cd laboratorio/servicios/carga && python -m pytest pruebas/"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from perfil import Perfil

PERFILES = Path(__file__).resolve().parents[3] / "perfiles"


def test_el_perfil_diurno_cumple_la_amplitud_de_h023():
    p = Perfil.cargar(PERFILES / "diurno.json")
    assert p.amplitud() >= 3
    # medido como lo mediria la prueba P-LAB-01: tasa por hora a lo largo de un martes
    martes = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)  # 00:00 en Colombia
    por_hora = [p.base_rps * p.factor_hora(martes + timedelta(hours=h)) for h in range(24)]
    assert max(por_hora) / min(por_hora) >= 3


def test_valle_de_madrugada_y_pico_de_tarde_en_hora_de_colombia():
    p = Perfil.cargar(PERFILES / "diurno.json")
    madrugada = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)   # 03:00 local
    tarde = datetime(2026, 10, 6, 20, 30, tzinfo=timezone.utc)     # 15:30 local
    assert p.factor_hora(tarde) / p.factor_hora(madrugada) >= 3.5


def test_fin_de_semana_mas_bajo():
    p = Perfil.cargar(PERFILES / "diurno.json")
    sabado = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    viernes = sabado - timedelta(days=1)
    assert p.factor_dia(sabado) == 0.6 and p.factor_dia(viernes) == 1.0


def test_interpola_sin_escalones():
    p = Perfil(base_rps=1, horas=tuple([1.0] * 12 + [2.0] * 12))
    medio = datetime(2026, 10, 6, 11, 30, tzinfo=timezone.utc)
    assert p.factor_hora(medio) == pytest.approx(1.5)


def test_ruido_reproducible_y_acotado():
    p = Perfil.cargar(PERFILES / "diurno.json")
    t = datetime(2026, 10, 6, 14, 7, 12, tzinfo=timezone.utc)
    assert p.factor_ruido(t) == p.factor_ruido(t.replace(second=50))  # mismo minuto, mismo ruido
    valores = [p.factor_ruido(t + timedelta(minutes=m)) for m in range(500)]
    assert 0.9 <= min(valores) and max(valores) <= 1.1 and len(set(valores)) > 400


def test_perfil_invalido():
    with pytest.raises(ValueError):
        Perfil(base_rps=1, horas=(1.0,) * 23)
