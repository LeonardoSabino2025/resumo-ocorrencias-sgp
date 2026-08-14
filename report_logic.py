"""
Lógica de leitura da planilha SGP e geração do resumo de ocorrências
para envio no WhatsApp, com sugestão de rota por técnico.

Não usa nenhuma LLM: toda a extração, contagem e formatação é feita
de forma determinística a partir dos dados da planilha.
"""
from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

# --------------------------------------------------------------------------
# Normalização de texto
# --------------------------------------------------------------------------

def _strip_accents(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))


def _norm_key(txt: str) -> str:
    """Chave normalizada para comparação (maiúsculas, sem acento, sem espaços duplicados/tab)."""
    if txt is None:
        return ""
    txt = str(txt).replace("\t", " ").replace("_x000D_", " ")
    txt = " ".join(txt.split())
    return _strip_accents(txt).upper().strip()


def first_name(full_name: str) -> str:
    if not full_name:
        return ""
    full_name = " ".join(str(full_name).split())
    return full_name.split(" ")[0].title()


def title_case(txt: str) -> str:
    if not txt:
        return ""
    return " ".join(str(txt).split()).title()


# --------------------------------------------------------------------------
# Tipos de ocorrência válidos (ordem oficial do relatório)
# --------------------------------------------------------------------------

GENERAL_TYPE_ORDER = [
    "Suporte - Redes",
    "Suporte - Conexão Lenta",
    "Suporte - Adequação de instalação",
    "Suporte - Sem Conexão",
    "Recolhimento de equipamentos",
    "Mudança de endereço",
    "Reativação",
    "Instalações",
    "78 Recolhimento de equipamentos - Urgente",
    "Instalação de TV",
    "Mudança de local de equipamentos",
    "R. de equipamentos sem sucesso",
]

TECH_CORE_TYPE_ORDER = [
    "Suporte - Sem Conexão",
    "Suporte - Conexão Lenta",
    "Mudança de endereço",
    "Recolhimento de equipamentos",
    "Reativação",
    "78 Recolhimento de equipamentos - Urgente",
    "Instalação de TV",
    "Instalações",
]

_TYPE_KEY_TO_CANONICAL = {_norm_key(t): t for t in GENERAL_TYPE_ORDER}


def canonical_type(raw_type: str) -> str:
    """Mapeia o texto bruto da coluna Tipo para o nome canônico conhecido.
    Se não houver correspondência, retorna o texto original normalizado
    (mantém título original, apenas limpando tabs/espaços)."""
    key = _norm_key(raw_type)
    if key in _TYPE_KEY_TO_CANONICAL:
        return _TYPE_KEY_TO_CANONICAL[key]
    cleaned = " ".join(str(raw_type or "").replace("\t", " ").split())
    return cleaned


# --------------------------------------------------------------------------
# Agrupamento para exibição no relatório:
# - "Recolhimento de equipamentos" e "78 Recolhimento de equipamentos -
#   Urgente" são somados e exibidos juntos como "Recolhimentos".
# - Tipos que começam com "Suporte - " exibem só o restante do nome
#   (ex.: "Suporte - Redes" -> "Redes").
# --------------------------------------------------------------------------

_RECOLHIMENTO_TYPES = {"Recolhimento de equipamentos", "78 Recolhimento de equipamentos - Urgente"}


def display_label(tipo_canonical: str) -> str:
    if tipo_canonical in _RECOLHIMENTO_TYPES:
        return "Recolhimentos"
    if tipo_canonical.startswith("Suporte - "):
        return tipo_canonical[len("Suporte - "):]
    if tipo_canonical.startswith("Suporte "):
        return tipo_canonical[len("Suporte "):]
    return tipo_canonical


GENERAL_GROUP_ORDER = []
for _t in GENERAL_TYPE_ORDER:
    _label = display_label(_t)
    if _label not in GENERAL_GROUP_ORDER:
        GENERAL_GROUP_ORDER.append(_label)

TECH_CORE_GROUP_ORDER = []
for _t in TECH_CORE_TYPE_ORDER:
    _label = display_label(_t)
    if _label not in TECH_CORE_GROUP_ORDER:
        TECH_CORE_GROUP_ORDER.append(_label)


# --------------------------------------------------------------------------
# Clusters geográficos (usados apenas para ordenar a rota sugerida —
# o nome do cluster nunca é exibido no relatório)
# --------------------------------------------------------------------------

