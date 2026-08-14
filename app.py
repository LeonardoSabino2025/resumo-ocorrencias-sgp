import streamlit as st
from datetime import date, datetime

from report_logic import load_occurrences, generate_report, PlanilhaError

st.set_page_config(
    page_title="Resumo de Ocorrências SGP",
    page_icon="🛠️",
    layout="centered",
)

st.title("🛠️ Resumo de Ocorrências por Técnico")
st.caption(
    "Gera o resumo (pronto para colar no WhatsApp) a partir do relatório de "
    "ocorrências exportado do SGP, com sugestão de rota por técnico."
)

with st.sidebar:
    st.header("1. Planilha")
    uploaded_file = st.file_uploader(
        "Relatório de Ocorrências (.xlsx) exportado do SGP",
        type=["xlsx"],
        help="Financeiro > Relatórios > Relatório de Ocorrência > exportar Excel",
    )

    st.header("2. Data de referência")
    report_date = st.date_input("Considerar como 'hoje'", value=date.today())

    st.divider()
    st.caption(
        "Nenhum arquivo é salvo pelo aplicativo — o processamento acontece "
        "apenas em memória durante a geração do resumo."
    )

if "df" not in st.session_state:
    st.session_state.df = None
if "report_text" not in st.session_state:
    st.session_state.report_text = ""

if uploaded_file is not None:
    try:
        st.session_state.df = load_occurrences(uploaded_file.getvalue())
        st.success(f"Planilha carregada: {len(st.session_state.df)} ocorrência(s) encontrada(s).")
    except PlanilhaError as e:
        st.session_state.df = None
        st.error(str(e))
    except Exception as e:
        st.session_state.df = None
        st.error(f"Não foi possível ler a planilha: {e}")

st.subheader("3. Gerar resumo")
col1, col2 = st.columns(2)
with col1:
    gerar_inicio = st.button("🌅 Início de Expediente", use_container_width=True, disabled=st.session_state.df is None)
with col2:
    gerar_final = st.button("🌇 Final de Expediente", use_container_width=True, disabled=st.session_state.df is None)

if gerar_inicio:
    st.session_state.report_text = generate_report(st.session_state.df, "inicio", report_date)
elif gerar_final:
    st.session_state.report_text = generate_report(st.session_state.df, "final", report_date)

if st.session_state.report_text:
    st.subheader("4. Resumo gerado")
    st.caption("Clique no ícone de cópia no canto superior direito do bloco abaixo e cole direto no WhatsApp Web.")
    st.code(st.session_state.report_text, language=None, wrap_lines=True)
elif st.session_state.df is None:
    st.info("Envie a planilha exportada do SGP na barra lateral para começar.")
