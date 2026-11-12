"""PR-10: fórmula de espera estimada."""

from app.services.estimacion_service import estimar_espera_min


def test_aplica_la_formula():
    # 5 personas · 4 min / (2 ventanillas · (1 − 0.2)) = 12.5 min
    assert estimar_espera_min(5, 4.0, 2, 0.2, 2, es_preferencial=False) == 12.5


def test_un_preferencial_no_espera_por_otros_preferenciales():
    assert estimar_espera_min(5, 4.0, 2, 0.2, 2, es_preferencial=True) == 10.0


def test_p_no_supera_k_sobre_k_mas_uno():
    # p = 0.9 se limita a 2/3: 5 · 4 / (2 · 1/3) = 30 min
    assert estimar_espera_min(5, 4.0, 2, 0.9, 2, es_preferencial=False) == 30.0


def test_sin_ventanillas_no_hay_estimacion():
    assert estimar_espera_min(5, 4.0, 0, 0.2, 2, es_preferencial=False) is None
