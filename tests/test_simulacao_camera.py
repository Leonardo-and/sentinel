"""A câmera virtual tem que falar a língua do ``VisaoCor`` de verdade.

O ponto do arquivo é a integração, não o desenho: um ``CameraVirtual`` que
funciona sozinho mas não serve uma ``VisaoConfig`` não serve para nada. Por
isso os testes usam ``load_visao()`` e ``VisaoCor`` do pacote — os mesmos que
a rotina ``--debug-visao`` usa.
"""

from __future__ import annotations

import pytest

from sentinel import SoBot, load_config, load_visao
from sentinel.simulacao.camera import CameraVirtual
from sentinel.simulacao.pista import Arena, Montagem, Objeto, exemplo
from sentinel.simulacao.placa import PlacaVirtual, RelogioFake
from sentinel.visao import VisaoCor

cv2 = pytest.importorskip("cv2", reason="precisa do extra [visao]")
np = pytest.importorskip("numpy", reason="precisa do extra [visao]")


# --- helpers ----------------------------------------------------------------
def placa(arena: Arena | None = None) -> PlacaVirtual:
    return PlacaVirtual(arena if arena is not None else exemplo(), relogio=RelogioFake())


def arena_com_pallet(distancia: float = 900.0) -> Arena:
    """Sala de teste: o robô olhando +x e um pallet vermelho bem na frente."""
    return Arena(
        largura=4000.0,
        altura=3000.0,
        objetos=(Objeto(500.0 + distancia, 1000.0, 300.0, 220.0, "vermelho", "pallet1"),),
        montagem=Montagem(500.0, 1000.0, 0.0),
    )


def visao(placa_: PlacaVirtual, **kwargs) -> VisaoCor:
    """``VisaoCor`` real apontada para a câmera virtual."""
    cfg = load_visao()
    return VisaoCor(cfg, captura=CameraVirtual(placa_, **kwargs))


def pixel_da_cor(frame, cor_bgr: tuple[int, int, int]) -> int:
    """Quantos pixels batem exatamente com a cor procurada."""
    return int(np.all(np.asarray(frame) == np.array(cor_bgr, dtype=np.uint8), axis=-1).sum())


# --- a superfície de VideoCapture --------------------------------------------
def test_a_camera_abre_e_esta_sempre_pronta():
    p = placa()
    cam = CameraVirtual(p)
    assert cam.isOpened() is True
    assert cam.set(cv2.CAP_PROP_FRAME_WIDTH, 320) is True
    ok, frame = cam.read()
    assert ok is True
    assert frame is not None
    cam.release()
    p.close()


def test_o_quadro_tem_a_resolucao_do_visao_config():
    cfg = load_visao()
    p = placa()
    cam = CameraVirtual(p, largura=cfg.largura, altura=cfg.altura)
    _, frame = cam.read()
    assert frame.shape[:2] == (cfg.altura, cfg.largura)
    assert frame.dtype == np.uint8
    p.close()


def test_o_mesmo_quadro_volta_enquanto_o_robo_nao_se_move():
    """Reprojetar a cada ``read`` é desperdício numa malha de visão."""
    p = placa()
    cam = CameraVirtual(p)
    _, primeiro = cam.read()
    _, segundo = cam.read()
    assert primeiro is segundo, "a pose não mudou: devolve o quadro guardado"
    p.close()


