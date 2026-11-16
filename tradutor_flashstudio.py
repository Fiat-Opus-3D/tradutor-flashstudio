# -*- coding: utf-8 -*-
"""
Tradutor PT-BR para Flash Studio (Flashforge)
Fiat Opus 3D — https://github.com/Fiat-Opus-3D/tradutor-flashstudio

O que faz:
  1. Baixa a tradução mais recente das frases exclusivas da Flashforge (repositório da Fiat Opus 3D)
     e a pt_BR do OrcaSlicer e do Bambu Studio (GitHub); usa também o Bambu Studio instalado, se houver.
     Sem internet, usa a cópia em cache ou a que vem dentro do .exe.
  2. Valida cada frase (placeholders como %s, %d, %1%, {nome}) para não travar o programa.
  3. Junta tudo num único arquivo .mo. Frase sem tradução válida fica de fora,
     e o Flash Studio mostra o texto original em inglês (comportamento padrão do gettext).
  4. Instala na pasta de idiomas do Flash Studio, sempre com backup do original.
  5. Permite restaurar tudo ao estado original.

Uso:
  tradutor_flashstudio.exe              -> menu interativo
  tradutor_flashstudio.exe --instalar   -> instala/atualiza sem perguntas
  tradutor_flashstudio.exe --restaurar  -> desfaz tudo
  tradutor_flashstudio.exe --pasta "C:\\caminho\\Flash Studio"  -> informa a pasta manualmente
"""

import argparse
import ctypes
import datetime
import gettext
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

try:
    import polib
except ImportError:
    print("Biblioteca 'polib' não encontrada. Rode: pip install polib")
    sys.exit(1)

VERSAO = "1.2.0"
IDIOMA = "pt_BR"
IS_WINDOWS = os.name == "nt"

# Fontes remotas, da mais completa para a menos completa
FONTES_REMOTAS = [
    ("OrcaSlicer (GitHub)",
     "https://raw.githubusercontent.com/OrcaSlicer/OrcaSlicer/main/localization/i18n/pt_BR/OrcaSlicer_pt_BR.po"),
    ("Bambu Studio (GitHub)",
     "https://raw.githubusercontent.com/bambulab/BambuStudio/master/bbl/i18n/pt_BR/BambuStudio_pt_BR.po"),
]

# Traduções das frases exclusivas da Flashforge: baixadas a cada execução, então um .exe antigo
# continua pegando as frases das versões novas do Flash Studio sem precisar ser trocado.
REPO = "Fiat-Opus-3D/tradutor-flashstudio"
URL_REPO = f"https://raw.githubusercontent.com/{REPO}/main"
FONTE_FLASHFORGE = ("Traduções Flashforge (Fiat Opus 3D)", f"{URL_REPO}/traducoes_flashforge.po")
# {"atual": "1.2.0", "minima": "1.2.0"}: abaixo da mínima o programa não instala (mudou algo que o .exe velho não sabe fazer)
URL_VERSAO = f"{URL_REPO}/versao.json"
PAGINA_DOWNLOAD = f"https://github.com/{REPO}/releases/latest"

PLURAL_PT_BR = "nplurals=2; plural=(n > 1);"

ACAO_ATUAL = None   # ação em andamento, repassada ao relançar como admin
RELANCADO = False


# ---------------------------------------------------------------- utilidades

def base_dados() -> Path:
    raiz = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    p = Path(raiz) / "TradutorFlashStudio"
    p.mkdir(parents=True, exist_ok=True)
    return p


DADOS = base_dados()
# pasta onde está o .exe (para personalizado.po e pendentes.po)
PASTA_EXE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
# pasta dos arquivos embutidos no .exe pelo PyInstaller (ao rodar o .py, a própria pasta do script)
PASTA_RECURSOS = Path(getattr(sys, "_MEIPASS", PASTA_EXE))
ARQ_EMBUTIDO = "traducoes_flashforge.po"
CACHE = DADOS / "cache"
BACKUP = DADOS / "backup"
ESTADO_ARQ = DADOS / "estado.json"
LOG_ARQ = DADOS / "log.txt"


