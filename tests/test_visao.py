"""Visão: faixa HSV (incluindo a quebra do vermelho no zero), centroide e estabilidade."""
from __future__ import annotations

import pytest

from sentinel import FaixaHSV, VisaoConfig, load_visao
from sentinel.visao import Deteccao, VisaoCor

VERMELHO = FaixaHSV(h_min=170, h_max=10, s_min=120, v_min=40)


# --- FaixaHSV: lógica pura, sem OpenCV --------------------------------------
def test_faixa_contem_cor_dentro():
    assert VERMELHO.contem(h=0, s=200, v=120)
    assert VERMELHO.contem(h=175, s=200, v=120)


def test_faixa_contem_corta_matiz_de_outra_cor():
    assert not VERMELHO.contem(h=90, s=200, v=120)     # verde
    assert not VERMELHO.contem(h=2, s=30, v=120)       # pouco saturado = cinza
    assert not VERMELHO.contem(h=2, s=200, v=20)       # escuro demais


def test_faixa_continua_nao_embaralha():
    faixa = FaixaHSV(h_min=100, h_max=130)
    assert faixa.contem(100, 0, 0) and faixa.contem(130, 0, 0)
    assert not faixa.contem(99, 0, 0) and not faixa.contem(131, 0, 0)


def test_limites_do_vermelho_sao_duas_faixas():
    assert VERMELHO.limites() == [([0, 120, 40], [10, 255, 255]),
                                   ([170, 120, 40], [179, 255, 255])]


def test_limites_de_faixa_continua_sao_um_par():
    assert FaixaHSV(h_min=40, h_max=85).limites() == [([40, 0, 0], [85, 255, 255])]


def test_faixa_rejeita_chave_desconhecida():
    with pytest.raises(ValueError, match="h_mid"):
        FaixaHSV.de_dict({"h_mid": 10})


def test_faixa_rejeita_valor_fora_de_escala():
    with pytest.raises(ValueError, match="h_max"):
        FaixaHSV(h_min=0, h_max=180)          # OpenCV vai até 179
    with pytest.raises(ValueError, match="s_max"):
        FaixaHSV(s_min=0, s_max=300)


def test_faixa_rejeita_faixa_invertida_de_saturacao():
    with pytest.raises(ValueError, match="invertida"):
        FaixaHSV(s_min=200, s_max=100)


# --- VisaoConfig -------------------------------------------------------------
def test_cor_desconhecida_fala_quais_existem():
    with pytest.raises(ValueError, match="vermelho"):
        VisaoConfig().faixa("magentao")


def test_config_rejeita_area_invertida():
    with pytest.raises(ValueError, match="área"):
        VisaoConfig(area_min=500, area_max=100)


def test_config_rejeita_kernel_par():
    with pytest.raises(ValueError, match="abrir_kernel"):
        VisaoConfig(abrir_kernel=4)


# --- Deteccao ----------------------------------------------------------------
def test_desvio_e_zero_no_centro():
    assert Deteccao(achou=True, cx=160, largura=320).desvio == 0


def test_desvio_muda_de_sinal_com_o_lado():
    assert Deteccao(achou=True, cx=200, largura=320).desvio == 40
    assert Deteccao(achou=True, cx=120, largura=320).desvio == -40


def test_sem_alvo_desvio_zero():
    assert Deteccao(achou=False, largura=320).desvio == 0


# --- com imagem de verdade (OpenCV + numpy) ----------------------------------
np = pytest.importorskip("numpy")
pytest.importorskip("cv2")


def _quadro(largura=320, altura=240, fundo=(30, 30, 30)):
    """Imagem BGR escura (fora de qualquer faixa de cor saturada)."""
    return np.full((altura, largura, 3), fundo, dtype=np.uint8)


def _retangulo(quadro, cor, x0, y0, x1, y1):
    """Pinta um bloco BGR e devolve o mesmo quadro (in place)."""
    quadro[y0:y1, x0:x1] = cor
    return quadro


def _cam(cfg: VisaoConfig) -> VisaoCor:
    return VisaoCor(cfg)


def _sem_kernel(**kwargs) -> VisaoConfig:
    return VisaoConfig(abrir_kernel=0, fechar_kernel=0, **kwargs)


def test_mede_o_bloco_vermelho_e_diz_o_desvio():
    cam = _cam(_sem_kernel())
    cam.selecionar("vermelho")
    quadro = _retangulo(_quadro(), (0, 0, 255), 180, 100, 220, 140)   # 40x40 BGR

    deteccao = cam.medir(quadro)

    assert deteccao.achou
    assert deteccao.area == 1600
    assert deteccao.cx == pytest.approx(200, abs=1)
    assert deteccao.cy == pytest.approx(120, abs=1)
    assert deteccao.desvio == pytest.approx(40, abs=1)
    assert deteccao.largura == 320


def test_nao_confunde_outra_cor():
    cam = _cam(_sem_kernel())
    cam.selecionar("vermelho")
    quadro = _retangulo(_quadro(), (255, 0, 0), 100, 100, 200, 200)   # azul BGR

    assert not cam.medir(quadro).achou


def test_bloco_pequeno_daixa_de_area():
    cam = _cam(_sem_kernel(area_min=300))
    cam.selecionar("vermelho")
    quadro = _retangulo(_quadro(), (0, 0, 255), 100, 100, 105, 105)   # 5x5 = 25 px

    deteccao = cam.medir(quadro)

    assert not deteccao.achou and deteccao.area == 0
    assert deteccao.largura == 320          # o quadro é conhecido mesmo sem alvo


