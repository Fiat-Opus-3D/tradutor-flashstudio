# Tradutor PT-BR para o Flash Studio — Fiat Opus 3D

Coloca o **Flash Studio** (fatiador da Flashforge) em português do Brasil: menus, Preparar,
Pré-visualizar, Dispositivo, configurações e mensagens.

## Baixar
[**TradutorFlashStudio.exe** — última versão](https://github.com/Fiat-Opus-3D/tradutor-flashstudio/releases/latest)

> O Windows pode mostrar "O Windows protegeu o computador", porque o programa não tem assinatura digital paga.
> Clique em **Mais informações → Executar assim mesmo**. O código está todo aqui no repositório, e o
> `.exe` é gerado pelo próprio GitHub a partir dele (aba *Actions*).

## Usar
1. Feche o Flash Studio.
2. Rode o `TradutorFlashStudio.exe` → opção **1**. Aceite o pedido de administrador.
3. Abra o Flash Studio. Ele já abre em português.

**O Flash Studio atualizou?** Rode o mesmo `.exe` de novo (opção 1). Ele baixa sozinho as traduções
mais recentes, então não precisa baixar o tradutor de novo. Se um dia precisar, ele mesmo avisa
e mostra o link.

**Frases em inglês depois de uma atualização?** São frases novas que ainda não foram traduzidas. Nada
quebra por isso. Rode o tradutor de novo daqui a uns dias.

**Desfazer tudo:** opção **2** (ou `TradutorFlashStudio.exe --restaurar`). Volta os arquivos e o idioma
exatamente como estavam.

## O que continua em inglês
A tela inicial (Recent, Models, Store...) é uma página web baixada dos servidores da Flashforge e não
tem português. Ela não usa o arquivo de tradução.

## Se o português não aparecer
Rode `TradutorFlashStudio.exe --substituir es` (ou outro idioma que você não usa): a tradução entra no
lugar dele e o programa já fica configurado nesse idioma.

## Correções próprias
Crie um `personalizado.po` ao lado do `.exe` com o texto em inglês **exatamente** como aparece no programa:
```
msgid "Prime tower"
msgstr "Torre de purga"
```
Rode a opção 1 de novo. As suas correções têm prioridade sobre todas as outras.

## Segurança (o que evita travar o Flash Studio)
- Toda frase é conferida: `%s`, `%d`, `%1%`, `{nome}`, tags e links precisam bater com o original. Se não
  baterem, a frase fica em inglês, nunca quebrada.
- O arquivo gerado é testado antes de instalar. Toda gravação é atômica e com backup.
- A configuração do Flash Studio tem checksum MD5: o programa recalcula e confere antes de gravar e, se
  não conseguir, não mexe.
- Backups, cache e log ficam em `%LOCALAPPDATA%\TradutorFlashStudio\`.

## De onde vêm as traduções
1. `personalizado.po` (suas correções)
2. `traducoes_flashforge.po`: frases exclusivas da Flashforge, traduzidas pela Fiat Opus 3D
3. Bambu Studio pt_BR (instalado ou do GitHub), pra usar as mesmas palavras do fatiador da Bambu
4. OrcaSlicer pt_BR (GitHub)

"OrcaSlicer" vira "Flash Studio" automaticamente nos textos.

## Linha de comando
- `--instalar` instala/atualiza sem perguntas
- `--restaurar` desfaz tudo
- `--substituir es` coloca a tradução também no lugar do idioma `es`
- `--pasta "C:\...\Flash Studio Desktop"` informa a pasta manualmente

## Rodar pelo código
Python 3.10+ → `pip install -r requirements.txt` → `python tradutor_flashstudio.py`.
Para gerar o `.exe`: `build.bat`.

Licença AGPL-3.0, a mesma do OrcaSlicer e do Bambu Studio, de onde vem parte das traduções.
Projeto independente, sem ligação com a Flashforge.