def log(msg: str = "") -> None:
    print(msg)
    try:
        with open(LOG_ARQ, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_estado() -> dict:
    if ESTADO_ARQ.exists():
        try:
            return json.loads(ESTADO_ARQ.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log("Aviso: estado.json ilegível, começando do zero (backups antigos continuam na pasta).")
    return {"arquivos": {}, "config": None, "fontes": {}}


def salvar_estado(estado: dict) -> None:
    tmp = ESTADO_ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ESTADO_ARQ)


def perguntar(texto: str, padrao: bool = True, auto: bool = False) -> bool:
    if auto:
        return padrao
    sufixo = " [S/n] " if padrao else " [s/N] "
    while True:
        r = input(texto + sufixo).strip().lower()
        if not r:
            return padrao
        if r in ("s", "sim", "y"):
            return True
        if r in ("n", "nao", "não"):
            return False


def preparar_console() -> None:
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------- admin / processos

def eh_admin() -> bool:
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def pasta_gravavel(pasta: Path) -> bool:
    # Uma tentativa só. (tempfile.mkstemp no Windows fica em loop quase infinito
    # quando a pasta é protegida, como Program Files sem administrador.)
    nome = Path(pasta) / f".teste_{os.getpid()}_{os.urandom(4).hex()}"
    try:
        fd = os.open(nome, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        os.remove(nome)
        return True
    except OSError:
        return False


def reabrir_como_admin() -> None:
    """Relança o programa pedindo permissão de administrador (UAC), mantendo a ação escolhida."""
    global RELANCADO
    args = list(sys.argv[1:])
    if ACAO_ATUAL and "--acao" not in args:
        args += ["--acao", ACAO_ATUAL]
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:
        exe = sys.executable
        args = [os.path.abspath(sys.argv[0])] + args
    params = subprocess.list2cmdline(args)
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if r <= 32:
        log("Permissão de administrador negada ou indisponível.")
        sys.exit(1)
    log("Continuando numa nova janela com permissão de administrador...")
    RELANCADO = True
    sys.exit(0)


def processos_rodando(nomes_exe: list) -> list:
    if not IS_WINDOWS or not nomes_exe:
        return []
    try:
        saida = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                               text=True, errors="replace", timeout=20).stdout.lower()
    except Exception:
        return []
    return [n for n in nomes_exe if f'"{n.lower()}"' in saida]


# ------------------------------------------------------- localizar instalações

def buscar_registro(trecho: str) -> list:
    """Procura programas instalados cujo nome contenha 'trecho'."""
    if not IS_WINDOWS:
        return []
    import winreg
    achados = []
    chaves = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for raiz, caminho in chaves:
        try:
            chave = winreg.OpenKey(raiz, caminho)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(chave)[0]):
            try:
                sub = winreg.OpenKey(chave, winreg.EnumKey(chave, i))
                nome = winreg.QueryValueEx(sub, "DisplayName")[0]
            except OSError:
                continue
            if trecho.lower() not in str(nome).lower():
                continue
            pasta = None
            for campo in ("InstallLocation", "DisplayIcon", "UninstallString"):
                try:
                    v = str(winreg.QueryValueEx(sub, campo)[0]).strip().strip('"')
                except OSError:
                    continue
                v = v.split(",")[0].strip('"')
                p = Path(v)
                p = p if p.is_dir() else p.parent
                if p.is_dir():
                    pasta = p
                    break
            if pasta:
                achados.append((str(nome), pasta))
    return achados


def candidatos_padrao(nomes: list) -> list:
    bases = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    saida = []
    for b in filter(None, bases):
        for n in nomes:
            p = Path(b) / n
            if p.is_dir():
                saida.append((n, p))
    return saida


def achar_pasta_i18n(instalacao: Path):
    """Procura a pasta de idiomas (resources/i18n ou similar) até 4 níveis."""
    preferidas = [instalacao / "resources" / "i18n", instalacao / "resources" / "localization",
                  instalacao / "i18n", instalacao / "localization"]
    for p in preferidas:
        if p.is_dir():
            return p
    for raiz, dirs, _ in os.walk(instalacao):
        if len(Path(raiz).relative_to(instalacao).parts) > 4:
            dirs[:] = []
            continue
        for d in dirs:
            if d.lower() in ("i18n", "localization"):
                return Path(raiz) / d
    return None


def listar_mos(pasta_i18n: Path) -> dict:
    """Retorna {codigo_idioma: caminho_do_mo} para os idiomas existentes."""
    idiomas = {}
    for sub in sorted(pasta_i18n.iterdir()):
        if not sub.is_dir():
            continue
        mos = list(sub.glob("*.mo")) + list((sub / "LC_MESSAGES").glob("*.mo"))
        if mos:
            idiomas[sub.name] = mos[0]
    return idiomas


def escolher_pasta_manual(titulo: str):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askdirectory(title=titulo)
        root.destroy()
        return Path(p) if p else None
    except Exception:
        p = input(f"{titulo}\nCole o caminho da pasta: ").strip().strip('"')
        return Path(p) if p else None


