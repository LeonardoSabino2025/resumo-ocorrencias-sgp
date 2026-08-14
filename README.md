# Resumo de Ocorrências por Técnico (SGP → WhatsApp)

Aplicativo Streamlit que lê o "Relatório de Ocorrência" exportado em Excel do
painel SGP e gera automaticamente o resumo em texto (pronto para colar no
WhatsApp), com sugestão de ordem de atendimento por técnico, sem depender de
nenhuma LLM — toda a extração e contagem é feita de forma determinística a
partir da planilha.

## Como usar

1. No SGP, exporte o **Relatório de Ocorrência** em Excel (botão verde no
   canto superior direito da tela).
2. Abra o app e envie o arquivo `.xlsx` na barra lateral.
3. Confira a data de referência (padrão: hoje).
4. Clique em **Início de Expediente** ou **Final de Expediente**:
   - **Início de Expediente**: lista apenas as ocorrências em aberto
     agendadas para a data de referência.
   - **Final de Expediente**: lista as ocorrências encerradas na data de
     referência; as que continuarem em aberto aparecem como
     "reagendada para amanhã".
5. Clique no ícone de copiar no bloco de texto gerado e cole no WhatsApp Web.

## Rodando localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Estrutura

- `report_logic.py` — leitura da planilha, normalização de tipos/bairros,
  agrupamento por técnico e geração do texto final.
- `app.py` — interface Streamlit.

## Observação sobre privacidade

Nenhuma planilha enviada é armazenada pelo aplicativo: o processamento
acontece inteiramente em memória durante a geração do resumo.