_CLUSTER_DEFS = {
    "BAIXO_VALE": [
        "Sitio", "Boa Vista", "Capoeira", "Rio dos Indios", "Barro Vermelho",
        "Capim", "Coqueiros", "Serrinha", "Imbiribeira", "Caiana de Baixo",
        "Alto do Sitio", "Central Parque Clube", "Zona Rural", "Santa Helena",
        "Inferninho",
    ],
    "CAIANA": ["Caiana de Cima", "Caiana", "Central Park Club", "Caina"],
    "CONTENDAS": ["Contendas", "Contenda"],
    "EXTREMOZ_URBANO": [
        "Alto de Extremoz", "Caminho do Mar", "Santos Dumont", "Moinho dos Ventos",
        "Bosque das Flores", "Centro", "Vila Nova", "Sport Clube", "Sport Clube 1",
        "Sport Clube 2", "Sport Clube 3", "Sport Clube 4", "Malvinas",
        "Costa das Dunas", "Estivas", "Parque das Flores", "Jardim de Extremoz",
        "Santa Célia", "Village dos Coqueiros", "Quinta das Figueiras", "Iraque",
        "Renascer", "Jardim Botânico", "Manaaim", "Parque do Servidor Extremoz",
        "São Miguel Arcanjo", "Loteamento Potiguar", "Central Park 2",
    ],
    "LITORAL": ["Pitangui", "Graçandu", "Barra do Rio", "Coqueirinho", "Matinha", "Aldeia", "Alto das Dunas"],
    "PEDRINHAS": ["Pedrinhas"],
    "JACUMA": ["Jacumã", "Praia de Jacumã"],
    "MAXARANGUAPE": ["Maxaranguape", "Barra de Maxaranguape"],
}

# Ordem heurística de deslocamento partindo da Praia de Pitangui (Litoral).
CLUSTER_ROUTE_ORDER = [
    "LITORAL", "PEDRINHAS", "BAIXO_VALE", "CAIANA", "EXTREMOZ_URBANO",
    "CONTENDAS", "MAXARANGUAPE", "JACUMA", "OUTROS",
]

_BAIRRO_KEY_TO_CLUSTER: dict[str, str] = {}
for _cluster, _bairros in _CLUSTER_DEFS.items():
    for _b in _bairros:
        _BAIRRO_KEY_TO_CLUSTER[_norm_key(_b)] = _cluster


def bairro_to_cluster(bairro: str) -> str:
    key = _norm_key(bairro)
    if not key:
        return "OUTROS"
    if key in _BAIRRO_KEY_TO_CLUSTER:
        return _BAIRRO_KEY_TO_CLUSTER[key]
    # correspondência aproximada: bairro conhecido contido no texto (ou vice-versa)
    for known_key, cluster in _BAIRRO_KEY_TO_CLUSTER.items():
        if known_key in key or key in known_key:
            return cluster
    # fuzzy match como último recurso
    import difflib
    match = difflib.get_close_matches(key, _BAIRRO_KEY_TO_CLUSTER.keys(), n=1, cutoff=0.72)
    if match:
        return _BAIRRO_KEY_TO_CLUSTER[match[0]]
    return "OUTROS"


def cluster_sort_key(bairro: str) -> int:
    cluster = bairro_to_cluster(bairro)
    try:
        return CLUSTER_ROUTE_ORDER.index(cluster)
    except ValueError:
        return len(CLUSTER_ROUTE_ORDER)


# --------------------------------------------------------------------------
# Leitura da planilha
# --------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "Protocolo", "Cliente", "Tipo", "Status", "Criada", "Agendamento",
    "Encerrada", "Responsável", "Bairro",
]


class PlanilhaError(Exception):
    pass