def localizar_flash(pasta_arg) -> Path:
    if pasta_arg:
        p = Path(pasta_arg)
        if not p.is_dir():
            log(f"Pasta informada não existe: {p}")
            sys.exit(1)
        return p
    achados = buscar_registro("flash") + candidatos_padrao(
        ["Flash Studio", "Flashforge\\Flash Studio", "Flashforge Studio", "FlashStudio"])
    achados = [(n, p) for n, p in achados
               if "flashprint" not in n.lower() and any(p.glob("*.exe"))]
    vistos, unicos = set(), []
    for n, p in achados:
        if str(p).lower() not in vistos:
            vistos.add(str(p).lower())
            unicos.append((n, p))
    if len(unicos) == 1:
        log(f"Flash Studio encontrado: {unicos[0][1]}")
        return unicos[0][1]
    if len(unicos) > 1:
        print("\nEncontrei mais de uma instalação:")
        for i, (n, p) in enumerate(unicos, 1):
            print(f"  {i}. {n} -> {p}")
        while True:
            r = input("Número da instalação correta: ").strip()
            if r.isdigit() and 1 <= int(r) <= len(unicos):
                return unicos[int(r) - 1][1]
    log("Não encontrei o Flash Studio automaticamente.")
    p = escolher_pasta_manual("Selecione a pasta de instalação do Flash Studio")
    if not p or not p.is_dir():
        log("Nenhuma pasta selecionada.")
        sys.exit(1)
    return p


def localizar_bambu_local():
    for _, p in buscar_registro("bambu studio") + candidatos_padrao(["Bambu Studio", "BambuStudio"]):
        i18n = achar_pasta_i18n(p)
        if i18n:
            pt = listar_mos(i18n).get(IDIOMA)
            if pt:
                return pt
    return None


# ----------------------------------------------------------------- validação

RE_BOOST = re.compile(r"%(\d+)%")
RE_PRINTF = re.compile(
    r"%(?:(\d+)\$)?[-+#0']*(?:\d+|\*)?(?:\.(?:\d+|\*))?(hh|h|ll|l|L|z|j|t|I64)?([diouxXeEfFgGaAcspn])")
RE_CHAVE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|\{\d*\}")


def assinatura(texto: str):
    """Extrai os placeholders de uma frase para comparar origem e tradução."""
    t = texto.replace("%%", "")
    boost = Counter(RE_BOOST.findall(t))
    t = RE_BOOST.sub("", t)
    posicionais, sequenciais = Counter(), []
    for pos, tam, conv in RE_PRINTF.findall(t):
        chave = (tam or "") + conv
        if pos:
            posicionais[(pos, chave)] += 1
        else:
            sequenciais.append(chave)
    t2 = RE_PRINTF.sub("", t)
    sobra_percent = t2.count("%")  # '%' solto pode quebrar a formatação
    chaves = Counter(RE_CHAVE.findall(texto))
    return boost, posicionais, sequenciais, chaves, sobra_percent


def traducao_segura(origem: str, traducao: str) -> bool:
    if not traducao or not traducao.strip():
        return False
    a, b = assinatura(origem), assinatura(traducao)
    if a[:4] != b[:4]:
        return False
    if b[4] > a[4]:  # tradução introduziu '%' solto que não existia
        return False
    return True


# ----------------------------------------------------------------- catálogos

def carregar_catalogo(caminho: Path, nome: str) -> dict:
    """Retorna {(contexto, msgid, msgid_plural): msgstr ou {idx: msgstr}} apenas com entradas seguras."""
    try:
        cat = polib.mofile(str(caminho)) if caminho.suffix.lower() == ".mo" else polib.pofile(str(caminho))
    except Exception as e:
        log(f"  [{nome}] erro ao ler: {e}")
        return {}
    ok, rejeitadas = {}, 0
    for e in cat:
        if not e.msgid or e.obsolete or "fuzzy" in e.flags:
            continue
        chave = (e.msgctxt or "", e.msgid, e.msgid_plural or "")
        if e.msgid_plural:
            formas = {int(k): v for k, v in e.msgstr_plural.items()}
            if len(formas) < 2 or not all(v.strip() for v in formas.values()):
                continue
            seguro = traducao_segura(e.msgid, formas[0]) or traducao_segura(e.msgid_plural, formas[0])
            seguro = seguro and all(traducao_segura(e.msgid_plural, formas[i]) for i in formas if i > 0)
            if seguro:
                ok[chave] = {0: formas[0], 1: formas[max(formas)]}
            else:
                rejeitadas += 1
        else:
            if not e.msgstr:
                continue
            if traducao_segura(e.msgid, e.msgstr):
                ok[chave] = e.msgstr
            else:
                rejeitadas += 1
    log(f"  [{nome}] {len(ok)} frases válidas, {rejeitadas} descartadas por segurança")
    return ok


def baixar(nome: str, url: str, estado: dict, minimo: int = 10000):
    destino = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", nome).strip("_") + ".po")
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"TradutorFlashStudio/{VERSAO}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dados = r.read()
        if len(dados) < minimo or b"msgid" not in dados:
            raise ValueError("arquivo baixado parece inválido")
        tmp = destino.with_suffix(".tmp")
        tmp.write_bytes(dados)
        os.replace(tmp, destino)
        novo_hash = hashlib.sha256(dados).hexdigest()
        antigo = estado["fontes"].get(nome)
        status = "sem mudanças" if antigo == novo_hash else ("NOVA versão" if antigo else "baixado")
        estado["fontes"][nome] = novo_hash
        log(f"  [{nome}] {status}")
        return destino
    except Exception as e:
        if destino.exists():
            log(f"  [{nome}] sem internet ou falha ({e}). Usando cópia em cache.")
            return destino
        log(f"  [{nome}] indisponível ({e}).")
        return None