def test_bloco_grande_daixa_de_area_maxima():
    cam = _cam(_sem_kernel(area_min=50, area_max=100))
    cam.selecionar("vermelho")
    quadro = _retangulo(_quadro(), (0, 0, 255), 10, 10, 200, 200)

    assert not cam.medir(quadro).achou


def test_kernel_ignora_ruido_de_1_pixel():
    quadro = _quadro()
    _retangulo(quadro, (0, 0, 255), 150, 110, 200, 150)     # bloco de verdade
    quadro[20:60, 300] = (0, 0, 255)                        # fio de 1 px de largura

    cam = _cam(VisaoConfig(abrir_kernel=5, fechar_kernel=5))
    cam.selecionar("vermelho")
    deteccao = cam.medir(quadro)

    assert deteccao.achou
    assert deteccao.cx == pytest.approx(175, abs=3)   # puxado só pelo bloco


def test_quadro_vazio_nao_inventa_alvo():
    cam = _cam(_sem_kernel())
    cam.selecionar("vermelho")

    assert not cam.medir(_quadro()).achou


# --- leitura da câmera e estabilidade ---------------------------------------
class CapturaFalsa:
    """Camera que devolve uma lista de quadros e depois frame nenhum."""

    def __init__(self, quadros):
        self.quadros = list(quadros)
        self.liberada = False

    def read(self):
        if self.quadros:
            return True, self.quadros.pop(0)
        return False, None

    def release(self):
        self.liberada = True


def _vermelho(x0=150, tamanho=50):
    return _retangulo(_quadro(), (0, 0, 255), x0, 100, x0 + tamanho, 150)


def _escuro():
    return _quadro()


def test_amostrar_so_confirma_depois_de_n_frames_seguidos():
    capturado = CapturaFalsa([_vermelho(), _vermelho(), _vermelho(), _vermelho()])
    cam = VisaoCor(_sem_kernel(frames_estaveis=3), captura=capturado)
    cam.selecionar("vermelho")

    assert not cam.amostrar().achou      # 1º frame: ainda acumulando
    assert not cam.amostrar().achou      # 2º
    assert cam.amostrar().achou          # 3º: confirmado

    (confirmada,) = [cam.amostrar()]     # 4º continua estável
    assert confirmada.estavel


def test_amostrar_recomeca_quando_o_alvo_some():
    quadros = [_vermelho(), _vermelho(), _escuro(), _vermelho(), _vermelho()]
    cam = VisaoCor(_sem_kernel(frames_estaveis=2), captura=CapturaFalsa(quadros))
    cam.selecionar("vermelho")

    confirmado = [cam.amostrar().achou for _ in range(5)]

    assert confirmado == [False, True, False, False, True]   # a janela zera ao perder


def test_trocar_de_cor_zera_a_estabilidade():
    capturado = CapturaFalsa([_vermelho(), _vermelho(), _vermelho()])
    cam = VisaoCor(_sem_kernel(frames_estaveis=2), captura=capturado)
    cam.selecionar("vermelho")
    cam.amostrar()

    assert cam.selecionar("azul") == VisaoConfig().cores["azul"]
    assert not cam.amostrar().achou


def test_detectar_sem_cor_escolhida_exige_argumento():
    with pytest.raises(ValueError, match="informe a cor"):
        VisaoCor(_sem_kernel(), captura=CapturaFalsa([])).detectar()


def test_detectar_avisa_quando_a_camera_entrega_nada():
    cam = VisaoCor(_sem_kernel(), captura=CapturaFalsa([]))
    deteccao = cam.detectar("vermelho")

    assert not deteccao.achou
    assert deteccao.largura == VisaoConfig.largura


def test_context_manager_abre_e_fecha_a_camera():
    capturada = CapturaFalsa([])

    with VisaoCor(_sem_kernel(), captura=capturada) as cam:
        assert cam.abrir() is cam

    assert capturada.liberada


def test_abrir_falha_com_mensagem_legivel():
    with pytest.raises(RuntimeError, match="/dev/video3"):
        VisaoCor(VisaoConfig(dispositivo=3), captura=None).abrir()


# --- leitura do TOML ---------------------------------------------------------
def test_load_visao_sem_arquivo_usa_padrao(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINEL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    cfg = load_visao()

    assert cfg.dispositivo == 0
    assert cfg.cores["vermelho"].h_min == 170
    assert "azul" in cfg.cores


def test_load_visao_une_a_cor_do_toml_com_o_padrao(tmp_path, monkeypatch):
    (tmp_path / "sentinel.toml").write_text(
        "[visao]\narea_min = 900\nframes_estaveis = 5\n\n"
        "[visao.cores.vermelho]\ns_min = 200\nh_min = 165\nh_max = 5\n"
    )
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "sentinel.toml"))

    cfg = load_visao()

    assert cfg.area_min == 900 and cfg.frames_estaveis == 5
    vermelho = cfg.cores["vermelho"]
    assert (vermelho.h_min, vermelho.h_max, vermelho.s_min) == (165, 5, 200)
    assert vermelho.v_min == 40                      # vindo do padrão
    assert "azul" in cfg.cores                       # cor não citada continua padrão


def test_load_visao_rejeita_chave_desconhecida(tmp_path, monkeypatch):
    (tmp_path / "sentinel.toml").write_text("[visao]\nprofundidade = 3\n")
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "sentinel.toml"))

    with pytest.raises(ValueError, match="profundidade"):
        load_visao()