def load_occurrences(file_bytes: bytes) -> pd.DataFrame:
    """Lê o Excel exportado do SGP (aba 'Relatório de Ocorrências') e devolve
    um DataFrame já com cabeçalho correto e datas convertidas."""
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=object)

    header_row_idx = None
    for i in range(min(15, len(raw))):
        row_values = [str(v).strip() for v in raw.iloc[i].tolist() if v is not None]
        if "Protocolo" in row_values and "Status" in row_values:
            header_row_idx = i
            break
    if header_row_idx is None:
        raise PlanilhaError(
            "Não encontrei a linha de cabeçalho (com as colunas 'Protocolo' e 'Status') "
            "na planilha. Confirme se é o export de 'Relatório de Ocorrência' do SGP."
        )

    header = [str(v).strip() if v is not None else "" for v in raw.iloc[header_row_idx].tolist()]
    df = raw.iloc[header_row_idx + 1:].copy()
    df.columns = header
    df = df.dropna(how="all")
    df = df[df["Protocolo"].notna()]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise PlanilhaError(f"Colunas ausentes na planilha: {', '.join(missing)}")

    for col in ("Criada", "Agendamento", "Encerrada"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df["Tipo"] = df["Tipo"].apply(canonical_type)
    df["Grupo"] = df["Tipo"].apply(display_label)
    df["Status"] = df["Status"].astype(str).str.strip()
    df["Responsável"] = df["Responsável"].astype(str).str.strip()
    df["Bairro"] = df["Bairro"].astype(str).str.strip()
    df["Cliente"] = df["Cliente"].astype(str).str.strip()

    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Geração do relatório
# --------------------------------------------------------------------------

@dataclass
class Stop:
    client_first_name: str
    bairro: str
    tipo: str

    def sort_key(self):
        return cluster_sort_key(self.bairro)


def _fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _general_totals_block(df: pd.DataFrame, mode: str, report_date: date) -> list[str]:
    lines = []
    is_today = lambda s: s.dt.date == report_date if hasattr(s, "dt") else False

    abertas_hoje = df[(df["Status"].str.upper() == "ABERTA") & (df["Agendamento"].dt.date == report_date)]
    encerradas_hoje = df[(df["Status"].str.upper() == "ENCERRADA") & (df["Encerrada"].dt.date == report_date)]

    for grupo in GENERAL_GROUP_ORDER:
        if mode == "inicio":
            n = int((abertas_hoje["Grupo"] == grupo).sum())
            if n == 0:
                continue
            lines.append(f"✔️ {grupo}: {n} abertas;")
        else:
            n_enc = int((encerradas_hoje["Grupo"] == grupo).sum())
            n_reagendadas = int(
                ((df["Grupo"] == grupo) & (df["Status"].str.upper() == "ABERTA") &
                 (df["Agendamento"].dt.date == report_date)).sum()
            )
            if n_enc == 0 and n_reagendadas == 0:
                continue
            if n_reagendadas > 0:
                lines.append(f"✔️ {grupo}: {n_enc} encerradas hoje | {n_reagendadas} reagendadas;")
            else:
                lines.append(f"✔️ {grupo}: {n_enc} encerradas hoje;")
    return lines


def _tech_block_inicio(tech: str, df_tech_today_open: pd.DataFrame, report_date: date) -> list[str] | None:
    if df_tech_today_open.empty:
        return None

    lines = [f"🔧 *{tech.upper()}*", "Tipo de ocorrência:"]
    counted_groups = set()
    for grupo in TECH_CORE_GROUP_ORDER:
        n = int((df_tech_today_open["Grupo"] == grupo).sum())
        counted_groups.add(grupo)
        if n == 0:
            continue
        lines.append(f"✔️ {grupo}: {n}")

    extras = sorted(set(df_tech_today_open["Grupo"]) - counted_groups)
    for grupo in extras:
        n = int((df_tech_today_open["Grupo"] == grupo).sum())
        if n == 0:
            continue
        lines.append(f"✔️ {grupo}: {n}")

    lines.append("")
    lines.append("Agendamentos por Tipo de Ocorrência:")
    for grupo in list(TECH_CORE_GROUP_ORDER) + extras:
        rows = df_tech_today_open[df_tech_today_open["Grupo"] == grupo]
        if rows.empty:
            continue
        lines.append(f"*{grupo.upper()}:*")
        for _, row in rows.sort_values(by="Bairro", key=lambda s: s.map(cluster_sort_key)).iterrows():
            lines.append(f"✔️ Cliente: {first_name(row['Cliente'])} em {title_case(row['Bairro'])};")

    stops = [
        Stop(first_name(r["Cliente"]), title_case(r["Bairro"]), r["Tipo"])
        for _, r in df_tech_today_open.iterrows()
    ]
    stops.sort(key=lambda s: s.sort_key())
    if stops:
        lines.append("")
        lines.append("🗺️ Sugestão de ordem de atendimento:")
        for idx, s in enumerate(stops, start=1):
            suffix = " (Partindo de Pitangui)" if idx == 1 else ""
            lines.append(f"{idx}. {s.client_first_name} - {s.bairro}{suffix}")

    return lines


def _grouped_client_lines(df_subset: pd.DataFrame) -> list[str]:
    lines = []
    order = list(dict.fromkeys(df_subset["Grupo"].tolist()))
    for grupo in order:
        rows = df_subset[df_subset["Grupo"] == grupo]
        lines.append(f"*{grupo.upper()}:*")
        for _, row in rows.iterrows():
            lines.append(f"✔️ Cliente: {first_name(row['Cliente'])} em {title_case(row['Bairro'])};")
    return lines


def _tech_block_final(tech: str, df_tech_closed: pd.DataFrame, df_tech_reagendada: pd.DataFrame, report_date: date) -> list[str] | None:
    if df_tech_closed.empty and df_tech_reagendada.empty:
        return None

    combined = pd.concat([df_tech_closed, df_tech_reagendada])
    lines = [f"🔧 *{tech.upper()}*", "Tipo de ocorrência:"]
    counted_groups = set()
    for grupo in TECH_CORE_GROUP_ORDER:
        n = int((combined["Grupo"] == grupo).sum())
        counted_groups.add(grupo)
        if n == 0:
            continue
        lines.append(f"✔️ {grupo}: {n}")
    extras = sorted(set(combined["Grupo"]) - counted_groups)
    for grupo in extras:
        n = int((combined["Grupo"] == grupo).sum())
        if n == 0:
            continue
        lines.append(f"✔️ {grupo}: {n}")

    lines.append("")
    lines.append("Ocorrências fechadas hoje:")
    if df_tech_closed.empty:
        lines.append("Nenhuma ocorrência encerrada hoje")
    else:
        lines.extend(_grouped_client_lines(df_tech_closed))

    if not df_tech_reagendada.empty:
        lines.append("")
        lines.append("Ocorrências reagendadas para amanhã:")
        lines.extend(_grouped_client_lines(df_tech_reagendada))

    stops = [
        Stop(first_name(r["Cliente"]), title_case(r["Bairro"]), r["Tipo"])
        for _, r in df_tech_reagendada.iterrows()
    ]
    stops.sort(key=lambda s: s.sort_key())
    if stops:
        lines.append("")
        lines.append("🗺️ Sugestão de ordem de atendimento (reagendadas):")
        for idx, s in enumerate(stops, start=1):
            lines.append(f"{idx}. {s.client_first_name} - {s.bairro}")

    return lines


OBSERVACOES = """Observações:
1. Senhores, por gentileza revisem este resumo, se alguma das informações não estiver de acordo, me notifiquem. Grato.
2. Revisem as informações, agendamento, combustível, EPI, fardamento, material, ferramentas e equipamentos os quais necessitarão ANTES de partirem para as atividades em campo - otimizem o tempo;
3. Em dias de chuva, conduzam o veículo tomando todas as precauções de direção defensiva além de consultarem o app Waze."""


def generate_report(df: pd.DataFrame, mode: str, report_date: date) -> str:
    """mode: 'inicio' ou 'final'"""
    assert mode in ("inicio", "final")

    period_label = "Início do Expediente" if mode == "inicio" else "Término do Expediente"
    header = f"Segue o resumo das ocorrências registradas no painel do SGP de responsabilidade dos técnicos hoje, {_fmt_date(report_date)} ({period_label})."

    blocks: list[str] = [header, ""]
    blocks.extend(_general_totals_block(df, mode, report_date))
    blocks.append("")

    if mode == "inicio":
        blocks.append("Resumo de ocorrências em aberto, por Técnico:")
    else:
        blocks.append("Resumo de ocorrências do dia, por Técnico:")
    blocks.append("")

    tecnicos = sorted(t for t in df["Responsável"].dropna().unique() if t and t.lower() != "nan")

    tech_blocks = []
    if mode == "inicio":
        for tech in tecnicos:
            df_tech_today_open = df[
                (df["Responsável"] == tech) &
                (df["Status"].str.upper() == "ABERTA") &
                (df["Agendamento"].dt.date == report_date)
            ]
            block = _tech_block_inicio(tech, df_tech_today_open, report_date)
            if block:
                tech_blocks.append(block)
    else:
        for tech in tecnicos:
            df_tech_closed = df[
                (df["Responsável"] == tech) &
                (df["Status"].str.upper() == "ENCERRADA") &
                (df["Encerrada"].dt.date == report_date)
            ]
            df_tech_reagendada = df[
                (df["Responsável"] == tech) &
                (df["Status"].str.upper() == "ABERTA") &
                (df["Agendamento"].dt.date == report_date)
            ]
            block = _tech_block_final(tech, df_tech_closed, df_tech_reagendada, report_date)
            if block:
                tech_blocks.append(block)

    for i, block in enumerate(tech_blocks):
        blocks.extend(block)
        blocks.append("")

    blocks.append(OBSERVACOES)

    return "\n".join(blocks)