def test_mexer_no_robo_faz_o_quadro_mudar():
    p = placa()
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.command_return(True, "MT0")
    b.wait_for("CR OK MT0")
    b.wheels_enable(True)
    cam = CameraVirtual(p)
    _, antes = cam.read()
    b.move(200, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    _, depois = cam.read()
    assert antes is not depois, "o robô andou: o quadro tem que ser reprojetado"
    assert not np.array_equal(antes, depois)
    p.close()


def test_release_esqueca_o_quadro_guardado():
    p = placa()
    cam = CameraVirtual(p)
    _, primeiro = cam.read()
    cam.release()
    _, segundo = cam.read()
    assert primeiro is not segundo
    p.close()


# --- o desenho ---------------------------------------------------------------
def test_a_sala_vazia_tem_so_parede_e_chao():
    """Sem objeto nenhum, a cena é o fundo (parede) em cima e o chão embaixo."""
    p = placa(Arena(largura=4000.0, altura=4000.0, montagem=Montagem(500.0, 2000.0, 0.0)))
    _, frame = CameraVirtual(p).read()
    saida = np.asarray(frame)
    cores = {tuple(int(v) for v in px) for px in np.unique(saida.reshape(-1, 3), axis=0)}
    assert len(cores) == 2, f"a cena devia ter 2 cores chapadas, tem {len(cores)}: {cores}"
    fundo, chao = saida[0, 0], saida[-1, 0]
    assert not np.array_equal(fundo, chao), "parede e chão não podem ser a mesma cor"
    p.close()


def test_a_parede_aparece_cinza_e_o_chao_com_a_cor_do_piso():
    p = placa(Arena(largura=4000.0, altura=4000.0, montagem=Montagem(500.0, 2000.0, 0.0)))
    _, frame = CameraVirtual(p).read()
    saida = np.asarray(frame)
    assert saida.ndim == 3 and saida.shape[2] == 3
    # A parede ocupa a faixa de cima, o chão a de baixo — e são bem diferentes.
    assert not np.array_equal(saida[:20, :, :], saida[-20:, :, :])
    p.close()


def test_o_objeto_aparece_da_cor_certa():
    p = placa(arena_com_pallet(distancia=900.0))
    _, frame = CameraVirtual(p).read()
    vermelho = (0, 0, 220)  # BGR
    assert pixel_da_cor(frame, vermelho) > 200, "o pallet vermelho não apareceu"
    p.close()


def test_a_cena_tem_um_teto_de_ciu_e_nao_parede_e_piso():
    """Inclinar a câmera para baixo não pode pintar o céu de parede."""
    p = placa(arena_com_pallet(distancia=900.0))
    _, frame = CameraVirtual(p).read()
    saida = np.asarray(frame)
    ceu = {tuple(int(v) for v in px) for px in saida[0, ::7, :]}
    assert len(ceu) <= 2, f"o céu não é uniforme: {ceu}"
    p.close()


def test_o_que_esta_na_garra_some_do_quadro():
    """Pegou o pallet: ele sai do chão, e o chão é o que a câmera vê."""
    p = placa(arena_com_pallet(distancia=260.0))
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.command_return(True, "MT0")
    b.wait_for("CR OK MT0")
    b.wheels_enable(True)
    cam = CameraVirtual(p)
    _, antes = cam.read()
    vermelho_antes = pixel_da_cor(antes, (0, 0, 220))
    b.elevator("up")
    b.digital_output(5, False)
    b.elevator("down")
    b.digital_output(5, True)
    p._esperar_muita(0.05)
    assert p.estado().segure is not None, "o pallet deveria estar na garra"
    _, depois = cam.read()
    vermelho_depois = pixel_da_cor(depois, (0, 0, 220))
    assert vermelho_depois < vermelho_antes, "o pallet continua no chão na imagem"
    p.close()


# --- a integração com o VisaoCor --------------------------------------------
def test_o_visao_cor_enxerga_o_pallet_vermelho():
    p = placa(arena_com_pallet(distancia=900.0))
    v = visao(p)
    v.selecionar("vermelho")
    deteccao = v.detectar()
    assert deteccao.achou is True
    assert deteccao.area > 200
    assert deteccao.largura == load_visao().largura
    p.close()


def test_o_visao_cor_nao_enxerga_quando_o_pallet_sumiu():
    p = placa(arena_com_pallet(distancia=900.0))
    v = visao(p)
    v.selecionar("vermelho")
    assert v.detectar().achou is True
    # Tira o pallet da arena: a mesma cor não pode mais aparecer.
    p._objetos.clear()
    assert v.detectar().achou is False
    p.close()


def test_o_desvio_aponta_para_o_lado_do_pallet():
    """Pallet à direita do eixo ⇒ desvio positivo (mesma convenção de antes)."""
    arena = Arena(
        largura=4000.0,
        altura=4000.0,
        objetos=(Objeto(1400.0, 1500.0, 300.0, 220.0, "vermelho", "pallet1"),),
        montagem=Montagem(500.0, 1000.0, 0.0),
    )
    p = placa(arena)
    v = visao(p)
    v.selecionar("vermelho")
    d = v.detectar()
    assert d.achou is True
    assert d.desvio > 0, f"o pallet está à direita, o desvio deu {d.desvio}"
    p.close()


def test_amostrar_so_confirma_depois_de_n_frames_seguidos():
    p = placa(arena_com_pallet(distancia=900.0))
    v = visao(p)
    v.selecionar("vermelho")
    janela = load_visao().frames_estaveis
    for i in range(janela - 1):
        assert v.amostrar().achou is False, f"confirmou antes da janela (frame {i})"
    assert v.amostrar().achou is True
    assert v.amostrar().estavel is True
    p.close()


def test_a_camera_se_adapta_a_resolucao_do_visao_config():
    """A câmera herda largura/altura do ``VisaoConfig`` quando não recebe nada."""
    cfg = load_visao()
    p = placa()
    v = visao(p)
    _, frame = v._captura.read()
    assert frame.shape[:2] == (cfg.altura, cfg.largura)
    p.close()


def test_medir_aceita_um_frame_salvo_tambem():
    """O ``VisaoCor.medir`` é o mesmo caminho da calibração por foto."""
    p = placa(arena_com_pallet(distancia=900.0))
    v = visao(p)
    v.selecionar("vermelho")
    _, frame = CameraVirtual(p).read()
    assert v.medir(frame).achou is True
    p.close()


def test_a_garra_transporta_o_pallet_e_a_camera_para_de_ver():
    """Fim a fim: pega, anda com o pallet e a imagem some com ele."""
    arena = Arena(
        largura=5000.0,
        altura=3000.0,
        objetos=(Objeto(760.0, 1000.0, 300.0, 220.0, "vermelho", "pallet1"),),
        montagem=Montagem(500.0, 1000.0, 0.0),
    )
    p = placa(arena)
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.command_return(True, "MT0")
    b.wait_for("CR OK MT0")
    b.wheels_enable(True)
    v = visao(p)
    v.selecionar("vermelho")
    assert v.detectar().achou is True

    b.elevator("up")
    b.digital_output(5, False)
    b.digital_output(5, True)  # com a garra em cima não pega
    b.elevator("down")
    b.digital_output(5, False)
    b.digital_output(5, True)
    p._esperar_muita(0.05)
    assert p.estado().segure is not None
    assert v.detectar().achou is False, "o pallet foi levantado, mas a câmera ainda o vê"
    p.close()
