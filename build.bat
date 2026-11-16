@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist traducoes_flashforge.po (
  echo Falta o arquivo traducoes_flashforge.po nesta pasta.
  goto erro
)
echo Instalando dependencias...
python -m pip install -r requirements.txt || goto erro
echo Gerando executavel...
python -m PyInstaller --onefile --console --clean --name TradutorFlashStudio --add-data "traducoes_flashforge.po;." tradutor_flashstudio.py || goto erro
if not exist dist\personalizado.po copy /Y personalizado.po dist\ >nul
echo.
echo Pronto: dist\TradutorFlashStudio.exe
pause
exit /b 0
:erro
echo Falhou. Confira se o Python 3.10+ esta instalado e no PATH.
pause
exit /b 1
