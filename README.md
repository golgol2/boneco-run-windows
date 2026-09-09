# BONECO RUN Windows

Fonte isolada do BONECO RUN Windows.

Este repositorio deve versionar o pacote Windows sem modificar o fluxo Linux.
O programa instalado usa:

- painel local: `http://127.0.0.1:8791`;
- API local: `http://127.0.0.1:8765/chat`;
- gateway mobile: `https://run.oboneco.com.br`;
- estado local: `%LOCALAPPDATA%\BonecoRunWindows\state`.

## Build

No Ubuntu com .NET SDK instalado:

```bash
./scripts/build-windows-installer.sh
```

Saidas:

- `dist/BONECO_RUN_WINDOWS_INSTALLER.exe`;
- `dist/BONECO_RUN_WINDOWS_WEBVIEW.zip`;
- `update/latest.json`.

## Atualizacao

O painel Windows consulta `update/latest.json` publicado no GitHub. Quando a
versao remota for maior que a versao local, o painel mostra uma sugestao de
atualizacao para baixar o novo instalador.

URL esperada do manifesto:

```text
https://raw.githubusercontent.com/golgol2/boneco-run-windows/main/update/latest.json
```

## Publicacao

1. Commitar a fonte.
2. Subir para `golgol2/boneco-run-windows`.
3. Criar uma release no GitHub com o arquivo `dist/BONECO_RUN_WINDOWS_INSTALLER.exe`.
4. Atualizar `update/latest.json` com a versao, URL de download e SHA256 do instalador.
