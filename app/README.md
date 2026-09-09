# BONECO RUN Windows WebView

Este pacote substitui o painel WinForms antigo por um fluxo isolado:

- icone na bandeja do Windows;
- daemon local em `http://127.0.0.1:8791`;
- painel HTML/CSS/JS aberto pelo Edge/Chrome em modo app;
- ChatGPT Projeto e ChatGPT API Local separados;
- celular conectado pelo gateway `https://run.oboneco.com.br`;
- execucao local no Windows por PowerShell.

O Linux nao e usado como dependencia deste pacote. O unico ponto compartilhado e o gateway de pareamento/mobile.

## Instalar

Use o instalador `BONECO_RUN_WINDOWS_INSTALLER.exe` ou extraia o ZIP no
Windows e execute:

```powershell
.\setup.cmd
```

O instalador:

- copia o programa para `%LOCALAPPDATA%\BonecoRunWindows`;
- exige aceite dos termos de uso e protecao de dados;
- encerra processos antigos do proprio Boneco Windows dentro desta pasta;
- valida os modulos locais;
- cria atalho na area de trabalho;
- cria atalho de inicializacao;
- inicia o icone na bandeja.

Para instalacao automatizada, use:

```powershell
.\setup.cmd -AcceptTerms
```

## Uso

1. Clique no icone do BONECO RUN perto do relogio.
2. Abra o painel.
3. Em `Projeto`, selecione a pasta alvo.
4. Em `ChatGPT`, configure a URL do Projeto.
5. Em `Celular`, registre o PC, gere pareamento e inicie o agente.
6. Em `Comando`, envie o objetivo para a IA do projeto.

## Validacao esperada

```text
status=WINDOWS_WEBVIEW_INSTALLED
```

No navegador local:

```text
http://127.0.0.1:8791/health
```

deve retornar `ok=true`.