def numeros(versao: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(versao))[:3])


def checar_versao() -> None:
    """Confere no repositório se este .exe ainda serve. Sem internet, segue em frente."""
    try:
        req = urllib.request.Request(URL_VERSAO, headers={"User-Agent": f"TradutorFlashStudio/{VERSAO}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            info = json.loads(r.read().decode("utf-8"))
        atual, minima = numeros(info.get("atual", VERSAO)), numeros(info.get("minima", "0"))
    except Exception:
        return
    if numeros(VERSAO) < minima:
        log(f"Esta versão do tradutor (v{VERSAO}) não serve mais para o Flash Studio atual.")
        log(f"Baixe a versão nova em: {PAGINA_DOWNLOAD}")
        log("Nada foi alterado no seu computador.")
        sys.exit(1)
    if numeros(VERSAO) < atual:
        log(f"Há uma versão nova do tradutor (v{info.get('atual')}) em {PAGINA_DOWNLOAD}")
        log("Esta continua funcionando; seguindo.\n")


RE_SO_LINK = re.compile(r"^\s*https?://\S+\s*$")


def chaves_do_flash(arquivos: list) -> set:
    """Todas as frases do Flash Studio, lidas dos .mo/.po dos idiomas que vêm com ele."""
    chaves = set()
    for arq in arquivos:
        try:
            cat = polib.pofile(str(arq)) if Path(arq).suffix.lower() == ".po" else polib.mofile(str(arq))
            for e in cat:
                # links (ex.: páginas da wiki) não se traduzem: ficam fora da contagem
                if e.msgid and not e.obsolete and not RE_SO_LINK.match(e.msgid):
                    chaves.add((e.msgctxt or "", e.msgid, e.msgid_plural or ""))
        except Exception:
            pass
    return chaves


def referencia_es(i18n: Path) -> dict:
    """Tradução espanhola oficial, usada só como pista de sentido no arquivo de pendentes."""
    ref = {}
    for po in list((i18n / "es").glob("*.po")) if (i18n / "es").is_dir() else []:
        try:
            for e in polib.pofile(str(po)):
                if e.msgid and not e.obsolete:
                    v = e.msgstr_plural.get(0, "") if e.msgid_plural else e.msgstr
                    ref[(e.msgctxt or "", e.msgid, e.msgid_plural or "")] = v
        except Exception:
            pass
    return ref


def exportar_pendentes(chaves_flash: set, final: dict, ref_es: dict, destino: Path) -> int:
    """Gera um .po com as frases do Flash Studio que ainda estão sem tradução."""
    faltam = sorted(k for k in chaves_flash if k not in final)
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=UTF-8", "Plural-Forms": PLURAL_PT_BR}
    for ctx, msgid, plural in faltam:
        es = ref_es.get((ctx, msgid, plural))
        e = polib.POEntry(msgctxt=ctx or None, msgid=msgid, comment=f"es: {es}" if es else "")
        if plural:
            e.msgid_plural = plural
            e.msgstr_plural = {0: "", 1: ""}
        po.append(e)
    destino.unlink(missing_ok=True)
    if faltam:
        po.save(str(destino))
    return len(faltam)


MARCA = "Flash Studio"
# "OrcaSlicer", "Orca Slicer", "Orca-Flashforge" e "Orca" sozinho; preserva OrcaCloud, Cubo Orca,
# modelos de teste ("Orca String Hell"...), extensões (.orca_printer) e links
RE_MARCA = re.compile(r"(?<![\w./-])(?:Orca[ -]?Slicer|Orca-Flashforge|Orca)"
                      r"(?!\w|[./-]\w| Cloud| Cube| Tolerance| String)")
RE_NAO_TROCAR_MARCA = re.compile(r"based on|desktop file", re.I)  # créditos e nomes de arquivo


def aplicar_marca(msgid: str, v):
    """Troca o nome OrcaSlicer pelo nome do programa nas traduções vindas de fora."""
    if RE_NAO_TROCAR_MARCA.search(msgid):
        return v
    if isinstance(v, dict):
        return {i: RE_MARCA.sub(MARCA, s) for i, s in v.items()}
    return RE_MARCA.sub(MARCA, v)


def montar_mo(fontes: list, personalizado, destino_tmp: Path, chaves_flash: set) -> dict:
    """fontes: lista de dicts já validados, em ordem de prioridade."""
    final = {}
    for cat in fontes:
        for k, v in cat.items():
            final.setdefault(k, v)
    final = {k: aplicar_marca(k[1], v) for k, v in final.items()}
    if personalizado:
        final.update(personalizado)  # maior prioridade: correções do próprio usuário, sem alteração

    po = polib.POFile()
    po.metadata = {
        "Project-Id-Version": "Flash Studio pt_BR (Fiat Opus 3D)",
        "Language": IDIOMA,
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": PLURAL_PT_BR,
    }
    for (ctx, msgid, plural), v in sorted(final.items()):
        if plural:
            po.append(polib.POEntry(msgctxt=ctx or None, msgid=msgid, msgid_plural=plural,
                                    msgstr_plural={0: v[0], 1: v[1]}))
        else:
            po.append(polib.POEntry(msgctxt=ctx or None, msgid=msgid, msgstr=v))
    po.save_as_mofile(str(destino_tmp))

    # Teste: o arquivo gerado precisa abrir no gettext padrão
    with open(destino_tmp, "rb") as f:
        t = gettext.GNUTranslations(f)
    amostra = next(iter(k for k in final if not k[0] and not k[2]), None)
    if amostra and t.gettext(amostra[1]) != final[amostra]:
        raise RuntimeError("Arquivo gerado falhou no teste de leitura.")

    cobertura = None
    if chaves_flash:
        traduzidas = sum(1 for k in chaves_flash if k in final)
        cobertura = (traduzidas, len(chaves_flash))
    return {"total": len(final), "cobertura": cobertura, "final": final}


# --------------------------------------------------------- backup / instalação

def instalar_arquivo(novo: Path, alvo: Path, estado: dict) -> None:
    chave = str(alvo)
    reg = estado["arquivos"].get(chave)
    if alvo.exists():
        atual = sha256(alvo)
        if not reg or atual != reg.get("hash_instalado"):
            # Arquivo original (ou atualizado pela Flashforge): guardar backup
            pasta_bkp = BACKUP / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            pasta_bkp.mkdir(parents=True, exist_ok=True)
            copia = pasta_bkp / f"{alvo.parent.name}__{alvo.name}"
            shutil.copy2(alvo, copia)
            reg = {"original": str(copia), "existia": True}
            log(f"  Backup do original: {copia}")
    elif not reg:
        reg = {"original": None, "existia": False}
    alvo.parent.mkdir(parents=True, exist_ok=True)
    tmp = alvo.with_suffix(".mo.novo")
    shutil.copy2(novo, tmp)
    os.replace(tmp, alvo)
    reg["hash_instalado"] = sha256(alvo)
    estado["arquivos"][chave] = reg
    salvar_estado(estado)
    log(f"  Instalado: {alvo}")


def arquivos_config() -> list:
    raiz = os.environ.get("APPDATA")
    if not raiz or not Path(raiz).is_dir():
        return []
    saida = []
    for d in Path(raiz).iterdir():
        n = d.name.lower()
        if d.is_dir() and "flash" in n and "flashprint" not in n:
            saida += list(d.glob("*.conf"))
    return saida


RE_LANG = re.compile(r'("language"\s*:\s*")([^"\\]*)(")')
RE_MD5 = re.compile(r"\n(# MD5 checksum )([0-9a-fA-F]{32})[ \t]*\r?\n?$")


def editar_idioma_conf(conf: Path, novo: str):
    """Troca só o valor de "language", preservando o resto do arquivo byte a byte.
    Se houver linha de checksum MD5, só altera quando consegue reproduzir o cálculo
    do próprio programa (senão o programa acharia o arquivo corrompido).
    Retorna (valor_anterior, None) ou (None, motivo_da_recusa)."""
    bruto = conf.read_bytes()
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        return None, "arquivo não está em UTF-8"
    achados = RE_LANG.findall(texto)
    if len(achados) != 1:
        return None, "chave de idioma ausente ou repetida"
    anterior = achados[0][1]
    novo_texto = RE_LANG.sub(lambda m: m.group(1) + novo + m.group(3), texto, count=1)

    m = RE_MD5.search(texto)
    if m:
        def md5(t: str) -> str:
            return hashlib.md5(t.encode("utf-8")).hexdigest()
        variantes = [
            lambda t, c: t[:c],
            lambda t, c: t[:c + 1],
            # Flash Studio/Orca no Windows: MD5 calculado com \n, arquivo gravado com \r\n
            lambda t, c: t[:c].rstrip("\r").replace("\r\n", "\n"),
        ]
        metodo = next((v for v in variantes if md5(v(texto, m.start())) == m.group(2).lower()), None)
        if metodo is None:
            return None, "checksum do arquivo em formato desconhecido"
        m2 = RE_MD5.search(novo_texto)
        novo_md5 = md5(metodo(novo_texto, m2.start()))
        if m.group(2).isupper():
            novo_md5 = novo_md5.upper()
        novo_texto = novo_texto[:m2.start()] + "\n" + m.group(1) + novo_md5 + texto[m.end(2):]
        # conferência: o novo arquivo precisa passar no mesmo teste
        m3 = RE_MD5.search(novo_texto)
        if md5(metodo(novo_texto, m3.start())) != m3.group(2).lower():
            return None, "falha na conferência do checksum"

    tmp = conf.with_suffix(conf.suffix + ".novo")
    tmp.write_bytes(novo_texto.encode("utf-8"))
    os.replace(tmp, conf)
    return anterior, None


def ajustar_config(estado: dict, auto: bool, idioma: str = IDIOMA) -> None:
    confs = arquivos_config()
    if not confs:
        log("  Configuração não encontrada (normal se o Flash Studio nunca foi aberto).")
        log("  Abra o programa uma vez, feche e rode de novo, ou escolha o idioma no menu Preferências.")
        return
    for conf in confs:
        try:
            atual = RE_LANG.findall(conf.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            log(f"  {conf.name}: não consegui ler ({e}).")
            continue
        if len(atual) == 1 and atual[0][1] == idioma:
            log(f"  {conf.name}: já está em {idioma}.")
            continue
        if not perguntar(f"  Definir o idioma do Flash Studio como {idioma} em {conf.name}?", True, auto):
            continue
        BACKUP.mkdir(parents=True, exist_ok=True)
        bkp = BACKUP / f"{conf.name}.{datetime.datetime.now():%Y%m%d_%H%M%S}.bak"
        shutil.copy2(conf, bkp)
        anterior, erro = editar_idioma_conf(conf, idioma)
        if erro:
            log(f"  {conf.name}: não alterei ({erro}). Escolha o idioma no menu Preferências.")
            continue
        # guarda o idioma de antes da PRIMEIRA instalação, para o Restaurar voltar a ele
        cfg_antigo = estado.get("config")
        if cfg_antigo and cfg_antigo.get("arquivo") == str(conf):
            anterior = cfg_antigo["anterior"]
        estado["config"] = {"arquivo": str(conf), "anterior": anterior, "backup": str(bkp)}
        salvar_estado(estado)
        log(f"  Idioma definido como '{idioma}'. Backup: {bkp}")


# ------------------------------------------------------------------- fluxos

def checar_programa_fechado(instalacao: Path, auto: bool) -> None:
    exes = [p.name for p in instalacao.glob("*.exe") if "unins" not in p.name.lower()]
    while processos_rodando(exes):
        log("O Flash Studio está aberto. Feche o programa para continuar.")
        if auto:
            sys.exit(1)
        input("Depois de fechar, aperte Enter...")


def garantir_permissao(pasta: Path) -> None:
    if pasta_gravavel(pasta):
        return
    if IS_WINDOWS and not eh_admin():
        log("Preciso de permissão de administrador para gravar na pasta do programa.")
        reabrir_como_admin()
    log(f"Sem permissão de escrita em {pasta}.")
    sys.exit(1)


def fluxo_instalar(instalacao: Path, auto: bool, slot, gerar_pendentes: bool = False) -> None:
    checar_versao()
    i18n = achar_pasta_i18n(instalacao)
    if not i18n:
        recursos = instalacao / "resources"
        if not recursos.is_dir():
            log("Pasta de idiomas não encontrada e a estrutura da instalação é desconhecida.")
            sys.exit(1)
        i18n = recursos / "i18n"
        log(f"Pasta de idiomas não existe. Vou criar: {i18n}")
        garantir_permissao(recursos)
        if not perguntar("Continuar?", True, auto):
            sys.exit(0)
        i18n.mkdir(exist_ok=True)
    log(f"Pasta de idiomas: {i18n}")
    garantir_permissao(i18n)
    checar_programa_fechado(instalacao, auto)

    estado = carregar_estado()
    idiomas = listar_mos(i18n)
    nossos = {r.get("hash_instalado") for r in estado["arquivos"].values()}
    originais = [mo for mo in idiomas.values() if sha256(mo) not in nossos]
    originais += [Path(r["original"]) for r in estado["arquivos"].values()
                  if r.get("original") and Path(r["original"]).exists()]
    if idiomas:
        dominio = next(iter(idiomas.values())).name
    else:
        sugestao = next((p.stem for p in instalacao.glob("*.exe") if "unins" not in p.name.lower()), "FlashStudio")
        log("Não há nenhum arquivo de idioma na pasta para eu copiar o nome.")
        dominio = (input(f"Nome do arquivo de tradução [{sugestao}.mo]: ").strip() if not auto else "") \
            or f"{sugestao}.mo"
        if not dominio.endswith(".mo"):
            dominio += ".mo"
    log(f"Idiomas existentes: {', '.join(idiomas) or 'nenhum'} | arquivo: {dominio}")

    log("\nBuscando traduções...")
    fontes = []
    for nome, url in FONTES_REMOTAS:
        arq = baixar(nome, url, estado)
        if arq:
            fontes.append((nome, arq))
    bambu_local = localizar_bambu_local()
    if bambu_local:
        log(f"  [Bambu Studio local] encontrado em {bambu_local}")
        fontes.append(("Bambu Studio (instalado)", bambu_local))
    salvar_estado(estado)
    if not fontes:
        log("Nenhuma fonte de tradução disponível. Verifique a internet.")
        sys.exit(1)

    log("\nValidando frases...")
    # só idiomas ativos (que têm .mo) e só o .po do próprio programa: ignora sobras de versões antigas
    pos_flash = [po for codigo, mo in idiomas.items() if codigo != IDIOMA
                 for po in mo.parent.glob(f"{Path(dominio).stem}*.po")]
    chaves_flash = chaves_do_flash(originais + pos_flash)
    catalogos = []
    for nome, arq in fontes:
        cat = carregar_catalogo(arq, nome)
        if cat:
            cob = sum(1 for k in chaves_flash if k in cat) if chaves_flash else len(cat)
            catalogos.append((cob, nome, cat))
    catalogos.sort(key=lambda x: x[0], reverse=True)  # a fonte que mais combina vem primeiro
    # Pedido do Pedro (02/11/2026): mesmas palavras do Bambu Studio que ele usa na outra impressora → Bambu antes do
    # Orca (instalado antes do GitHub: é o que aparece na tela dele). O Orca fica pras frases que o Bambu não tem.
    catalogos.sort(key=lambda x: (0 if "instalado" in x[1] else 1) if "Bambu" in x[1] else 2)
    # Filtros de arquivo ("…|*.crt;*.pem|…"): o Bambu pt_BR tem alguns quebrados ("* . *") → nesses vale a próxima fonte.
    catalogos = [(c, n, {k: v for k, v in cat.items() if "|*" not in k[1]} if "Bambu" in n else cat)
                 for c, n, cat in catalogos]

    # Traduções próprias da Flashforge: vêm antes das fontes de fora. Duas cópias, a baixada (sempre a mais nova
    # publicada) e a embutida (funciona sem internet). No .exe a baixada manda; rodando o .py (máquina de quem
    # traduz), o arquivo local manda — ele tem as frases recém-traduzidas que ainda não foram publicadas.
    proprias = []
    arq = baixar(*FONTE_FLASHFORGE, estado, minimo=1000)
    if arq:
        proprias.append(("Flashforge (baixadas)", arq))
    embutido = PASTA_RECURSOS / ARQ_EMBUTIDO
    if embutido.exists():
        proprias.insert(0 if not getattr(sys, "frozen", False) else len(proprias), ("Flashforge (embutidas)", embutido))
    salvar_estado(estado)
    for nome, arq in reversed(proprias):
        cat = carregar_catalogo(arq, nome)
        if cat:
            catalogos.insert(0, (0, nome, cat))
    log("Ordem de prioridade: " + " > ".join(n for _, n, _ in catalogos))

    personalizado = None
    arq_pers = PASTA_EXE / "personalizado.po"
    if arq_pers.exists():
        personalizado = carregar_catalogo(arq_pers, "personalizado.po")

    tmp_mo = DADOS / "gerado.mo"
    info = montar_mo([c for _, _, c in catalogos], personalizado, tmp_mo, chaves_flash)
    log(f"\nArquivo gerado com {info['total']} frases.")
    if info["cobertura"]:
        t, n = info["cobertura"]
        log(f"Cobertura no Flash Studio: {t}/{n} ({100 * t / n:.1f}%).")
    if gerar_pendentes:  # só pra quem mantém a tradução (--pendentes)
        pendentes = PASTA_EXE / "pendentes.po"
        if not pasta_gravavel(PASTA_EXE):
            pendentes = DADOS / "pendentes.po"
        qtd = exportar_pendentes(chaves_flash, info["final"], referencia_es(i18n), pendentes)
        if qtd:
            log(f"{qtd} frases ainda em inglês. Lista em: {pendentes}")
    elif info["cobertura"] and info["cobertura"][0] < info["cobertura"][1]:
        t, n = info["cobertura"]
        log(f"{n - t} frases desta versão do Flash Studio ainda aparecem em inglês (nada quebra por isso).")
        log("  A tradução delas sai numa próxima atualização: é só rodar este programa de novo daqui a uns dias.")

    # Destino 1: pasta pt_BR (funciona se o Flash Studio listar português)
    alvo_pt = i18n / IDIOMA / dominio
    instalar_arquivo(tmp_mo, alvo_pt, estado)

    # Destino 2 (opcional): ocupar o lugar de um idioma que você não usa.
    # Se isso já foi feito numa execução anterior, repete no mesmo idioma sem perguntar.
    if slot is None:
        slot = next((Path(a).parent.name for a in estado["arquivos"]
                     if Path(a).parent.parent == i18n and Path(a).parent.name != IDIOMA), None)
        if slot:
            log(f"Atualizando também o idioma '{slot}', substituído numa instalação anterior.")
    if slot is None and not auto and idiomas:
        outros = [c for c in idiomas if c != IDIOMA]
        if outros and perguntar("\nSe português não aparecer no menu, posso colocar a tradução no lugar "
                                "de outro idioma. Quer fazer isso agora?", False):
            for i, c in enumerate(outros, 1):
                print(f"  {i}. {c}")
            r = input("Número do idioma a substituir (Enter cancela): ").strip()
            if r.isdigit() and 1 <= int(r) <= len(outros):
                slot = outros[int(r) - 1]
    if slot:
        if slot not in idiomas:
            log(f"Idioma '{slot}' não existe na pasta. Ignorado.")
        else:
            instalar_arquivo(tmp_mo, idiomas[slot], estado)
            log(f"No Flash Studio, escolha o idioma '{slot}' para ver em português.")

    log("\nConfiguração de idioma:")
    ajustar_config(estado, auto, slot if slot in idiomas else IDIOMA)
    tmp_mo.unlink(missing_ok=True)
    log("\nConcluído. Abra o Flash Studio. Se algo der errado, rode a opção Restaurar.")


def fluxo_restaurar(instalacao, auto: bool) -> None:
    estado = carregar_estado()
    if not estado["arquivos"] and not estado.get("config"):
        log("Nada para restaurar.")
        return
    if instalacao:
        checar_programa_fechado(instalacao, auto)
    for alvo_s, reg in list(estado["arquivos"].items()):
        alvo = Path(alvo_s)
        garantir_permissao(alvo.parent if alvo.parent.exists() else alvo.parent.parent)
        try:
            if reg.get("existia") and reg.get("original") and Path(reg["original"]).exists():
                shutil.copy2(reg["original"], alvo)
                log(f"  Restaurado: {alvo}")
            elif not reg.get("existia") and alvo.exists():
                alvo.unlink()
                log(f"  Removido: {alvo}")
                try:
                    alvo.parent.rmdir()  # só remove se ficou vazia
                except OSError:
                    pass
            del estado["arquivos"][alvo_s]
        except OSError as e:
            log(f"  Falha em {alvo}: {e}")
    cfg = estado.get("config")
    if cfg and Path(cfg["arquivo"]).exists():
        anterior, erro = editar_idioma_conf(Path(cfg["arquivo"]), cfg["anterior"])
        if erro:
            log(f"  Não consegui restaurar o idioma ({erro}). Backup do arquivo em: {cfg.get('backup')}")
        else:
            estado["config"] = None
            log(f"  Idioma do Flash Studio voltou para '{cfg['anterior']}'.")
    salvar_estado(estado)
    log("Restauração concluída.")


def main() -> None:
    global ACAO_ATUAL
    preparar_console()
    ap = argparse.ArgumentParser(description="Tradutor PT-BR para Flash Studio")
    ap.add_argument("--instalar", action="store_true", help="instala/atualiza sem perguntas")
    ap.add_argument("--restaurar", action="store_true", help="desfaz todas as alterações")
    ap.add_argument("--pasta", help="pasta de instalação do Flash Studio")
    ap.add_argument("--substituir", help="código do idioma a substituir (ex.: es)")
    ap.add_argument("--pendentes", action="store_true", help="gera pendentes.po com as frases sem tradução")
    ap.add_argument("--acao", choices=["instalar", "restaurar"], help=argparse.SUPPRESS)
    args = ap.parse_args()
    automatico = args.instalar or args.restaurar

    print(f"Tradutor PT-BR para Flash Studio v{VERSAO} | Fiat Opus 3D")
    print(f"Dados e backups em: {DADOS}\n")

    try:
        if args.restaurar or args.acao == "restaurar":
            ACAO_ATUAL = "restaurar"
            fluxo_restaurar(Path(args.pasta) if args.pasta else None, auto=bool(args.restaurar))
        elif args.instalar or args.acao == "instalar":
            ACAO_ATUAL = "instalar"
            fluxo_instalar(localizar_flash(args.pasta), auto=bool(args.instalar), slot=args.substituir,
                           gerar_pendentes=args.pendentes)
        else:
            print("1. Instalar / atualizar tradução")
            print("2. Restaurar original")
            print("3. Sair")
            op = input("\nEscolha: ").strip()
            if op == "1":
                ACAO_ATUAL = "instalar"
                fluxo_instalar(localizar_flash(args.pasta), auto=False, slot=args.substituir,
                               gerar_pendentes=args.pendentes)
            elif op == "2":
                ACAO_ATUAL = "restaurar"
                fluxo_restaurar(None, auto=False)
    except KeyboardInterrupt:
        log("\nCancelado.")
    except SystemExit as e:
        if RELANCADO or automatico:
            raise
        if e.code not in (0, None):
            log("Operação interrompida.")
    except Exception as e:
        import traceback
        log(f"\nErro inesperado: {e!r}")
        try:
            with open(LOG_ARQ, "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except OSError:
            pass
        log(f"Detalhes em {LOG_ARQ}. Os arquivos são gravados de forma atômica, nada fica pela metade.")
    if not automatico:
        input("\nEnter para sair...")


if __name__ == "__main__":
    main()
